"""I-GUIDE Tool Server — dual-transport (MCP + REST).

Exposes every ``@mcp_tool`` function over two transports from one process:

* ``/mcp/*``              — MCP protocol (for AI agents / pipeline)
* ``/api/tool/<name>``    — REST endpoint per tool (for developers)
* ``/api/tools``          — Catalog: list all tools with categories + schemas
* ``/api/health``         — Simple health check
* ``/api/docs``           — Swagger UI (auto-generated from tool signatures)
* ``/api/redoc``          — ReDoc alternative documentation UI

Both transports call the same underlying Python function, so any shared
in-memory state (``_dataframe_cache``, etc.) works seamlessly.

Run with: python server.py
Or: uvicorn server:app --host 0.0.0.0 --port 8000
"""

import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from dotenv import load_dotenv

# Load .env from root folder
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# Create MCP server instance using FastMCP
# mcp = FastMCP(
#     name="I-GUIDE Tools",
#     json_response=True,
# )
def _hosts_from_env(var: str, defaults: list[str]) -> list[str]:
    raw = os.getenv(var, "").strip()
    return [h.strip() for h in raw.split(",") if h.strip()] if raw else defaults

_default_allowed_hosts = ["127.0.0.1:*", "localhost:*", "mcp-server:*"]
_default_allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://mcp-server:*"]

mcp = FastMCP(
    name="I-GUIDE Tools",
    json_response=True,
    host="0.0.0.0",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_hosts_from_env("MCP_ALLOWED_HOSTS", _default_allowed_hosts),
        allowed_origins=_hosts_from_env("MCP_ALLOWED_ORIGINS", _default_allowed_origins),
    ),
)
# MCP transport — ASGI app speaking the MCP streamable HTTP protocol.
mcp_app = mcp.streamable_http_app()

