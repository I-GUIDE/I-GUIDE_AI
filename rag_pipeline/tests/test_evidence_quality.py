"""Tests for agent_runtime.evidence_quality (rerank + grounding audit).

These inject a fake LLM (a str->str callable) so no network/keys are needed.
"""

from __future__ import annotations

import json

from agent_runtime.evidence_quality import audit_answer_grounding, rerank_documents


DOCS = [
    {"doc_id": "a", "title": "Irrelevant", "contents": "cooking recipes"},
    {"doc_id": "b", "title": "Floods", "contents": "Texas flood inundation maps and HAND model"},
    {"doc_id": "c", "title": "Tangential", "contents": "general hydrology background"},
]


def test_rerank_reorders_by_llm_scores():
    def fake_llm(prompt: str) -> str:
        return json.dumps(
            {"ranking": [
                {"doc_id": "b", "score": 0.95, "reason": "direct"},
                {"doc_id": "c", "score": 0.4, "reason": "background"},
                {"doc_id": "a", "score": 0.05, "reason": "off-topic"},
            ]}
        )

    out = rerank_documents("texas floods", DOCS, llm=fake_llm)
    assert [d["doc_id"] for d in out] == ["b", "c", "a"]


def test_rerank_respects_top_k_and_appends_omitted():
    def fake_llm(prompt: str) -> str:
        # Model only ranks two of three docs.
        return json.dumps({"ranking": [
            {"doc_id": "b", "score": 0.9},
            {"doc_id": "a", "score": 0.1},
        ]})

    out = rerank_documents("texas floods", DOCS, top_k=2, llm=fake_llm)
    assert [d["doc_id"] for d in out] == ["b", "a"]  # top_k applied, omitted 'c' would follow


def test_rerank_noops_on_single_or_bad_llm():
    assert rerank_documents("q", DOCS[:1], llm=lambda p: "garbage") == DOCS[:1]
    # Unparseable ranking -> original order preserved
    same = rerank_documents("q", DOCS, llm=lambda p: "not json")
    assert [d["doc_id"] for d in same] == ["a", "b", "c"]


def test_audit_returns_verdict():
    def fake_llm(prompt: str) -> str:
        return json.dumps({
            "hallucination_detected": True,
            "severity": "high",
            "issues": [{"claim": "X", "reason": "unsupported"}],
            "summary": "Answer adds unsupported claim X.",
        })

    verdict = audit_answer_grounding("q?", "answer with X", DOCS, llm=fake_llm)
    assert verdict["hallucination_detected"] is True
    assert verdict["severity"] == "high"
    assert verdict["issues"][0]["claim"] == "X"


def test_audit_insufficient_data_is_benign():
    verdict = audit_answer_grounding("q?", "", DOCS, llm=lambda p: "{}")
    assert verdict["hallucination_detected"] is False
    assert "Insufficient" in verdict["summary"]


def test_audit_handles_text_evidence_and_bad_json():
    verdict = audit_answer_grounding("q?", "some answer", "raw evidence text", llm=lambda p: "not json")
    assert verdict["hallucination_detected"] is False
    assert verdict["severity"] in {"none", "unknown"}


def test_quality_tools_parse_and_call(monkeypatch):
    import agent_runtime.evidence_quality as eq
    import agent_runtime.langchain_quality_tools as qt

    # make_quality_tools imports these from evidence_quality at call time, so
    # patch them at the source before building the tools (no real LLM).
    monkeypatch.setattr(eq, "rerank_documents", lambda q, docs, top_k=None, llm=None: list(reversed(docs)))
    monkeypatch.setattr(
        eq, "audit_answer_grounding",
        lambda question, answer, evidence, llm=None: {"hallucination_detected": False, "summary": "ok"},
    )
    tools = qt.make_quality_tools()
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"rerank_evidence", "audit_answer_grounding"}

    out = by_name["rerank_evidence"].invoke(
        {"query": "x", "documents_json": json.dumps(DOCS), "top_k": 3}
    )
    parsed = json.loads(out)
    assert parsed["count"] == 3 and parsed["reranked"][0]["doc_id"] == "c"  # reversed

    verdict = json.loads(
        by_name["audit_answer_grounding"].invoke(
            {"question": "q", "answer": "a", "evidence_json": json.dumps(DOCS)}
        )
    )
    assert verdict["summary"] == "ok"
