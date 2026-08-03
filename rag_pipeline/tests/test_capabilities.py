"""Capability/meta questions ("what tools do you have") answered from the live registries.

No LLM in the supervisor pipeline sees the tool registry, so these were previously answered
with irrelevant KB retrieval or the no-grounding reply.
"""

from __future__ import annotations

from agent_runtime.capabilities import describe_capabilities, is_capability_query


def test_capability_detection_positive():
    for q in (
        "what tools do you have",
        "What tools can you use?",
        "Which tools do you have access to",
        "what can you do",
        "What are your capabilities?",
        "list your tools",
        "show me your available tools",
        "what skills do you have",
    ):
        assert is_capability_query(q), q


def test_capability_detection_does_not_hijack_domain_questions():
    for q in (
        "what tools are available for flood mapping",     # domain, not about the assistant
        "what tools do researchers use for accessibility analysis",
        "show tools for hydrology analysis",
        "explain the QGIS processing tools in this notebook",
        "find datasets about surveying tools",
    ):
        assert not is_capability_query(q), q


def test_describe_capabilities_is_capability_prose_not_a_tool_dump():
    """User-facing prose: no internal tool names, no per-tool bullet list."""
    text = describe_capabilities()
    for internal in ("keyword_search", "semantic_search", "neo4j_search", "spatial_search",
                     "opengeodata_search", "inspect_vector", "plot_vector", "geocode_places",
                     "execute_code", "kb_point_heatmap", "agent_kb_search"):
        assert internal not in text, internal
    # capabilities are described in plain language instead
    assert "knowledge base by keyword and by meaning" in text
    assert "coordinate system" in text            # vector inspection
    assert "coordinates" in text                  # geocoding
    assert "Finding things" in text and "Working with geospatial data" in text


def test_describe_capabilities_covers_all_registries():
    """Everything the deployment offers is represented — including the pieces a per-request
    client allowlist would hide (external open data, KB code/method search, MCP)."""
    text = describe_capabilities(include_mcp_tools=True)
    assert "NASA CMR" in text and "Data.gov" in text          # external open data
    assert "implementation-level detail" in text              # agent-KB code/method search
    assert "heat maps" in text                                # runnable KB workflows
    assert "MCP service" in text                              # MCP spatial-analysis tools
    assert "related elements its contributor curated" in text # by-id + related


def test_describe_capabilities_ignores_request_allowlist_but_honors_deployment_gates():
    """A client's search-method allowlist is not a limit on what the assistant can do, so the
    self-description still covers everything; a real deployment gate (code exec off) is honest."""
    text = describe_capabilities(enabled_search_methods=["keyword_search"], code_exec=False)
    assert "NASA CMR" in text                                  # not narrowed by the allowlist
    assert "running it is disabled on this deployment" in text  # honest about code exec


def test_graph_routes_capability_question_deterministically(monkeypatch):
    """'what tools do you have' must be answered by the capabilities node — the orchestrate
    strategy (LLM pipeline) must not run at all."""
    import agent_runtime.orchestrator_graph as og
    import agent_runtime.strategy as strat

    def explode(*a, **k):
        raise AssertionError("orchestrate strategy must not run for a capability question")
    monkeypatch.setattr(strat, "get_orchestration_strategy", explode)

    graph = og.build_orchestrator_graph(llm=object())     # llm never invoked on this route
    state = graph.invoke({"query": "what tools do you have", "chat_history": [], "thread_id": None})
    answer = state.get("final_answer") or ""
    assert "Here is what I can help with" in answer
    assert "knowledge base by keyword and by meaning" in answer
