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


def test_describe_capabilities_reflects_live_registries():
    text = describe_capabilities()
    # core retrieval tools present and grouped
    for name in ("keyword_search", "semantic_search", "neo4j_search", "opengeodata_search",
                 "geocode_places"):
        assert name in text, name
    assert "Knowledge-base search" in text
    assert "execute_code" in text                         # code exec section present


def test_describe_capabilities_honors_request_config():
    text = describe_capabilities(enabled_search_methods=["keyword_search"], code_exec=False)
    assert "keyword_search" in text
    assert "semantic_search" not in text                  # allowlist respected
    assert "currently disabled" in text                   # honest about code exec being off


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
    assert "keyword_search" in answer and "semantic_search" in answer
