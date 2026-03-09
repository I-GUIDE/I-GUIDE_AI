#!/usr/bin/env python3
"""
Proper MCP client for testing the MCP server.
This implements the full MCP protocol with session management.

Usage:
    python test_mcp_client.py
"""

import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

async def test_mcp_sse_server():
    """Test MCP server via SSE transport."""
    print("=" * 60)
    print("🧪 Testing MCP Server (SSE Transport)")
    print("=" * 60)
    print()
    
    # Connect to SSE endpoint
    url = "http://localhost:8000"
    print(f"📡 Connecting to: {url}")
    
    try:
        async with sse_client(url) as (read, write):
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
                    print(f"      {tool.description}")
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
                print(f"   Result: {tool_result.content}")
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
                print(f"   Result preview: {str(tool_result.content)[:200]}...")
                print()
                
                print("=" * 60)
                print("✅ All MCP tests passed!")
                print("=" * 60)
                
    except Exception as e:
        print(f"❌ Error: {e}")
        print()
        
        # Print full traceback for debugging
        import traceback
        print("Full error details:")
        traceback.print_exc()
        print()
        
        print("Make sure the MCP server is running:")
        print("  cd MCP_server")
        print("  python server.py")
        return False
    
    return True

async def main():
    success = await test_mcp_sse_server()
    if not success:
        exit(1)

if __name__ == "__main__":
    print()
    asyncio.run(main())
