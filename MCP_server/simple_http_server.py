#!/usr/bin/env python3
"""
Simple HTTP wrapper for MCP tools (for testing without MCP protocol complexity).
This provides a basic REST API for calling tools directly.

Usage:
    python simple_http_server.py

Then test with:
    curl http://localhost:8001/tools
    curl -X POST http://localhost:8001/call/estimate_biomass -H "Content-Type: application/json" -d '{"region":"Iowa","year":2023}'
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any, Dict
import uvicorn
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from MCP_server.smolagents_adapter import get_smolagents_tools

app = FastAPI(title="I-GUIDE Tools API", version="1.0.0")

# Load tools at startup
print("🔍 Loading tools...")
tools = get_smolagents_tools()
tools_dict = {tool.name: tool for tool in tools}
print(f"✅ Loaded {len(tools)} tools")

class ToolCallRequest(BaseModel):
    """Request body for tool calls."""
    arguments: Dict[str, Any]

@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "status": "ok",
        "message": "I-GUIDE Tools API",
        "tools_count": len(tools),
        "endpoints": {
            "list_tools": "GET /tools",
            "call_tool": "POST /call/{tool_name}",
            "tool_info": "GET /tools/{tool_name}"
        }
    }

@app.get("/tools")
def list_tools():
    """List all available tools."""
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputs": getattr(tool, "inputs", {})
            }
            for tool in tools
        ]
    }

@app.get("/tools/{tool_name}")
def get_tool_info(tool_name: str):
    """Get detailed information about a specific tool."""
    if tool_name not in tools_dict:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    
    tool = tools_dict[tool_name]
    return {
        "name": tool.name,
        "description": tool.description,
        "inputs": getattr(tool, "inputs", {}),
        "output_type": getattr(tool, "output_type", "any")
    }

@app.post("/call/{tool_name}")
def call_tool(tool_name: str, request: ToolCallRequest):
    """Call a specific tool with arguments."""
    if tool_name not in tools_dict:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    
    tool = tools_dict[tool_name]
    
    try:
        # Call the tool
        result = tool(**request.arguments)
        
        return {
            "success": True,
            "tool": tool_name,
            "arguments": request.arguments,
            "result": result
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Tool execution failed: {str(e)}"
        )

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Starting Simple HTTP API for I-GUIDE Tools")
    print("=" * 60)
    print()
    print("📍 URL: http://localhost:8001")
    print()
    print("📝 Test commands:")
    print("   curl http://localhost:8001/tools")
    print("   curl -X POST http://localhost:8001/call/estimate_biomass \\")
    print("     -H 'Content-Type: application/json' \\")
    print("     -d '{\"arguments\":{\"region\":\"Iowa\",\"year\":2023}}'")
    print()
    print("🛑 Press Ctrl+C to stop")
    print("=" * 60)
    print()
    
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
