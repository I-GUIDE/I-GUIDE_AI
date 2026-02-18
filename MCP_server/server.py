"""I-GUIDE MCP Server using official Model Context Protocol SDK.

This server exposes geospatial analysis tools via the MCP protocol,
making them available to AI assistants like Claude Desktop, LangChain,
and other MCP-compatible clients.

Run with: python server.py
Or: uvicorn server:app --host 0.0.0.0 --port 8000
"""

import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Callable

from mcp.server import FastMCP
from dotenv import load_dotenv

# Load .env from root folder
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# Create MCP server instance using FastMCP
mcp = FastMCP(
    name="I-GUIDE Tools",
    json_response=True,
)

# Registry to store tool functions
_tool_registry: dict[str, Callable] = {}

# --- MCP tool decorator (marks functions for MCP registration) ---
def mcp_tool(
    _func=None,
    *,
    summary: str | None = None,
    description: str | None = None,
    tool_description: str | None = None,
    mcp_description: str | None = None,
):
    """Decorator to mark functions as MCP tools.
    
    This decorator is backward-compatible with the old FastAPI implementation.
    It now marks functions for MCP registration while preserving metadata.
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


def scan_and_register_tools():
    """Scan tools/ directory and register all @mcp_tool decorated functions."""
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
                    register_tool_with_mcp(attr)
                    tool_count += 1
                    
        except Exception as e:
            print(f"Warning: Failed to load {py_file.name}: {e}")
            continue
    
    print(f"✅ Registered {tool_count} MCP tools from {tools_dir}")
    return tool_count


def register_tool_with_mcp(func: Callable):
    """Register a tool function with the MCP server.
    
    This function:
    1. Stores the tool in the registry
    2. Extracts metadata from the function
    3. Registers it with MCPServer using the @mcp.tool() decorator
    4. Preserves the exact function signature for schema generation
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
    
    # Create wrapper function
    def tool_wrapper(**kwargs) -> Any:
        """MCP tool wrapper that calls the original function."""
        try:
            result = func(**kwargs)
            return result
        except Exception as e:
            # Return error as string so MCP client can see it
            error_msg = f"Tool execution error: {type(e).__name__}: {str(e)}"
            print(f"❌ {tool_name} failed: {error_msg}")
            raise # Return error as string so MCP client can see it
    
    # Set metadata BEFORE decorating (FastMCP uses function name for registration)
    tool_wrapper.__name__ = tool_name
    tool_wrapper.__doc__ = description
    tool_wrapper.__annotations__ = annotations
    tool_wrapper.__signature__ = sig
    tool_wrapper.__signature__ = sig
    tool_wrapper.__annotations__ = annotations
    
    # Now register with FastMCP using the properly named wrapper
    mcp.tool()(tool_wrapper)
    
    print(f"  📌 {tool_name}({', '.join(sig.parameters.keys())})")


# Auto-scan and register tools at module load
print("\n🔍 Scanning for MCP tools...")
scan_and_register_tools()
print(f"✅ MCP Server ready with {len(_tool_registry)} tools\n")

# Run server when executed directly
if __name__ == "__main__":
    print("🚀 Starting I-GUIDE MCP Server...")
    print("   Server name: I-GUIDE Tools")
    print("   Transport: STDIO (Standard Input/Output)")
    print("\n📝 For MCP Inspector:")
    print("   1. In Inspector, select 'STDIO' transport")
    print("   2. Command: python")
    print("   3. Arguments: -m MCP_server.server")
    print("\n🛑 Starting stdio server...\n")
    
    # Use stdio transport (works better with MCP Inspector)
    mcp.run(transport="stdio")
