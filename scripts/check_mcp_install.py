"""Quick sanity check for the MCP server.

Verifies the MCP SDK is installed and the MCP server loads + auto-registers its
tools (over both the MCP and REST transports). The agent layer reaches these tools
via the LangGraph runtime (`agent_runtime/langchain_mcp_tools.py`); the legacy
smolagents adapter has been archived (see `archive/`).
"""

import os
import sys

# Add repo root to sys.path (scripts/ is one level below repo root)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def test_imports() -> bool:
    print("🧪 Testing MCP SDK import...")
    try:
        import mcp  # noqa: F401
        from mcp.server import FastMCP  # noqa: F401
        print("  ✅ mcp + FastMCP available")
        return True
    except ImportError as e:
        print(f"  ❌ mcp not installed: {e}")
        print("     Run: pip install --user 'mcp[cli]'")
        return False


def test_server_tools() -> bool:
    """Import the MCP server and confirm it auto-registered tools."""
    print("\n🧪 Testing MCP server tool registration...")
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "MCP_server"))
        import server  # noqa: F401  (runs scan_and_register_tools at import)

        registry = getattr(server, "_tool_registry", {})
        names = sorted(registry.keys()) if isinstance(registry, dict) else []
        if not names:
            print("  ⚠️  no tools registered")
            return False
        print(f"  ✅ {len(names)} tools registered")
        for n in names:
            print(f"     • {n}")
        # the ingestion + generic-executor tools should be present
        for expected in ("ingest_github_repo", "run_notebook_workflow", "run_code_element"):
            mark = "✅" if expected in names else "⚠️ "
            print(f"  {mark} {expected}")
        return True
    except Exception as e:
        print(f"  ❌ Error importing MCP server: {e}")
        import traceback
        traceback.print_exc()
        return False


def main() -> int:
    print("=" * 60)
    print("MCP Server Sanity Check")
    print("=" * 60)
    results = [("MCP SDK import", test_imports()), ("Server tool registration", test_server_tools())]
    print("\n" + "=" * 60)
    for name, ok in results:
        print(f"{'✅ PASS' if ok else '❌ FAIL'} - {name}")
    all_ok = all(ok for _, ok in results)
    print("\n🎉 MCP server OK." if all_ok else "\n⚠️  Some checks failed (see above).")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
