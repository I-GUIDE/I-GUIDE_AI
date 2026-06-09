"""Tests for the shared-state supervisor-over-peers graph.

Fully stubbed: scripted decider + injected worker fns + a fake str->str LLM
(routed by prompt for rerank/audit). No network, no keys.
"""

from __future__ import annotations

import json

from agent_runtime.supervisor_graph import (
    build_supervisor_graph,
    is_supervisor_enabled,
    run_supervisor,
)

DOCS = [
    {"doc_id": "a", "title": "A", "contents": "alpha"},
    {"doc_id": "b", "title": "B", "contents": "beta"},
    {"doc_id": "c", "title": "C", "contents": "gamma"},
]


def _fake_llm(prompt: str) -> str:
    low = prompt.lower()
    if "auditing a retrieval-augmented answer" in low:
        return json.dumps({"hallucination_detected": False, "severity": "none", "issues": [], "summary": "grounded"})
    if "relevance judge" in low:
        return json.dumps({"ranking": [
            {"doc_id": "b", "score": 0.9}, {"doc_id": "a", "score": 0.5}, {"doc_id": "c", "score": 0.1},
        ]})
    return "x"


def _scripted(seq):
    box = {"i": 0}

    def decide(state, distilled):
        i = box["i"]
        box["i"] += 1
        return seq[i] if i < len(seq) else "done"

    return decide


def test_supervisor_loops_search_then_analyze_then_synthesize():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "analyze", "done"]),
        search_fn=lambda q, s: list(DOCS),
        analyze_fn=lambda q, ev, st: {"summary": "ran workflow"},   # workflow output, not prose
        synthesize_fn=lambda q, ev, ar, cr, ch: "the answer",       # answer composed separately
        do_rerank=False,
    )
    assert state["actions"] == ["search", "analyze", "done"]
    assert {d["doc_id"] for d in state["evidence"]} == {"a", "b", "c"}
    assert state["analysis_results"] == {"summary": "ran workflow"}
    assert state["final_answer"] == "the answer"
    assert state["audit"]["summary"] == "grounded"  # audit now in synthesize


def test_evidence_accumulates_and_dedups_across_searches():
    calls = {"n": 0}

    def search_fn(q, s):
        calls["n"] += 1
        return DOCS[:2] if calls["n"] == 1 else DOCS[1:]  # overlap on 'b'

    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "search", "analyze", "done"]),
        search_fn=search_fn, analyze_fn=lambda *a: {"summary": "s"},
        synthesize_fn=lambda *a: "ans", do_rerank=False, do_audit=False,
    )
    assert [d["doc_id"] for d in state["evidence"]] == ["a", "b", "c"]  # 'b' not duplicated


def test_max_steps_guard_terminates():
    state = run_supervisor(
        "q", llm=_fake_llm, max_steps=3,
        decide_fn=lambda s, d: "search",  # would loop forever
        search_fn=lambda q, s: [], analyze_fn=lambda *a: {}, synthesize_fn=lambda *a: "",
        do_rerank=False, do_audit=False,
    )
    assert state["actions"][-1] == "done"
    assert len(state["actions"]) <= 4


def test_code_peer_feeds_synthesis():
    state = run_supervisor(
        "write code", llm=_fake_llm,
        decide_fn=_scripted(["code", "done"]),
        search_fn=lambda q, s: [],
        code_fn=lambda q, ev, st: {"answer": "code-answer", "code_result": {"x": 1}},
        synthesize_fn=lambda q, ev, ar, cr, ch: f"final:{(cr or {}).get('answer', '')}",
        do_audit=False,
    )
    assert state["code_result"]["answer"] == "code-answer"
    assert state["final_answer"] == "final:code-answer"  # synthesize incorporates code_result


def test_graph_has_peer_nodes_codefn_3arg():
    # CodeFn now takes (query, evidence, state)
    g = build_supervisor_graph(
        decide_fn=_scripted(["done"]), search_fn=lambda q, s: [],
        analyze_fn=lambda *a: {}, code_fn=lambda q, ev, st: None,
        synthesize_fn=lambda *a: "", llm=_fake_llm,
    )
    assert "code" in set(g.get_graph().nodes)


def test_analyze_request_drives_supervisor_routing():
    """analyze signals it needs evidence -> supervisor runs search, then re-runs analyze."""
    calls = {"analyze": 0}

    def analyze_fn(q, ev, st):
        calls["analyze"] += 1
        if calls["analyze"] == 1:
            return {"summary": "insufficient", "needs": ["search"]}  # request evidence
        return {"summary": "complete"}

    def decide(state, distilled):  # only consulted when the needs queue is empty
        return "analyze" if state.get("actions", []).count("analyze") == 0 else "done"

    state = run_supervisor(
        "q", llm=_fake_llm, decide_fn=decide,
        search_fn=lambda q, s: list(DOCS), analyze_fn=analyze_fn,
        synthesize_fn=lambda *a: "final", do_rerank=False, do_audit=False,
    )
    assert calls["analyze"] == 2  # re-ran after its request was fulfilled
    assert state["actions"] == ["analyze", "search", "analyze", "done"]
    assert {d["doc_id"] for d in state["evidence"]} == {"a", "b", "c"}
    assert state["analysis_results"] == {"summary": "complete"}
    assert state["final_answer"] == "final"


