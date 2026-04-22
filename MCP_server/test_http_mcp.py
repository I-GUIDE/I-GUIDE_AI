#!/usr/bin/env python3
"""
Simple test script for the MCP HTTP server.
Run this with the server running on port 8000.

Usage:
    python test_http_mcp.py
"""

import requests
import json
import sys

BASE_URL = "http://localhost:8000/mcp"

def test_list_tools():
    """Test listing available tools."""
    print("🧪 Test 1: List available tools")
    
    payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "test-client",
                "version": "1.0.0"
            }
        },
        "id": 1
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        # Initialize session
        print("  📡 Initializing session...")
        response = requests.post(BASE_URL, json=payload, headers=headers)
        response.raise_for_status()
        
        init_result = response.json()
        print(f"  ✅ Session initialized")
        
        # Get session ID from headers or response
        session_id = response.headers.get("X-Session-Id") or response.headers.get("x-session-id")
        
        # Now list tools with session
        list_payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        }
        
        if session_id:
            headers["X-Session-Id"] = session_id
            print(f"  📋 Using session: {session_id[:16]}...")
        
        print("  📡 Listing tools...")
        response = requests.post(BASE_URL, json=list_payload, headers=headers)
        response.raise_for_status()
        
        result = response.json()
        
        if "result" in result and "tools" in result["result"]:
            tools = result["result"]["tools"]
            print(f"  ✅ Found {len(tools)} tools:")
            for tool in tools:
                print(f"     - {tool['name']}")
            return True, session_id
        else:
            print(f"  ❌ Unexpected response: {json.dumps(result, indent=2)}")
            return False, None
            
    except requests.exceptions.ConnectionError:
        print("  ❌ Connection failed. Is the server running?")
        print("     Start it with: python server.py")
        return False, None
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False, None


def test_call_tool(session_id=None):
    """Test calling a specific tool."""
    print("\n🧪 Test 2: Call estimate_biomass tool")
    
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "estimate_biomass",
            "arguments": {
                "region": "Iowa",
                "year": 2023
            }
        },
        "id": 3
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    if session_id:
        headers["X-Session-Id"] = session_id
    
    try:
        print("  📡 Calling estimate_biomass(region='Iowa', year=2023)...")
        response = requests.post(BASE_URL, json=payload, headers=headers)
        response.raise_for_status()
        
        result = response.json()
        
        if "result" in result:
            print(f"  ✅ Tool executed successfully:")
            content = result["result"].get("content", [])
            if content:
                for item in content:
                    if item.get("type") == "text":
                        print(f"     {item['text']}")
            return True
        elif "error" in result:
            print(f"  ❌ Error: {result['error']['message']}")
            return False
        else:
            print(f"  ❌ Unexpected response: {json.dumps(result, indent=2)}")
            return False
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def test_server_health():
    """Quick check if server is responding."""
    print("🧪 Test 0: Server health check")
    try:
        response = requests.get("http://localhost:8000/", timeout=2)
        print(f"  ✅ Server is responding (status: {response.status_code})")
        return True
    except:
        print("  ❌ Server is not responding on http://localhost:8000")
        print("     Start it with: python server.py")
        return False


def main():
    print("=" * 60)
    print("MCP HTTP Server Test Suite")
    print("=" * 60)
    print()
    
    # Test server health
    if not test_server_health():
        sys.exit(1)
    
    print()
    
    # Test listing tools
    success, session_id = test_list_tools()
    if not success:
        print("\n❌ Tests failed")
        sys.exit(1)
    
    # Test calling a tool
    success = test_call_tool(session_id)
    
    print()
    print("=" * 60)
    if success:
        print("✅ All tests passed!")
        print()
        print("Next steps:")
        print("  • Test with MCP Inspector: npx -y @modelcontextprotocol/inspector")
        print("  • Connect to: http://localhost:8000/mcp")
        print("  • Use in notebook: see smolagent_mcp_tools.ipynb")
    else:
        print("❌ Some tests failed")
    print("=" * 60)


if __name__ == "__main__":
    main()
