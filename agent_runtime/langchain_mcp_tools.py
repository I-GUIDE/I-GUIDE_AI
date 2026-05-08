from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type

import requests
from pydantic import create_model

from agent_runtime.streaming_trace import emit_trace_event

logger = logging.getLogger(__name__)

DEFAULT_MCP_MODULES = (
    "search_tools",
    "data_tools",
    "spatial_analysis_tools",
    "image_tools",
    "notebook_workflow_tools",
    "generated_notebook_tools",
)
DEFAULT_REMOTE_MCP_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp/")


# ---------------------------------------------------------------------------
# Tool list cache
#
# make_langchain_mcp_tools() builds StructuredTool wrappers for every MCP
# tool.  For remote tools this hits the MCP server over the network once
# per call.  Within a single agent query the function is called 2-6 times.
#
# The cache stores the final tool list keyed by (remote_url, modules)
# with a TTL controlled by MCP_CACHE_TTL_SECONDS (default 60s, 0 disables).
# ---------------------------------------------------------------------------

_CacheKey = Tuple[str, Tuple[str, ...]]
_mcp_tool_cache: Dict[_CacheKey, Tuple[float, List[Any]]] = {}
_mcp_cache_lock = Lock()
_mcp_cache_stats = {"hits": 0, "misses": 0, "stores": 0}


def _mcp_cache_ttl_seconds() -> float:
    """TTL for the MCP tool cache, in seconds.  0 disables the cache."""
    raw = os.getenv("MCP_CACHE_TTL_SECONDS", "60")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 60.0


def _mcp_cache_key(remote_url: str, include_modules: Optional[List[str]]) -> _CacheKey:
    modules: Tuple[str, ...]
    if include_modules is None:
        modules = tuple(DEFAULT_MCP_MODULES)
    else:
        modules = tuple(include_modules)
    return (remote_url, modules)


def clear_mcp_cache() -> None:
    """Drop every cached MCP tool list.  Useful for tests and dev reloads."""
    with _mcp_cache_lock:
        _mcp_tool_cache.clear()
        _mcp_cache_stats["hits"] = 0
        _mcp_cache_stats["misses"] = 0
        _mcp_cache_stats["stores"] = 0
    logger.info("MCP tool cache cleared")


def get_mcp_cache_stats() -> Dict[str, Any]:
    """Return a snapshot of cache state for visibility / tests."""
    with _mcp_cache_lock:
        return {
            "entries": len(_mcp_tool_cache),
            "hits": _mcp_cache_stats["hits"],
            "misses": _mcp_cache_stats["misses"],
            "stores": _mcp_cache_stats["stores"],
            "ttl_seconds": _mcp_cache_ttl_seconds(),
            "keys": [
                {"url": url, "modules": list(modules)}
                for (url, modules) in _mcp_tool_cache.keys()
            ],
        }


def _mcp_tool(
    _func=None,
    *,
    category: str | None = None,
    summary: str | None = None,
    description: str | None = None,
    tool_description: str | None = None,
    mcp_description: str | None = None,
):
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


def _ensure_mcp_import_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    mcp_dir = repo_root / "MCP_server"
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    return mcp_dir


def _ensure_server_stub() -> None:
    existing = sys.modules.get("server")
    if existing is not None and hasattr(existing, "mcp_tool"):
        return
    stub = ModuleType("server")
    stub.mcp_tool = _mcp_tool  # type: ignore[attr-defined]
    sys.modules["server"] = stub


def _ensure_fastapi_stub() -> None:
    existing = sys.modules.get("fastapi")
    if existing is not None:
        return
    try:
        importlib.import_module("fastapi")
        return
    except Exception:
        pass

    stub = ModuleType("fastapi")

    class UploadFile:  # pragma: no cover - simple import shim
        pass

    def File(default: Any = None):  # pragma: no cover - simple import shim
        return default

    stub.UploadFile = UploadFile  # type: ignore[attr-defined]
    stub.File = File  # type: ignore[attr-defined]
    sys.modules["fastapi"] = stub


def _iter_mcp_functions(module: Any) -> Iterable[Callable[..., Any]]:
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if callable(attr) and getattr(attr, "_is_mcp_tool", False):
            yield attr


def _tool_description(func: Callable[..., Any]) -> str:
    return (
        getattr(func, "_mcp_description", None)
        or getattr(func, "_tool_description", None)
        or getattr(func, "__doc__", None)
        or f"MCP tool: {func.__name__}"
    ).strip()


def _tool_metadata(func: Callable[..., Any]) -> Dict[str, Any]:
    """Return StructuredTool metadata for a decorated MCP function.

    Currently carries the capability category set by @mcp_tool(category=...).
    Read downstream by agent_runtime.tool_policy to route tools by intent.
    """
    metadata: Dict[str, Any] = {}
    category = getattr(func, "_mcp_category", None)
    if category:
        metadata["category"] = category
    return metadata


def _remote_mcp_url() -> str:
    url = (os.getenv("MCP_SERVER_URL") or DEFAULT_REMOTE_MCP_URL).strip()
    return f"{url.rstrip('/')}/" if url.rstrip("/").endswith("/mcp") else url


