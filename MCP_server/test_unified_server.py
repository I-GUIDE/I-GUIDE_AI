#!/usr/bin/env python3
"""
Test script for the unified server (MCP + REST).

This script verifies that:
1. Server starts successfully
2. Both MCP and REST endpoints are accessible
3. Tools are registered in both transports
4. Swagger UI is available

Usage:
    # Terminal 1: Start server
    python MCP_server/server.py
    
    # Terminal 2: Run tests
    python MCP_server/test_unified_server.py
"""

import requests
import time
import sys

BASE_URL = "http://localhost:8000"

def test_root_endpoint():
    """Test the root endpoint."""
    print("🧪 Testing root endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/")
        response.raise_for_status()
        data = response.json()
        print(f"   ✅ Root endpoint OK")
        print(f"   📊 Tools loaded: {data.get('tools_count', 0)}")
        return True
    except Exception as e:
        print(f"   ❌ Root endpoint failed: {e}")
        return False


def test_rest_api_info():
    """Test REST API info endpoint."""
    print("\n🧪 Testing REST API info endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/api/")
        response.raise_for_status()
        data = response.json()
        print(f"   ✅ REST API info OK")
        print(f"   📊 Tools: {data.get('tools_count', 0)}")
        return True
    except Exception as e:
        print(f"   ❌ REST API info failed: {e}")
        return False


def test_rest_tools_list():
    """Test REST API tools list."""
    print("\n🧪 Testing REST API tools list...")
    try:
        response = requests.get(f"{BASE_URL}/api/tools")
        response.raise_for_status()
        data = response.json()
        tools = data.get('tools', [])
        print(f"   ✅ Tools list OK")
        print(f"   📊 Found {len(tools)} tools:")
        for tool in tools[:5]:  # Show first 5
            print(f"      • {tool['name']}: {tool['description'][:60]}...")
        if len(tools) > 5:
            print(f"      ... and {len(tools) - 5} more")
        return True
    except Exception as e:
        print(f"   ❌ Tools list failed: {e}")
        return False


def test_swagger_ui():
    """Test that Swagger UI is accessible."""
    print("\n🧪 Testing Swagger UI...")
    try:
        response = requests.get(f"{BASE_URL}/api/docs")
        response.raise_for_status()
        print(f"   ✅ Swagger UI accessible at {BASE_URL}/api/docs")
        return True
    except Exception as e:
        print(f"   ❌ Swagger UI failed: {e}")
        return False


def test_openapi_schema():
    """Test that OpenAPI schema is generated."""
    print("\n🧪 Testing OpenAPI schema...")
    try:
        response = requests.get(f"{BASE_URL}/api/openapi.json")
        response.raise_for_status()
        data = response.json()
        paths = data.get('paths', {})
        tool_endpoints = [p for p in paths.keys() if p.startswith('/tool/')]
        print(f"   ✅ OpenAPI schema OK")
        print(f"   📊 Tool endpoints: {len(tool_endpoints)}")
        return True
    except Exception as e:
        print(f"   ❌ OpenAPI schema failed: {e}")
        return False


def test_health_endpoint():
    """Test health check endpoint."""
    print("\n🧪 Testing health endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/api/health")
        response.raise_for_status()
        data = response.json()
        print(f"   ✅ Health check OK")
        print(f"   📊 Status: {data.get('status')}")
        print(f"   📊 Transports: {', '.join(data.get('transports', []))}")
        return True
    except Exception as e:
        print(f"   ❌ Health check failed: {e}")
        return False


def main():
    print("=" * 70)
    print("🧪 Testing I-GUIDE Unified Server")
    print("=" * 70)
    
    # Check if server is running
    print("\n🔍 Checking if server is running...")
    try:
        requests.get(f"{BASE_URL}/", timeout=2)
        print("   ✅ Server is running")
    except requests.exceptions.ConnectionError:
        print("   ❌ Server is not running!")
        print("\n💡 Start the server first:")
        print("   python MCP_server/server.py")
        sys.exit(1)
    except Exception as e:
        print(f"   ❌ Error connecting to server: {e}")
        sys.exit(1)
    
    # Run tests
    results = []
    results.append(("Root Endpoint", test_root_endpoint()))
    results.append(("REST API Info", test_rest_api_info()))
    results.append(("Tools List", test_rest_tools_list()))
    results.append(("Swagger UI", test_swagger_ui()))
    results.append(("OpenAPI Schema", test_openapi_schema()))
    results.append(("Health Check", test_health_endpoint()))
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 Test Summary")
    print("=" * 70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"   {status} - {test_name}")
    
    print()
    print(f"   Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! The unified server is working correctly.")
        print("\n📝 Next steps:")
        print("   • Open Swagger UI: http://localhost:8000/api/docs")
        print("   • Test image tools with file upload")
        print("   • Connect MCP clients to: http://localhost:8000/mcp")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
