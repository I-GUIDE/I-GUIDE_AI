"""I-GUIDE Unified Server - MCP Protocol + REST API

Exposes geospatial analysis tools via dual transport:
1. MCP Protocol (/mcp) - for AI assistants (Claude Desktop, LangChain)
2. REST API (/api) - for developers with Swagger UI

Run with: python server.py
Access Swagger: http://localhost:8000/api/docs
Access MCP: http://localhost:8000/mcp
"""

import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Callable, Union, Dict

from fastapi import FastAPI, UploadFile, File as FastAPIFile, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
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
mcp = FastMCP(
    name="I-GUIDE Tools",
    json_response=True,
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["149.165.147.219:*"],
        allowed_origins=["http://149.165.147.219:*"],
    ),
)
mcp_app = mcp.streamable_http_app()

# Create REST API for developer access (Swagger UI)
rest_app = FastAPI(
    title="I-GUIDE Tools REST API",
    description="Developer-friendly REST endpoints for all MCP tools. "
                "Each tool decorated with @mcp_tool is automatically exposed here.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS for web access
rest_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registry to store tool functions
_tool_registry: dict[str, Callable] = {}

# --- Tool decorator (marks functions for dual transport exposure) ---
def mcp_tool(
    _func=None,
    *,
    summary: str | None = None,
    description: str | None = None,
    tool_description: str | None = None,
    mcp_description: str | None = None,
):
    """Decorator to expose functions via MCP protocol AND REST API.
    
    This decorator registers the function with:
    - MCP protocol (for AI assistants like Claude, LangChain)
    - REST API with Swagger UI (for developers and web apps)
    
    The name 'mcp_tool' is historical - it now registers with both transports.
    
    Args:
        summary: Short summary for the tool
        description: Detailed description
        tool_description: Description for REST API
        mcp_description: Description for MCP protocol
    """
    def decorator(func):
        func._is_mcp_tool = True
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

# Alias for clarity in new code
tool = mcp_tool


def scan_and_register_tools():
    """Scan tools/ directory and register all @mcp_tool decorated functions.
    
    Each tool is registered with:
    - MCP protocol for AI assistants
    - REST API with Swagger UI for developers
    """
    tools_dir = Path(__file__).parent / "tools"
    
    # Add MCP_server to path so tools can import server module
    base_dir = str(Path(__file__).parent)
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    
    tool_count = 0
    
    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.name.startswith("__"):
            continue
            
        try:
            # Import module
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            if spec is None or spec.loader is None:
                continue
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find and register decorated functions
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if callable(attr) and getattr(attr, "_is_mcp_tool", False):
                    register_tool(attr)
                    tool_count += 1
                    
        except Exception as e:
            print(f"Warning: Failed to load {py_file.name}: {e}")
            continue
    
    print(f"✅ Registered {tool_count} tools (MCP + REST) from {tools_dir}")
    return tool_count


def register_tool(func: Callable):
    """Register a tool function with BOTH MCP protocol AND REST API.
    
    This function:
    1. Stores the tool in the registry
    2. Extracts metadata from the function
    3. Registers with MCP protocol (for AI assistants)
    4. Registers with REST API (for developers/Swagger UI)
    5. Auto-detects file upload parameters and creates appropriate endpoints
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
    
    # ========== 1. Register with MCP Protocol ==========
    def mcp_wrapper(**kwargs) -> Any:
        """MCP tool wrapper that calls the original function."""
        try:
            result = func(**kwargs)
            return result
        except Exception as e:
            error_msg = f"Tool execution error: {type(e).__name__}: {str(e)}"
            print(f"❌ {tool_name} failed: {error_msg}")
            raise
    
    # Set metadata for MCP
    mcp_wrapper.__name__ = tool_name
    mcp_wrapper.__doc__ = description
    mcp_wrapper.__annotations__ = annotations
    mcp_wrapper.__signature__ = sig
    
    # Register with FastMCP
    mcp.tool()(mcp_wrapper)
    
    # ========== 2. Register with REST API ==========
    _register_rest_endpoint(func, tool_name, description, sig)
    
    print(f"  📌 {tool_name}({', '.join(sig.parameters.keys())}) → MCP + REST")


def _register_rest_endpoint(func: Callable, tool_name: str, description: str, sig: inspect.Signature):
    """Register a REST API endpoint for the tool.
    
    Auto-detects if the tool needs file upload support and creates
    the appropriate endpoint (multipart/form-data or JSON).
    """
    # Check if function accepts file uploads
    has_file_param = False
    file_param_name = None
    other_params = {}
    
    for param_name, param in sig.parameters.items():
        param_type = param.annotation
        
        # Check if it's UploadFile or Union containing UploadFile or Dict
        is_file_param = False
        if param_type == UploadFile:
            is_file_param = True
        elif hasattr(param_type, '__origin__'):
            if param_type.__origin__ == Union:
                # Check if Union contains UploadFile or Dict (for file handling)
                args = getattr(param_type, '__args__', ())
                if UploadFile in args or Dict in args or dict in args:
                    is_file_param = True
        
        if is_file_param:
            has_file_param = True
            file_param_name = param_name
        else:
            other_params[param_name] = param
    
    if has_file_param:
        # Create multipart file upload endpoint
        @rest_app.post(
            f"/tool/{tool_name}",
            summary=getattr(func, "_tool_summary", tool_name),
            description=description,
            tags=["Tools with File Upload"],
            response_model=None
        )
        async def rest_file_endpoint(
            file: UploadFile = FastAPIFile(..., description="File to process (image, document, etc.)"),
            prompt_text: str = Form(default="", description="Optional prompt or parameters for the tool")
        ):
            """REST endpoint for file upload tools."""
            try:
                # Build kwargs based on function signature
                kwargs = {}
                if file_param_name:
                    kwargs[file_param_name] = file
                
                # Add other parameters if they exist in signature
                for param_name in other_params:
                    if param_name == "prompt_text":
                        kwargs["prompt_text"] = prompt_text
                
                result = func(**kwargs)
                return {
                    "success": True,
                    "tool": tool_name,
                    "result": result
                }
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Tool execution failed: {str(e)}"
                )
        
        # Fix function name for OpenAPI (prevents conflicts)
        rest_file_endpoint.__name__ = f"rest_{tool_name}_file"
        
    else:
        # Create JSON endpoint for regular tools
        @rest_app.post(
            f"/tool/{tool_name}",
            summary=getattr(func, "_tool_summary", tool_name),
            description=description,
            tags=["Tools"],
            response_model=None
        )
        async def rest_json_endpoint(arguments: Dict[str, Any]):
            """REST endpoint for JSON-based tools."""
            try:
                result = func(**arguments)
                return {
                    "success": True,
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result
                }
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Tool execution failed: {str(e)}"
                )
        
        # Fix function name for OpenAPI
        rest_json_endpoint.__name__ = f"rest_{tool_name}_json"


# ========== REST API Info Endpoints ==========
@rest_app.get("/", tags=["Info"])
def rest_api_info():
    """Get information about the REST API and available tools."""
    return {
        "title": "I-GUIDE Tools REST API",
        "description": "Developer-friendly REST endpoints for all MCP tools",
        "tools_count": len(_tool_registry),
        "tools": list(_tool_registry.keys()),
        "endpoints": {
            "swagger_ui": "/docs",
            "redoc": "/redoc",
            "openapi_schema": "/openapi.json",
            "list_tools": "/tools",
            "tool_endpoint_pattern": "/tool/{tool_name}"
        }
    }


@rest_app.get("/tools", tags=["Info"])
def list_tools():
    """List all available tools with their signatures and descriptions."""
    tools_info = []
    for name, func in _tool_registry.items():
        sig = inspect.signature(func)
        doc = (func.__doc__ or "").strip()
        first_line = doc.split('\n')[0] if doc else f"Tool: {name}"
        
        tools_info.append({
            "name": name,
            "description": first_line,
            "endpoint": f"/tool/{name}",
            "parameters": {
                param_name: {
                    "type": str(param.annotation) if param.annotation != inspect.Parameter.empty else "Any",
                    "default": str(param.default) if param.default != inspect.Parameter.empty else None,
                    "required": param.default == inspect.Parameter.empty
                }
                for param_name, param in sig.parameters.items()
            }
        })
    
    return {
        "tools_count": len(tools_info),
        "tools": tools_info
    }


@rest_app.get("/health", tags=["Info"])
def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "tools_loaded": len(_tool_registry),
        "transports": ["mcp", "rest"]
    }


# Auto-scan and register tools at module load
print("\n🔍 Scanning for tools...")
scan_and_register_tools()
print(f"✅ Unified Server ready with {len(_tool_registry)} tools\n")

# ========== Create Unified Parent App ==========
parent_app = FastAPI(
    title="I-GUIDE Unified Server",
    description="Dual transport server: MCP Protocol for AI assistants + REST API for developers",
    version="1.0.0"
)

# Mount MCP protocol at /mcp
parent_app.mount("/mcp", mcp_app)

# Mount REST API at /api
parent_app.mount("/api", rest_app)


@parent_app.get("/")
def root():
    """Root endpoint with server information."""
    return {
        "name": "I-GUIDE Unified Server",
        "version": "1.0.0",
        "description": "Geospatial analysis tools via MCP protocol and REST API",
        "tools_count": len(_tool_registry),
        "tools": list(_tool_registry.keys()),
        "endpoints": {
            "mcp_protocol": "/mcp (for AI assistants like Claude, LangChain)",
            "rest_api": "/api (for developers and web apps)",
            "swagger_ui": "/api/docs",
            "redoc": "/api/redoc",
            "health": "/api/health"
        },
        "usage": {
            "mcp": "Connect AI assistants to /mcp endpoint",
            "rest": "POST to /api/tool/{tool_name} with JSON or multipart data",
            "docs": "Visit /api/docs for interactive API documentation"
        }
    }


# Run server when executed directly
if __name__ == "__main__":
    print("=" * 70)
    print("🚀 Starting I-GUIDE Unified Server")
    print("=" * 70)
    print()
    print(f"📊 Loaded {len(_tool_registry)} tools:")
    for tool_name in sorted(_tool_registry.keys()):
        print(f"   • {tool_name}")
    print()
    print("🌐 Server URLs:")
    print("   Root:       http://localhost:8000")
    print("   MCP:        http://localhost:8000/mcp")
    print("   REST API:   http://localhost:8000/api")
    print("   Swagger UI: http://localhost:8000/api/docs")
    print("   ReDoc:      http://localhost:8000/api/redoc")
    print()
    print("📝 Usage:")
    print("   • AI Assistants: Connect to /mcp endpoint")
    print("   • Developers: Use /api/tool/{name} endpoints")
    print("   • Testing: Open /api/docs in browser")
    print()
    print("🛑 Press Ctrl+C to stop")
    print("=" * 70)
    print()
    
    import uvicorn
    uvicorn.run(parent_app, host="0.0.0.0", port=8000)

