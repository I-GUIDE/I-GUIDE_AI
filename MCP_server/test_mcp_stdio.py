#!/usr/bin/env python3
"""
Test MCP server using stdio transport (simpler than SSE for testing).

Usage:
    python test_mcp_stdio.py
"""

import asyncio
import subprocess
import sys
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

async def test_mcp_stdio():
    """Test MCP server via stdio transport."""
    print("=" * 60)
    print("🧪 Testing MCP Server (STDIO Transport)")
    print("=" * 60)
    print()
    
    # Start the server as a subprocess with stdio transport
    server_script = sys.executable
    server_params = StdioServerParameters(
        command=server_script,
        args=["-c", """
import sys
sys.path.insert(0, '/Users/shritan/Desktop/IGUIDE/i-guide-platform-flask-servers')
from MCP_server.server import mcp
mcp.run(transport='stdio')
"""],
        env=None
    )
    
    print("📡 Starting MCP server via stdio...")
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                print("✅ Connected to MCP server")
                print()
                
                # Initialize session
                print("🔄 Initializing session...")
                await session.initialize()
                print("✅ Session initialized")
                print()
                
                # List available tools
                print("📋 Listing available tools...")
                tools_result = await session.list_tools()
                tools = tools_result.tools
                
                print(f"✅ Found {len(tools)} tools:")
                for i, tool in enumerate(tools, 1):
                    print(f"   {i}. {tool.name}")
                    if tool.description:
                        desc_short = tool.description[:60] + "..." if len(tool.description) > 60 else tool.description
                        print(f"      {desc_short}")
                print()
                
                # Call a tool
                print("🔧 Calling estimate_biomass tool...")
                tool_result = await session.call_tool(
                    "estimate_biomass",
                    {
                        "region": "Iowa",
                        "year": 2023
                    }
                )
                
                print("✅ Tool executed successfully!")
                if tool_result.content:
                    for item in tool_result.content:
                        if hasattr(item, 'text'):
                            print(f"   Result: {item.text}")
                print()
                
                # Call another tool
                print("🔧 Calling search_publications tool...")
                tool_result = await session.call_tool(
                    "search_publications",
                    {
                        "query": "climate change",
                        "limit": 3
                    }
                )
                
                print("✅ Tool executed successfully!")
                if tool_result.content:
                    for item in tool_result.content:
                        if hasattr(item, 'text'):
                            result_preview = item.text[:200] + "..." if len(item.text) > 200 else item.text
                            print(f"   Result preview: {result_preview}")
                print()
                
                print("=" * 60)
                print("✅ All MCP tests passed!")
                print("=" * 60)
                
                return True
                
    except Exception as e:
        print(f"❌ Error: {e}")
        print()
        
        # Print full traceback for debugging
        import traceback
        print("Full error details:")
        traceback.print_exc()
        print()
        
        return False

async def main():
    success = await test_mcp_stdio()
    if not success:
        exit(1)

if __name__ == "__main__":
    print()
    asyncio.run(main())
