# archive/

Dead code retained for reference, not imported by the running system.

## smolagents (archived 2026-06)

The platform's agent layer moved from **smolagents** to a **LangGraph**
supervisor-over-peers architecture (`agent_runtime/`). The smolagents integration is
no longer used:

- `smolagents_adapter.py` — wrapped MCP `@mcp_tool` functions as smolagents tools
  (`get_smolagents_tools`). Superseded by the LangGraph runtime, which reaches MCP
  tools via `agent_runtime/langchain_mcp_tools.py` (remote MCP) and the MCP server's
  dual MCP + REST registration (`MCP_server/server.py:register_tool_with_mcp`).
- `smolagent_mcp_tools.ipynb` — the smolagents demo notebook.

The `smolagents` dependency was removed from `requirements.txt` and the smolagents
checks were removed from `scripts/check_mcp_install.py`.