# REST transport — separate FastAPI app that will receive one route per tool.
rest_app = FastAPI(
    title="I-GUIDE Tool API",
    description=(
        "Direct HTTP access to every @mcp_tool function. "
        "Intended for developers and ad-hoc scripting; agents should prefer /mcp."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Parent app that stitches both transports together.
app = FastAPI(title="I-GUIDE Tool Server", docs_url=None, redoc_url=None)
app.mount("/mcp", mcp_app)
app.mount("/api", rest_app)

# Registry to store tool functions
_tool_registry: dict[str, Callable] = {}

# --- MCP tool categories ---
# Capability-based taxonomy — describes WHAT SHAPE of work a tool does,
# independent of domain (climate, biomass, health, urban, etc).
# Any new tool from any domain should fit into exactly one of these.
# Intent → category mapping lives in agent_runtime.tool_policy.
# Remote/external MCP tools are auto-classified into this same taxonomy
# by rag_pipeline.langchain_mcp_tools at import time.
MCP_TOOL_CATEGORIES = frozenset({
    "retrieval_internal",  # I-GUIDE's own indices (keyword, semantic, Neo4j)
    "retrieval_external",  # federated third-party catalogs (STAC, OGC, web)
    "data_loading",        # fetches a dataset into working memory
    "computation",         # analyzes/transforms loaded data (stats, spatial joins)
    "generation",          # produces new artifacts (maps, images, code, notebooks)
    "io",                  # file read/write
})


# --- MCP tool decorator (marks functions for MCP registration) ---
def mcp_tool(
    _func=None,
    *,
    category: str | None = None,
    summary: str | None = None,
    description: str | None = None,
    tool_description: str | None = None,
    mcp_description: str | None = None,
):
    """Decorator to mark functions as MCP tools.

    Args:
        category: Routing category read by agent_runtime.tool_policy at runtime
            to decide which tools are available for a given query intent.
            Must be one of MCP_TOOL_CATEGORIES. Optional for now; Phase 3
            validation will make it required.
        summary: Short human summary.
        description / tool_description / mcp_description: Tool description
            variants surfaced to the LLM (kept for backward compatibility).
    """
    if category is not None and category not in MCP_TOOL_CATEGORIES:
        raise ValueError(
            f"Unknown mcp_tool category {category!r}. "
            f"Valid categories: {sorted(MCP_TOOL_CATEGORIES)}"
        )

    def decorator(func):
        func._is_mcp_tool = True
        if category:
            func._mcp_category = category
        if summary:
            func._mcp_summary = summary
            func._tool_summary = summary
        if description:
            func._tool_description = description
            func._mcp_description = description
        if tool_description:
            func._tool_description = tool_description
        if mcp_description:
            func._mcp_description = mcp_description
        return func

    if callable(_func):
        return decorator(_func)
    return decorator


def scan_and_register_tools():
    """Scan tools/ directory and register all @mcp_tool decorated functions.

    Uses the normal Python package import system (``importlib.import_module``)
    so cross-tool imports like ``from tools.data_tools import _dataframe_cache``
    resolve to the same module instance the decorator-scan loaded.  Bypassing
    this with ``spec_from_file_location`` created a second instance of every
    module and broke shared state between tools.
    """
    tools_dir = Path(__file__).parent / "tools"

    # Add MCP_server to path so:
    #   - tools can do `from server import mcp_tool`
    #   - we can do `importlib.import_module("tools.<name>")`
    base_dir = str(Path(__file__).parent)
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

    # Load the package itself first so subsequent submodule imports bind
    # as attributes on it.
    import importlib
    importlib.import_module("tools")

    tool_count = 0

    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.name.startswith("__"):
            continue

        module_name = f"tools.{py_file.stem}"
        try:
            module = importlib.import_module(module_name)

            # Find and register decorated functions
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if callable(attr) and getattr(attr, "_is_mcp_tool", False):
                    register_tool_with_mcp(attr)
                    tool_count += 1

        except Exception as e:
            print(f"Warning: Failed to load {py_file.name}: {e}")
            continue

    print(f"✅ Registered {tool_count} MCP tools from {tools_dir}")
    return tool_count


def _has_upload_file_param(sig: inspect.Signature) -> bool:
    """Detect whether the tool expects file uploads (multipart body)."""
    try:
        from fastapi import UploadFile
    except Exception:
        return False
    for param in sig.parameters.values():
        annotation = param.annotation
        if annotation is UploadFile:
            return True
        args = getattr(annotation, "__args__", ())
        if args and any(arg is UploadFile for arg in args):
            return True
    return False


def _json_safe(value: Any) -> Any:
    """Coerce arbitrary tool return values into JSON-serialisable form."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    # Pandas / numpy / GeoPandas objects expose to_dict or __dict__
    if hasattr(value, "to_dict"):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    return str(value)


def _register_rest_endpoint(func: Callable, *, description: str) -> None:
    """Mount *func* as ``POST /tool/<name>`` on the REST sub-app.

    Dispatches based on whether the function takes ``UploadFile`` params:
    - yes → accept multipart form-data
    - no  → accept JSON body

    The tool function itself is unchanged; this is purely a transport adapter.
    """
    tool_name = func.__name__
    sig = inspect.signature(func)
    summary = getattr(func, "_mcp_summary", None) or description.split("\n", 1)[0].strip()
    category = getattr(func, "_mcp_category", None)
    tags = [category] if category else ["tool"]

    async def json_endpoint(request: Request) -> JSONResponse:
        try:
            raw = await request.body()
            body = await request.json() if raw else {}
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")
        try:
            result = func(**body)
        except TypeError as exc:
            raise HTTPException(status_code=400, detail=f"Bad arguments: {exc}") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Tool execution error: {type(exc).__name__}: {exc}",
            ) from exc
        return JSONResponse({"tool": tool_name, "result": _json_safe(result)})

    async def multipart_endpoint(request: Request) -> JSONResponse:
        form = await request.form()
        kwargs: Dict[str, Any] = {}
        for key, value in form.multi_items():
            kwargs[key] = value
        try:
            result = func(**kwargs)
        except TypeError as exc:
            raise HTTPException(status_code=400, detail=f"Bad arguments: {exc}") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Tool execution error: {type(exc).__name__}: {exc}",
            ) from exc
        return JSONResponse({"tool": tool_name, "result": _json_safe(result)})

    handler = multipart_endpoint if _has_upload_file_param(sig) else json_endpoint
    rest_app.add_api_route(
        path=f"/tool/{tool_name}",
        endpoint=handler,
        methods=["POST"],
        name=tool_name,
        summary=summary,
        description=description,
        tags=tags,
    )


def register_tool_with_mcp(func: Callable):
    """Register a tool function with BOTH transports.

    1. Store the tool in the registry
    2. Extract metadata from the function
    3. Register with FastMCP via ``@mcp.tool()`` — agents / pipeline reach here
    4. Register a FastAPI REST route at ``/api/tool/<name>`` — developers reach here
    5. Preserve the exact function signature so MCP schema generation and
       OpenAPI docs both reflect the real parameters.
    """
    tool_name = func.__name__
    _tool_registry[tool_name] = func

    # Extract description from decorator metadata or docstring
    description = (
        getattr(func, "_mcp_description", None)
        or getattr(func, "_tool_description", None)
        or func.__doc__
        or f"Tool: {tool_name}"
    ).strip()

    # Get function signature for schema generation
    sig = inspect.signature(func)
    annotations = func.__annotations__.copy() if hasattr(func, '__annotations__') else {}

    # Create wrapper function for FastMCP registration
    def tool_wrapper(**kwargs) -> Any:
        """MCP tool wrapper that calls the original function."""
        try:
            return func(**kwargs)
        except Exception as e:
            error_msg = f"Tool execution error: {type(e).__name__}: {str(e)}"
            print(f"❌ {tool_name} failed: {error_msg}")
            raise

    # Set metadata BEFORE decorating (FastMCP uses function name for registration)
    tool_wrapper.__name__ = tool_name
    tool_wrapper.__doc__ = description
    tool_wrapper.__annotations__ = annotations
    tool_wrapper.__signature__ = sig

    # Register with FastMCP (agent / pipeline transport)
    mcp.tool()(tool_wrapper)

    # Register with the REST sub-app (developer transport)
    _register_rest_endpoint(func, description=description)

    print(f"  📌 {tool_name}({', '.join(sig.parameters.keys())})")


# ---------------------------------------------------------------------------
# REST-only helper endpoints (catalog + health)
# ---------------------------------------------------------------------------

@rest_app.get("/health", tags=["meta"], summary="Health check")
def api_health() -> Dict[str, Any]:
    """Return a simple OK for monitoring."""
    return {"status": "healthy", "tool_count": len(_tool_registry)}


@rest_app.get("/tools", tags=["meta"], summary="List all registered tools")
def api_list_tools() -> Dict[str, Any]:
    """Return every registered tool with its category, description, and parameters."""
    tools: List[Dict[str, Any]] = []
    for name, func in sorted(_tool_registry.items()):
        sig = inspect.signature(func)
        parameters = []
        for param_name, param in sig.parameters.items():
            annotation = param.annotation
            if annotation is inspect.Parameter.empty:
                type_name = "Any"
            else:
                type_name = getattr(annotation, "__name__", str(annotation))
            parameters.append({
                "name": param_name,
                "type": type_name,
                "required": param.default is inspect.Parameter.empty,
                "default": None if param.default is inspect.Parameter.empty else repr(param.default),
            })
        tools.append({
            "name": name,
            "category": getattr(func, "_mcp_category", None),
            "description": (
                getattr(func, "_mcp_description", None)
                or getattr(func, "_tool_description", None)
                or func.__doc__
                or ""
            ).strip(),
            "parameters": parameters,
            "accepts_file_upload": _has_upload_file_param(sig),
        })
    return {"count": len(tools), "tools": tools}


# Auto-scan and register tools at module load
print("\n🔍 Scanning for MCP tools...")
scan_and_register_tools()
print(f"✅ Tool server ready with {len(_tool_registry)} tools\n")

# Run server when executed directly
if __name__ == "__main__":
    print("🚀 Starting I-GUIDE Tool Server (dual-transport)")
    print("   MCP protocol:  http://localhost:8000/mcp")
    print("   REST API:      http://localhost:8000/api")
    print("   Tool catalog:  http://localhost:8000/api/tools")
    print("   Swagger UI:    http://localhost:8000/api/docs")
    print("   ReDoc:         http://localhost:8000/api/redoc")
    print("   Health:        http://localhost:8000/api/health")
    print()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