def _json_schema_type(schema: Dict[str, Any]) -> Type[Any]:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null = [item for item in schema_type if item != "null"]
        schema_type = non_null[0] if non_null else None
    if schema_type == "string":
        return str
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        return list[Any]
    if schema_type == "object":
        return dict[str, Any]
    return Any


def _args_schema_from_remote_tool(tool: Any) -> Type[Any]:
    input_schema = getattr(tool, "inputSchema", None) or {}
    properties = input_schema.get("properties") or {}
    required = set(input_schema.get("required") or [])
    fields: Dict[str, Tuple[Type[Any], Any]] = {}

    for name, spec in properties.items():
        field_type = _json_schema_type(spec if isinstance(spec, dict) else {})
        default = ... if name in required else None
        fields[str(name)] = (field_type, default)

    model_name = f"RemoteMCPTool_{getattr(tool, 'name', 'Tool')}"
    if not fields:
        fields["payload"] = (Optional[dict[str, Any]], None)
    return create_model(model_name, **fields)


def _serialize_remote_tool_result(result: Any) -> str:
    if hasattr(result, "model_dump"):
        payload = result.model_dump()
    elif isinstance(result, dict):
        payload = result
    else:
        return str(result)

    content = payload.get("content") or []
    text_parts: List[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text" and item.get("text"):
                text_parts.append(str(item["text"]))
            elif item.get("type") == "resource_link":
                text_parts.append(str(item.get("uri") or item.get("name") or "resource_link"))
            else:
                text_parts.append(str(item))
        elif hasattr(item, "text"):
            text_parts.append(str(getattr(item, "text")))
        else:
            text_parts.append(str(item))

    serialized: Dict[str, Any] = {
        "is_error": bool(payload.get("isError", False)),
        "structured_content": payload.get("structuredContent"),
        "content": content,
        "text": "\n".join(part for part in text_parts if part).strip(),
    }
    return json.dumps(serialized, ensure_ascii=True, default=str)


async def _remote_mcp_list_tools_async(url: str) -> List[Any]:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return list(result.tools or [])


async def _remote_mcp_call_tool_async(url: str, tool_name: str, arguments: Dict[str, Any]) -> str:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    payload = dict(arguments or {})
    if "payload" in payload and isinstance(payload["payload"], dict) and len(payload) == 1:
        payload = payload["payload"]
    emit_trace_event(
        "mcp_call_start",
        {
            "kind": "mcp_call_start",
            "label": "MCP call started",
            "tool_name": f"mcp_{tool_name}",
            "mcp_tool_name": tool_name,
            "mcp_url": url,
            "args": payload,
            "message": f"Calling MCP tool mcp_{tool_name}",
        },
    )
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            try:
                await session.initialize()
                result = await session.call_tool(tool_name, payload)
                serialized = _serialize_remote_tool_result(result)
                emit_trace_event(
                    "mcp_call_end",
                    {
                        "kind": "mcp_call_end",
                        "label": "MCP call completed",
                        "tool_name": f"mcp_{tool_name}",
                        "mcp_tool_name": tool_name,
                        "mcp_url": url,
                        "content": serialized,
                        "message": f"MCP tool mcp_{tool_name} completed",
                    },
                )
                return serialized
            except Exception as exc:
                emit_trace_event(
                    "mcp_call_error",
                    {
                        "kind": "mcp_call_error",
                        "label": "MCP call failed",
                        "tool_name": f"mcp_{tool_name}",
                        "mcp_tool_name": tool_name,
                        "mcp_url": url,
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                )
                raise


def _make_remote_mcp_tools(url: str) -> List[Any]:
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    try:
        remote_tools = asyncio.run(_remote_mcp_list_tools_async(url))
    except Exception as exc:
        logger.warning("Remote MCP unavailable at %s: %s", url, exc)
        return []

    tools: List[Any] = []
    for remote_tool in remote_tools:
        remote_name = getattr(remote_tool, "name", "")
        if not remote_name:
            continue

        args_schema = _args_schema_from_remote_tool(remote_tool)
        tool_name = f"mcp_{remote_name}"
        description = (getattr(remote_tool, "description", None) or f"Remote MCP tool: {remote_name}").strip()

        async def remote_tool_runner_async(
            _tool_name: str = remote_name,
            _url: str = url,
            **kwargs: Any,
        ) -> str:
            return await _remote_mcp_call_tool_async(_url, _tool_name, kwargs)

        def remote_tool_runner_sync(
            _tool_name: str = remote_name,
            _url: str = url,
            **kwargs: Any,
        ) -> str:
            return asyncio.run(_remote_mcp_call_tool_async(_url, _tool_name, kwargs))

        remote_tool_runner_async.__name__ = tool_name
        remote_tool_runner_async.__doc__ = description
        remote_tool_runner_sync.__name__ = tool_name
        remote_tool_runner_sync.__doc__ = description

        try:
            tools.append(
                StructuredTool.from_function(
                    func=remote_tool_runner_sync,
                    coroutine=remote_tool_runner_async,
                    name=tool_name,
                    description=description,
                    args_schema=args_schema,
                    infer_schema=False,
                )
            )
        except Exception as exc:
            logger.warning("Skipping remote MCP tool %s: %s", tool_name, exc)

    if tools:
        logger.info("Loaded %s remote MCP tools from %s", len(tools), url)
    return tools


def _make_image_tools(module: Any) -> List[Callable[..., Any]]:
    api_url = getattr(
        module,
        "API_URL",
        os.getenv("VISION_API_URL"),
    )
    api_key = getattr(module, "API_KEY", os.getenv("VISION_API_KEY"))
    model_name = getattr(
        module,
        "MODEL_NAME",
        os.getenv("VISION_API_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct"),
    )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def describe_image_b64(image_base64: str, prompt_text: str = "Describe what is in this image.") -> str:
        """
        Describe an image from base64-encoded bytes using the configured vision model.
        """
        body = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    ],
                }
            ],
            "stream": False,
        }
        response = requests.post(api_url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        data = response.json() or {}
        choices = data.get("choices") or []
        if not choices:
            return "No description generated."
        return (choices[0].get("message") or {}).get("content") or "No description generated."

    def describe_map_b64(
        image_base64: str,
        prompt_text: str = (
            "Describe the given map. Focus on which area the map depicts, "
            "what problem the map describes, and what information the map provides. "
            "Format the response in markdown format."
        ),
    ) -> str:
        """
        Describe a map image from base64-encoded bytes with area/problem/information focus.
        """
        body = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    ],
                }
            ],
            "stream": False,
        }
        response = requests.post(api_url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        data = response.json() or {}
        choices = data.get("choices") or []
        if not choices:
            return "No description generated."
        return (choices[0].get("message") or {}).get("content") or "No description generated."

    describe_image_b64.__name__ = "describe_image_b64"
    describe_map_b64.__name__ = "describe_map_b64"
    describe_image_b64._mcp_category = "generation"
    describe_map_b64._mcp_category = "generation"
    return [describe_image_b64, describe_map_b64]


def make_langchain_mcp_tools(
    *,
    include_modules: Optional[List[str]] = None,
) -> List[Any]:
    """
    Build LangChain tools from selected MCP tool modules under MCP_server/tools.

    Results are cached for ``MCP_CACHE_TTL_SECONDS`` (default 60s) to avoid
    re-fetching the remote MCP tool list on every call.  Set the env var to
    ``0`` to disable caching.  Use :func:`clear_mcp_cache` in tests.
    """
    remote_url = _remote_mcp_url()
    cache_key = _mcp_cache_key(remote_url, include_modules)
    ttl = _mcp_cache_ttl_seconds()

    if ttl > 0:
        with _mcp_cache_lock:
            cached = _mcp_tool_cache.get(cache_key)
            if cached is not None:
                stored_at, tools = cached
                if (time.monotonic() - stored_at) < ttl:
                    _mcp_cache_stats["hits"] += 1
                    logger.debug("MCP tool cache HIT for %s", cache_key)
                    return list(tools)
                # Expired — fall through and rebuild.
                _mcp_tool_cache.pop(cache_key, None)
            _mcp_cache_stats["misses"] += 1
    logger.debug("MCP tool cache MISS for %s (ttl=%.1fs)", cache_key, ttl)

    remote_tools = _make_remote_mcp_tools(remote_url)
    if remote_tools:
        if ttl > 0:
            with _mcp_cache_lock:
                _mcp_tool_cache[cache_key] = (time.monotonic(), list(remote_tools))
                _mcp_cache_stats["stores"] += 1
        return remote_tools

    # Local fallback — only needs StructuredTool when we actually build local tools.
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    _ensure_mcp_import_path()
    _ensure_server_stub()

    modules = include_modules or list(DEFAULT_MCP_MODULES)
    tools: List[Any] = []

    for module_name in modules:
        if module_name == "image_tools":
            _ensure_fastapi_stub()
        try:
            module = importlib.import_module(f"tools.{module_name}")
        except Exception as exc:
            logger.warning("Skipping MCP module %s: %s", module_name, exc)
            continue

        if module_name == "image_tools":
            candidates = _make_image_tools(module)
        else:
            candidates = list(_iter_mcp_functions(module))

        for func in candidates:
            tool_name = f"mcp_{func.__name__}"
            try:
                tools.append(
                    StructuredTool.from_function(
                        func=func,
                        name=tool_name,
                        description=_tool_description(func),
                        metadata=_tool_metadata(func),
                    )
                )
            except Exception as exc:
                logger.warning("Skipping MCP tool %s from %s: %s", tool_name, module_name, exc)
                continue

    if tools:
        logger.info("Loaded %s MCP tools via local import fallback.", len(tools))
    else:
        logger.warning("No remote MCP tools available and no local MCP tools loaded.")

    if ttl > 0 and tools:
        with _mcp_cache_lock:
            _mcp_tool_cache[cache_key] = (time.monotonic(), list(tools))
            _mcp_cache_stats["stores"] += 1
    return tools


__all__ = [
    "make_langchain_mcp_tools",
    "DEFAULT_MCP_MODULES",
    "clear_mcp_cache",
    "get_mcp_cache_stats",
]
