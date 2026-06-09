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


def test_supervisor_loops_search_then_analyze_then_done():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "analyze", "done"]),
        search_fn=lambda q, s: list(DOCS),
        analyze_fn=lambda q, ev, ch: "the answer",
        do_rerank=False,
    )
    assert state["actions"] == ["search", "analyze", "done"]
    assert {d["doc_id"] for d in state["evidence"]} == {"a", "b", "c"}
    assert state["final_answer"] == "the answer"
    assert state["audit"]["summary"] == "grounded"  # audit bundled into analyze


def test_evidence_accumulates_and_dedups_across_searches():
    calls = {"n": 0}

    def search_fn(q, s):
        calls["n"] += 1
        return DOCS[:2] if calls["n"] == 1 else DOCS[1:]  # overlap on 'b'

    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "search", "analyze", "done"]),
        search_fn=search_fn, analyze_fn=lambda *a: "ans", do_rerank=False,
    )
    assert [d["doc_id"] for d in state["evidence"]] == ["a", "b", "c"]  # 'b' not duplicated


def test_max_steps_guard_terminates():
    state = run_supervisor(
        "q", llm=_fake_llm, max_steps=3,
        decide_fn=lambda s, d: "search",  # would loop forever
        search_fn=lambda q, s: [], analyze_fn=lambda *a: "", do_rerank=False, do_audit=False,
    )
    assert state["actions"][-1] == "done"
    assert len(state["actions"]) <= 4


def test_code_peer_surfaces_answer():
    state = run_supervisor(
        "write code", llm=_fake_llm,
        decide_fn=_scripted(["code", "done"]),
        search_fn=lambda q, s: [],
        code_fn=lambda q, ev: {"answer": "code-answer", "code_result": {"x": 1}},
        analyze_fn=lambda *a: "", do_audit=False,
    )
    assert state["code_result"]["answer"] == "code-answer"
    assert state["final_answer"] == "code-answer"  # surfaced since analysis produced none


def test_rerank_is_bundled_into_search():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "done"]),
        search_fn=lambda q, s: list(DOCS), analyze_fn=lambda *a: "", do_rerank=True,
    )
    assert [d["doc_id"] for d in state["evidence"]] == ["b", "a", "c"]  # reranked


def test_distilled_view_is_compact():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "analyze", "done"]),
        search_fn=lambda q, s: list(DOCS), analyze_fn=lambda *a: "ans", do_rerank=False,
    )
    d = state["distilled"]
    assert d["has_evidence"] and d["document_count"] == 3 and d["answer"] == "ans"
    assert "evidence" not in d and "documents" not in d  # heavy docs not surfaced


def test_graph_has_peer_nodes():
    g = build_supervisor_graph(
        decide_fn=_scripted(["done"]), search_fn=lambda q, s: [],
        analyze_fn=lambda *a: "", code_fn=lambda q, ev: None, llm=_fake_llm,
    )
    nodes = set(g.get_graph().nodes)
    for n in ("supervisor", "search", "analyze", "code", "finalize"):
        assert n in nodes


def test_flag_parsing(monkeypatch):
    monkeypatch.delenv("AGENT_SUPERVISOR", raising=False)
    assert is_supervisor_enabled() is False
    monkeypatch.setenv("AGENT_SUPERVISOR", "true")
    assert is_supervisor_enabled() is True


def test_orchestrate_node_uses_supervisor_when_flagged(monkeypatch):
    """With AGENT_SUPERVISOR set, the orchestrate node routes through run_supervisor."""
    import agent_runtime.supervisor_graph as sg
    import agent_runtime.graph_runtime as gr

    monkeypatch.setenv("AGENT_SUPERVISOR", "1")
    monkeypatch.setattr(
        sg, "run_supervisor",
        lambda query, **kwargs: {"final_answer": "supervisor answer", "evidence": [], "actions": ["analyze", "done"]},
    )

    result = gr.run_agent_query("find flood datasets for texas and summarize")
    assert result["final_answer"] == "supervisor answer"
