#!/usr/bin/env python3
"""
Test file upload through MCP HTTP server.

This script tests the full MCP protocol flow with image uploads:
1. Reads an image file
2. Encodes to base64 (for JSON transport)
3. Sends MCP JSON-RPC request
4. Receives response through MCP protocol

Usage:
    # Terminal 1: Start MCP server
    cd MCP_server && python server.py --http
    
    # Terminal 2: Run this test
    python test_mcp_http_upload.py
"""

import requests
import base64
import json
import sys
import os
from pathlib import Path

# MCP server endpoint
MCP_SERVER_URL = "http://localhost:8000"
MCP_ENDPOINT = f"{MCP_SERVER_URL}/mcp"

# Global session ID
session_id = None

def initialize_session():
    """Initialize MCP session and get session ID"""
    global session_id
    
    print("🔗 Initializing MCP session...")
    
    request = {
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
        "id": 0
    }
    
    try:
        response = requests.post(
            MCP_ENDPOINT,
            json=request,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
        )
        response.raise_for_status()
        
        # Get session ID from headers
        session_id = response.headers.get('mcp-session-id')
        
        if session_id:
            print(f"✅ Session initialized: {session_id[:16]}...")
            return True
        else:
            print("❌ No session ID in response")
            return False
            
    except Exception as e:
        print(f"❌ Error initializing session: {e}")
        return False

def test_tool_discovery():
    """Test 1: Discover available tools via MCP protocol"""
    print("=" * 60)
    print("TEST 1: Tool Discovery")
    print("=" * 60)
    
    request = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "params": {},
        "id": 1
    }
    
    try:
        response = requests.post(
            MCP_ENDPOINT,
            json=request,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "mcp-session-id": session_id
            }
        )
        response.raise_for_status()
        result = response.json()
        
        if "result" in result and "tools" in result["result"]:
            tools = result["result"]["tools"]
            print(f"✅ Found {len(tools)} tools:")
            for tool in tools:
                print(f"   • {tool['name']}")
            return True
        else:
            print(f"❌ Unexpected response: {result}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_simple_tool():
    """Test 2: Call a simple tool (biomass estimation)"""
    print("\n" + "=" * 60)
    print("TEST 2: Simple Tool Call (No Upload)")
    print("=" * 60)
    
    request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "estimate_biomass",
            "arguments": {
                "region": "Iowa",
                "year": 2023
            }
        },
        "id": 2
    }
    
    try:
        print("📡 Calling: estimate_biomass(region='Iowa', year=2023)")
        response = requests.post(
            MCP_ENDPOINT,
            json=request,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "mcp-session-id": session_id
            }
        )
        response.raise_for_status()
        result = response.json()
        
        if "result" in result:
            print(f"✅ Result: {result['result']}")
            return True
        else:
            print(f"❌ Error in response: {result}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_image_upload(image_path: str):
    """Test 3: Upload and describe image via MCP protocol"""
    print("\n" + "=" * 60)
    print("TEST 3: Image Upload Through MCP Protocol")
    print("=" * 60)
    
    # Check file exists
    if not os.path.exists(image_path):
        print(f"❌ Image file not found: {image_path}")
        print("\n💡 To fix:")
        print(f"   1. Place an image at: {image_path}")
        print(f"   2. Or edit this script to use a different path")
        return False
    
    print(f"📸 Image: {image_path}")
    
    # Read and encode image
    try:
        with open(image_path, 'rb') as f:
            image_bytes = f.read()
        
        print(f"✅ Read {len(image_bytes):,} bytes")
        
        image_b64 = base64.b64encode(image_bytes).decode()
        print(f"✅ Encoded to base64: {len(image_b64):,} characters")
        
    except Exception as e:
        print(f"❌ Error reading file: {e}")
        return False
    
    # Build MCP request
    request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "describe_image",
            "arguments": {
                "file": {
                    "content": image_b64,
                    "name": os.path.basename(image_path)
                },
                "prompt_text": "Describe what you see in this image in detail."
            }
        },
        "id": 3
    }
    
    print("\n📡 Sending MCP request...")
    print(f"   Method: tools/call")
    print(f"   Tool: describe_image")
    print(f"   Request size: ~{len(json.dumps(request)):,} bytes")
    
    try:
        # Send to MCP server
        response = requests.post(
            MCP_ENDPOINT,
            json=request,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "mcp-session-id": session_id
            },
            timeout=60  # Vision API can be slow
        )
        
        response.raise_for_status()
        result = response.json()
        
        # Parse result
        if "result" in result:
            print("\n" + "=" * 60)
            print("✅ SUCCESS - Image Description:")
            print("=" * 60)
            
            # Extract the actual description
            description = result["result"]
            if isinstance(description, dict) and "content" in description:
                description = description["content"]
            elif isinstance(description, list) and len(description) > 0:
                description = description[0].get("text", str(description))
            
            print(description)
            print("=" * 60)
            return True
            
        elif "error" in result:
            print(f"\n❌ MCP Error: {result['error']}")
            return False
        else:
            print(f"\n⚠️  Unexpected response format:")
            print(json.dumps(result, indent=2))
            return False
            
    except requests.exceptions.Timeout:
        print(f"❌ Request timeout (vision API took too long)")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "MCP HTTP UPLOAD TEST SUITE" + " " * 16 + "║")
    print("╚" + "=" * 58 + "╝")
    
    # Check if server is running
    print("\n🔍 Checking MCP server...")
    try:
        response = requests.get(MCP_SERVER_URL, timeout=2)
        print(f"✅ MCP server is running at {MCP_SERVER_URL}")
    except:
        print(f"❌ MCP server not running at {MCP_SERVER_URL}")
        print("\n💡 Start the server first:")
        print("   cd MCP_server")
        print("   python server.py --http")
        return 1
    
    # Initialize session
    if not initialize_session():
        print("❌ Failed to initialize MCP session")
        return 1
    
    # Run tests
    results = []
    
    results.append(("Tool Discovery", test_tool_discovery()))
    results.append(("Simple Tool Call", test_simple_tool()))
    
    # Ask for image path
    print("\n" + "=" * 60)
    print("📸 Image Upload Test")
    print("=" * 60)
    print("\nEnter path to test image (or press Enter for default):")
    print("Default: /Users/shritan/Desktop/test_image.jpg")
    user_input = input("Path: ").strip().strip('"').strip("'")
    
    if not user_input:
        image_path = "/Users/shritan/Desktop/test_image.jpg"
    else:
        image_path = user_input
    
    results.append(("Image Upload", test_image_upload(image_path)))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    all_passed = all(result for _, result in results)
    
    if all_passed:
        print("\n🎉 All tests passed!")
        print("\n✅ Your MCP server is working correctly with:")
        print("   • HTTP transport")
        print("   • Tool discovery")
        print("   • Simple tools")
        print("   • Image upload tools")
        print("\n🚀 Ready for production deployment!")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
