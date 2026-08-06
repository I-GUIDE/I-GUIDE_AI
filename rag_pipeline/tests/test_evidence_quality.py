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


# --- cross-source ordering when rerank is unavailable ------------------------------
# The defect: rerank failure returned the merged SWEEP order (arbitrary across sources) and skipped
# top_k entirely, because truncation only happened on the success path. With scores that are not
# commensurable across sources — BM25 ~4-9 next to the catalog scorer's ~0-1 — that meant the
# retained evidence depended on merge position, not relevance.

MIXED = [
    {"doc_id": "nid",   "source": "keyword",     "score": 8.584, "title": "National Inventory of Dams"},
    {"doc_id": "fm1",   "source": "keyword",     "score": 4.910, "title": "Flood Risk Map: Madison"},
    {"doc_id": "fm2",   "source": "keyword",     "score": 4.910, "title": "Flood Risk Map: Monroe"},
    {"doc_id": "fm3",   "source": "keyword",     "score": 4.910, "title": "Flood Risk Map: Clinton"},
    {"doc_id": "lock",  "source": "opengeodata", "score": 1.000, "title": "Lock and dam conditions"},
    {"doc_id": "major", "source": "opengeodata", "score": 0.631, "title": "Major Dams in the US"},
    {"doc_id": "grand", "source": "opengeodata", "score": 0.500, "title": "GRanDv1: Dams"},
    {"doc_id": "gres",  "source": "opengeodata", "score": 0.431, "title": "GRanDv1: Reservoirs"},
]


def _boom(_prompt):
    raise RuntimeError("inference endpoint unreachable")


def test_rrf_interleaves_sources_by_rank_not_by_score_magnitude():
    from agent_runtime.evidence_quality import rrf_order

    order = [d["doc_id"] for d in rrf_order(MIXED)]
    # Each source's own #1 leads, then each #2, and so on — the catalog hit reaches position 2
    # despite scoring 1.0 against a BM25 8.584 it cannot be compared with.
    assert order == ["nid", "lock", "fm1", "major", "fm2", "grand", "fm3", "gres"]


def test_rrf_keeps_catalog_results_that_truncation_used_to_discard():
    """The concrete consequence: in merge order every catalog hit sat at position 5-8, so a
    top_k of 4 dropped all of them."""
    kept = [d["source"] for d in rerank_documents("dams in Illinois", MIXED, top_k=4, llm=_boom)]
    assert kept.count("opengeodata") == 2          # was 0 before
    assert len(kept) == 4                          # and top_k is honoured at all


def test_rerank_failure_truncates_to_top_k():
    """top_k used to be applied ONLY when the model answered, so a failure returned everything."""
    assert len(rerank_documents("q", MIXED, top_k=3, llm=_boom)) == 3
    assert len(rerank_documents("q", MIXED, top_k=3, llm=lambda p: "not json")) == 3


def test_rerank_failure_is_logged_not_silent(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="agent_runtime.evidence_quality"):
        rerank_documents("q", MIXED, top_k=4, llm=_boom)
    assert "falling back to reciprocal rank fusion" in caplog.text
    assert "inference endpoint unreachable" in caplog.text      # the CAUSE, not just the symptom


def test_a_working_rerank_still_decides_the_order():
    """RRF is a fallback only — it must never override a model that answered."""
    def good(_prompt):
        return json.dumps({"ranking": [{"doc_id": "major"}, {"doc_id": "nid"}]})

    out = [d["doc_id"] for d in rerank_documents("dams", MIXED, top_k=3, llm=good)]
    assert out[:2] == ["major", "nid"]


def test_rrf_preserves_a_single_sources_own_order():
    from agent_runtime.evidence_quality import rrf_order

    one = [d for d in MIXED if d["source"] == "opengeodata"]
    assert [d["doc_id"] for d in rrf_order(one)] == ["lock", "major", "grand", "gres"]


def test_rrf_preserves_order_when_a_source_reports_no_scores():
    from agent_runtime.evidence_quality import rrf_order

    docs = [{"doc_id": f"w{i}", "source": "web", "title": f"page {i}"} for i in range(4)]
    assert [d["doc_id"] for d in rrf_order(docs)] == ["w0", "w1", "w2", "w3"]


