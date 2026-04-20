"""Quick test script to verify MCP implementation.

Run this to verify your MCP server implementation is working correctly.
"""

import sys
import os

# Add repo root to sys.path (scripts/ is one level below repo root)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_imports():
    """Test that required modules can be imported."""
    print("🧪 Testing imports...")
    try:
        import mcp
        # MCP module doesn't expose __version__ directly, but we can check it's importable
        print(f"  ✅ mcp installed")
        from mcp.server import FastMCP
        print(f"  ✅ FastMCP class available")
    except ImportError as e:
        print(f"  ❌ mcp not installed: {e}")
        print("     Run: pip install --user 'mcp[cli]'")
        return False
    
    try:
        import smolagents
        print(f"  ✅ smolagents installed")
    except ImportError as e:
        print(f"  ❌ smolagents not installed: {e}")
        print("     Run: pip install --user smolagents")
        return False
    
    return True


def test_local_adapter():
    """Test the local adapter (recommended approach)."""
    print("\n🧪 Testing local adapter...")
    try:
        from MCP_server.smolagents_adapter import get_smolagents_tools
        tools = get_smolagents_tools()
        print(f"  ✅ Loaded {len(tools)} tools")
        
        # List tools
        print("\n  📋 Available tools:")
        for i, tool in enumerate(tools, 1):
            # smolagents tools are SimpleTool objects with .name attribute
            tool_name = tool.name if hasattr(tool, 'name') else str(tool)
            print(f"     {i}. {tool_name}")
        
        return True
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_simple_tool():
    """Test a simple tool execution."""
    print("\n🧪 Testing tool execution...")
    try:
        from MCP_server.smolagents_adapter import get_smolagents_tools
        tools = get_smolagents_tools()
        
        # Find estimate_biomass tool
        biomass_tool = None
        for t in tools:
            tool_name = t.name if hasattr(t, 'name') else str(t)
            if tool_name == 'estimate_biomass':
                biomass_tool = t
                break
        
        if not biomass_tool:
            print("  ❌ estimate_biomass tool not found")
            return False
        
        # Execute tool
        result = biomass_tool(region="Iowa", year=2023)
        print(f"  ✅ Tool executed successfully")
        print(f"     Result: {result}")
        
        # Verify result structure
        if isinstance(result, dict) and 'biomass_tons' in result:
            print(f"  ✅ Result has expected structure")
            return True
        else:
            print(f"  ⚠️  Unexpected result structure")
            return False
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_data_loading():
    """Test data loading tools (summary returns)."""
    print("\n🧪 Testing data loading tools...")
    try:
        from MCP_server.smolagents_adapter import get_smolagents_tools
        tools = get_smolagents_tools()
        
        # Find load_chicago_community_areas tool
        load_tool = None
        for t in tools:
            tool_name = t.name if hasattr(t, 'name') else str(t)
            if tool_name == 'load_chicago_community_areas':
                load_tool = t
                break
        
        if not load_tool:
            print("  ❌ load_chicago_community_areas tool not found")
            return False
        
        # Execute tool
        print("  ⏳ Loading Chicago community areas (may take a few seconds)...")
        result = load_tool()
        
        # Verify it returns a summary, not full GeoJSON
        if isinstance(result, dict):
            if 'feature_count' in result and '_cache_key' in result:
                print(f"  ✅ Returns compact summary")
                print(f"     Feature count: {result['feature_count']}")
                print(f"     Sample features: {len(result.get('sample_features', []))}")
                
                # Check token estimate
                import json
                json_str = json.dumps(result)
                char_count = len(json_str)
                token_estimate = char_count // 4  # Rough estimate
                
                if token_estimate < 10000:
                    print(f"  ✅ Token estimate: ~{token_estimate} (within limits)")
                    return True
                else:
                    print(f"  ⚠️  Token estimate: ~{token_estimate} (high)")
                    return False
            else:
                print(f"  ⚠️  Result doesn't have expected summary structure")
                print(f"     Keys: {list(result.keys())}")
                return False
        else:
            print(f"  ❌ Result is not a dict: {type(result)}")
            return False
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("MCP Implementation Test Suite")
    print("=" * 60)
    
    results = []
    
    # Run tests
    results.append(("Imports", test_imports()))
    results.append(("Local Adapter", test_local_adapter()))
    results.append(("Simple Tool", test_simple_tool()))
    results.append(("Data Loading", test_data_loading()))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    all_passed = all(result for _, result in results)
    
    if all_passed:
        print("\n🎉 All tests passed! MCP implementation is working correctly.")
        print("\n📝 Next steps:")
        print("   1. Update notebook Cell 2 with local adapter")
        print("   2. Test full notebook workflow")
        print("   3. Optional: Start MCP server and test with Inspector")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check error messages above.")
        print("\n📝 Common fixes:")
        print("   - Install MCP SDK: pip install --user 'mcp[cli]'")
        print("   - Install smolagents: pip install --user smolagents")
        print("   - Check that you're in the correct directory")
        return 1


if __name__ == "__main__":
    sys.exit(main())