def test_code_request_routes_then_reruns():
    """code signals it needs evidence -> supervisor runs search, then re-runs code."""
    calls = {"code": 0}

    def code_fn(q, ev, st):
        calls["code"] += 1
        if calls["code"] == 1:
            return {"needs": ["search"]}  # code needs evidence first
        return {"answer": "code-done", "code_result": {"ok": True}}

    def decide(state, distilled):
        return "code" if state.get("actions", []).count("code") == 0 else "done"

    state = run_supervisor(
        "write code", llm=_fake_llm, decide_fn=decide,
        search_fn=lambda q, s: list(DOCS), code_fn=code_fn,
        synthesize_fn=lambda q, ev, ar, cr, ch: f"final:{(cr or {}).get('answer', '')}",
        do_rerank=False, do_audit=False,
    )
    assert calls["code"] == 2
    assert state["actions"] == ["code", "search", "code", "done"]
    assert state["code_result"]["answer"] == "code-done"
    assert state["final_answer"] == "final:code-done"


def test_request_capability_tool_records_needs():
    """The request_capability tool (the model-driven signal) records valid requests."""
    from agent_runtime.supervisor_graph import _make_request_tool

    tool, requests = _make_request_tool()
    tool.invoke({"capability": "search", "reason": "need evidence from KB"})
    tool.invoke({"capability": "bogus"})  # ignored
    assert requests == [{"capability": "search", "reason": "need evidence from KB"}]


def test_rerank_is_bundled_into_search():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "done"]),
        search_fn=lambda q, s: list(DOCS), synthesize_fn=lambda *a: "x",
        do_rerank=True, do_audit=False,
    )
    assert [d["doc_id"] for d in state["evidence"]] == ["b", "a", "c"]  # reranked


def test_distilled_view_is_compact():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "analyze", "done"]),
        search_fn=lambda q, s: list(DOCS), analyze_fn=lambda *a: {"summary": "s"},
        synthesize_fn=lambda *a: "ans", do_rerank=False, do_audit=False,
    )
    d = state["distilled"]
    assert d["has_evidence"] and d["document_count"] == 3 and d["answer"] == "ans"
    assert "evidence" not in d and "documents" not in d  # heavy docs not surfaced


def test_graph_has_peer_nodes():
    g = build_supervisor_graph(
        decide_fn=_scripted(["done"]), search_fn=lambda q, s: [],
        analyze_fn=lambda *a: {}, code_fn=lambda q, ev: None,
        synthesize_fn=lambda *a: "", llm=_fake_llm,
    )
    nodes = set(g.get_graph().nodes)
    for n in ("supervisor", "search", "analyze", "code", "synthesize"):
        assert n in nodes


def test_flag_parsing(monkeypatch):
    # Default ON when unset.
    monkeypatch.delenv("AGENT_SUPERVISOR", raising=False)
    assert is_supervisor_enabled() is True
    # Explicit opt-out.
    monkeypatch.setenv("AGENT_SUPERVISOR", "0")
    assert is_supervisor_enabled() is False
    monkeypatch.setenv("AGENT_SUPERVISOR", "false")
    assert is_supervisor_enabled() is False
    # Explicit on.
    monkeypatch.setenv("AGENT_SUPERVISOR", "1")
    assert is_supervisor_enabled() is True


def test_orchestrate_uses_supervisor_by_default(monkeypatch):
    """With AGENT_SUPERVISOR unset, the orchestrate node defaults to the supervisor."""
    import agent_runtime.supervisor_graph as sg
    import agent_runtime.graph_runtime as gr

    monkeypatch.delenv("AGENT_SUPERVISOR", raising=False)
    monkeypatch.setattr(
        sg, "run_supervisor",
        lambda query, **kwargs: {"final_answer": "supervisor answer", "evidence": [], "actions": ["analyze", "done"]},
    )

    result = gr.run_agent_query("find flood datasets for texas and summarize")
    assert result["final_answer"] == "supervisor answer"


def test_use_supervisor_false_forces_agents_as_tools(monkeypatch):
    """A per-request use_supervisor=False overrides the default and skips the supervisor."""
    from types import SimpleNamespace

    import agent_runtime.orchestrator_graph as og
    import agent_runtime.supervisor_graph as sg
    import agent_runtime.graph_runtime as gr

    def boom(*a, **k):
        raise AssertionError("run_supervisor should NOT be called when use_supervisor=False")

    monkeypatch.setattr(sg, "run_supervisor", boom)
    monkeypatch.setattr(og, "collect_orchestration_tools", lambda **k: [])
    monkeypatch.setattr(og, "build_orchestrator_agent_executor", lambda **k: object())
    monkeypatch.setattr(
        og, "invoke_agent_with_payload_fallback",
        lambda *a, **k: {"messages": [SimpleNamespace(content="agents-as-tools answer", type="ai", tool_calls=[])]},
    )

    result = gr.run_agent_query("substantive query", use_supervisor=False)
    assert result["final_answer"] == "agents-as-tools answer"
