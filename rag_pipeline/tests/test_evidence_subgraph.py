"""Tests for the shared-state evidence/quality sub-graph (retrieve→rerank→analyze→audit).

Fully stubbed — a fake str->str LLM (routed by prompt) drives rerank + audit, and
retrieve/compose are injected. No network, no keys.
"""

from __future__ import annotations

import json

from agent_runtime.evidence_subgraph import (
    build_evidence_subgraph,
    default_compose_fn,
    distill_evidence_state,
    extract_documents_from_search_evidence,
    run_evidence_pipeline,
)

DOCS = [
    {"doc_id": "a", "title": "A", "contents": "alpha"},
    {"doc_id": "b", "title": "B", "contents": "beta floods texas"},
    {"doc_id": "c", "title": "C", "contents": "gamma"},
]


def _retrieve(query, state):
    return list(DOCS)


def _stub_compose(query, documents, chat_history=None):
    return "composed"


def _fake_llm(prompt: str) -> str:
    low = prompt.lower()
    if "auditing a retrieval-augmented answer" in low:
        return json.dumps(
            {"hallucination_detected": False, "severity": "none", "issues": [], "summary": "grounded"}
        )
    if "relevance judge" in low:
        return json.dumps({"ranking": [
            {"doc_id": "b", "score": 0.9},
            {"doc_id": "a", "score": 0.5},
            {"doc_id": "c", "score": 0.1},
        ]})
    return "fallback"


def test_pipeline_runs_retrieve_rerank_analyze_audit():
    state = run_evidence_pipeline("texas floods", _retrieve, compose_fn=_stub_compose, llm=_fake_llm, top_k=5)
    assert [d["doc_id"] for d in state["documents"]] == ["b", "a", "c"]  # reranked
    assert state["answer"] == "composed"
    assert state["audit"]["summary"] == "grounded"
    # distilled payload surfaced upward (compact, no heavy docs)
    assert state["distilled"]["answer"] == "composed"
    assert state["distilled"]["doc_ids"] == ["b", "a", "c"]
    assert state["distilled"]["document_count"] == 3


def test_rerank_can_be_disabled_and_top_k_trims():
    state = run_evidence_pipeline(
        "q", _retrieve, compose_fn=_stub_compose, llm=_fake_llm, do_rerank=False, top_k=2
    )
    assert [d["doc_id"] for d in state["documents"]] == ["a", "b"]  # original order, trimmed


def test_audit_can_be_disabled():
    state = run_evidence_pipeline("q", _retrieve, compose_fn=_stub_compose, llm=_fake_llm, do_audit=False)
    assert state["audit"] == {}
    assert "distilled" in state and state["distilled"]["answer"] == "composed"


def test_graph_has_linear_pipeline_nodes():
    g = build_evidence_subgraph(_retrieve, compose_fn=_stub_compose, llm=_fake_llm)
    nodes = set(g.get_graph().nodes)
    for n in ("retrieve", "rerank", "analyze", "audit"):
        assert n in nodes


def test_extract_documents_from_search_evidence():
    payload = {
        "search_agent_tool_results": [
            {"name": "keyword_search", "content": json.dumps([{"doc_id": "a", "title": "A", "contents": "x"}])},
            {"name": "semantic_search", "content": json.dumps({"results": [{"id": "b", "name": "B", "snippet": "y"}]})},
            {"name": "broken", "content": "not json"},
        ]
    }
    docs = extract_documents_from_search_evidence(payload)
    ids = {(d.get("doc_id") or d.get("id")) for d in docs}
    assert ids == {"a", "b"}


def test_distill_is_compact():
    st = {"documents": DOCS, "answer": "ans", "audit": {"summary": "ok"}}
    d = distill_evidence_state(st)
    assert d["answer"] == "ans"
    assert d["doc_ids"] == ["a", "b", "c"]
    assert d["document_count"] == 3
    assert "documents" not in d  # heavy docs not surfaced upward


def test_default_compose_uses_llm_and_evidence():
    captured = {}

    def rec(prompt):
        captured["prompt"] = prompt
        return "grounded answer [b]"

    compose = default_compose_fn(llm=rec)
    out = compose("what floods?", DOCS)
    assert out == "grounded answer [b]"
    assert "Evidence:" in captured["prompt"]
    assert "[a]" in captured["prompt"] and "[b]" in captured["prompt"]