def test_rrf_reads_the_nested_document_shape():
    """Evidence entries arrive as {"document": {...}} on the supervisor path."""
    from agent_runtime.evidence_quality import rrf_order

    docs = [
        {"document": {"doc_id": "k1", "source": "keyword", "score": 9.0}},
        {"document": {"doc_id": "o1", "source": "opengeodata", "score": 0.9}},
        {"document": {"doc_id": "k2", "source": "keyword", "score": 8.0}},
    ]
    assert [d["document"]["doc_id"] for d in rrf_order(docs)] == ["k1", "o1", "k2"]


def test_rrf_tolerates_junk_entries():
    from agent_runtime.evidence_quality import rrf_order

    docs = ["a bare string", {"doc_id": "x", "source": "web", "score": "not a number"}, None]
    assert len(rrf_order(docs)) == 3          # never raises, never drops


# --- the audit must DISCRIMINATE -------------------------------------------------
# Reported: a correct, fully-grounded answer was flagged "multiple high-severity hallucinations".
# Measuring it found something worse than a false positive — the same answer with a fabricated
# journal, date, institution, benchmark score, price and adopter appended got the SAME verdict, and
# the flagged claims were the LEGITIMATE ones in both cases. A caveat that says "high" either way
# carries no information. These tests pin the structure that fixed it; the wording itself was chosen
# by measurement against a correct/fabricated pair and is documented above the prompt.


def test_the_ledger_is_emitted_before_the_verdict():
    """The original schema put hallucination_detected FIRST, so the model committed to a verdict
    autoregressively and then backfilled rationalisations for it."""
    from agent_runtime.evidence_quality import _AUDIT_PROMPT

    schema = _AUDIT_PROMPT[_AUDIT_PROMPT.index("Respond ONLY with JSON"):]
    assert schema.index("claim_ledger") < schema.index("hallucination_detected")


def test_the_prompt_demands_a_verbatim_span_per_claim():
    """Nothing previously forced the model to LOOK for support, so it asserted "not directly
    supported" about claims stated almost verbatim in the evidence."""
    from agent_runtime.evidence_quality import _AUDIT_PROMPT

    assert "evidence_quote" in _AUDIT_PROMPT
    assert "VERBATIM" in _AUDIT_PROMPT
    assert "paraphrase" in _AUDIT_PROMPT.lower()


def test_specifics_must_be_decomposed_into_their_own_rows():
    """An invented figure inside an otherwise-supported sentence was summarised into one
    'supported' row and passed clean."""
    from agent_runtime.evidence_quality import _AUDIT_PROMPT

    assert "DECOMPOSE SPECIFICS" in _AUDIT_PROMPT


def test_execution_record_remains_first_class_grounding():
    """Kept from the previous prompt: a genuinely produced map/file/count grounds an answer even
    when no retrieved document mentions it, or every QGIS and code answer gets flagged."""
    from agent_runtime.evidence_quality import _AUDIT_PROMPT

    assert "execution record" in _AUDIT_PROMPT
    assert "FIRST-CLASS" in _AUDIT_PROMPT


def test_the_audit_window_matches_the_synthesizers():
    """The auditor was shown 5 docs x 600 chars while the synthesizer wrote from 8 x 2500 — strictly
    less evidence than the writer, then asked whether the writer invented things."""
    from agent_runtime.evidence_quality import _audit_window
    from agent_runtime.supervisor.evidence_subgraph import _format_documents
    import inspect

    sig = inspect.signature(_format_documents)
    assert _audit_window() == (sig.parameters["limit"].default,
                               sig.parameters["max_chars"].default)


def test_the_audit_window_is_env_tunable(monkeypatch):
    from agent_runtime.evidence_quality import _audit_window

    monkeypatch.setenv("AGENT_AUDIT_DOC_LIMIT", "3")
    monkeypatch.setenv("AGENT_AUDIT_DOC_CHARS", "900")
    assert _audit_window() == (3, 900)
    monkeypatch.setenv("AGENT_AUDIT_DOC_CHARS", "not-a-number")
    assert _audit_window()[1] == 2500          # never silently collapses to a tiny window


def test_a_ledger_of_all_supported_rows_yields_a_clean_verdict():
    """The verdict must be readable off the ledger, so a fake LLM returning an all-supported ledger
    produces severity none — no hidden path back to a flag."""
    def fake_llm(_prompt):
        return json.dumps({
            "claim_ledger": [{"claim": "x", "evidence_quote": "[d1] a real span", "status": "supported"}],
            "hallucination_detected": False, "severity": "none", "issues": [],
            "summary": "grounded",
        })

    v = audit_answer_grounding("q", "an answer", DOCS, llm=fake_llm)
    assert v["hallucination_detected"] is False and v["severity"] == "none" and v["issues"] == []
