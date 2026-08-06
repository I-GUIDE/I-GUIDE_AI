"""Iterative search: retry with a REFORMULATED query, and a supervisor view rich enough to judge.

Before this, a second search re-ran the IDENTICAL query (so it could only return the identical
documents) and the supervisor saw only counts — 8 off-topic documents looked exactly like 8 good
ones.
"""

from __future__ import annotations

import json

from agent_runtime.supervisor_graph import run_supervisor
from agent_runtime.supervisor.graph import (
    _distill,
    _fallback_refinement,
    _focus_terms,
    _results_are_poor,
    _term_coverage,
)


def _doc(did, title, contents="", source="semantic", score=0.5):
    return {"doc_id": did, "title": title, "contents": contents, "source": source, "score": score}


# --- the supervisor's picture ---------------------------------------------------

def test_distill_shows_what_was_retrieved_not_just_how_much():
    docs = [_doc("a", "Illinois flood risk", "flooding in Illinois", "semantic", 0.9),
            _doc("b", "Kansas City Crime", "crime records", "keyword", 0.2)]
    d = _distill({"query": "datasets about flooding in Illinois", "evidence": docs,
                  "actions": ["search"], "search_attempts": 1,
                  "searched_queries": ["datasets about flooding in Illinois"]})
    assert d["evidence_titles"] == ["Illinois flood risk", "Kansas City Crime"]
    assert d["evidence_sources"] == {"semantic": 1, "keyword": 1}
    assert d["topical_coverage"] == 0.5        # half the set is off-topic — visible now
    assert d["top_score"] == 0.9
    assert d["queries_searched"] == ["datasets about flooding in Illinois"]
    assert "document_count" in d and d["document_count"] == 2


def test_distill_surfaces_peer_outcomes_and_artifacts():
    d = _distill({
        "query": "map it", "evidence": [],
        "analysis_results": {"summary": "Computed a true 25 km buffer with QGIS",
                             "steps": [{"result": {"managed_output": {
                                 "file_id": "f1", "filename": "map.png",
                                 "download_url": "/agent/files/f1/download"}}}]},
        "code_result": {"answer": "ran the script"}, "actions": ["analyze"]})
    assert d["analysis_summary"].startswith("Computed a true 25 km buffer")
    assert d["code_summary"] == "ran the script"
    assert d["artifacts_produced"] == ["map.png"]


def test_topical_coverage_semantics():
    assert _focus_terms("find any datasets about flooding in Illinois") == ["flooding", "illinois"]
    assert _term_coverage([], "flooding") is None                     # nothing to judge
    assert _term_coverage([_doc("a", "x")], "find the data") is None  # query is pure filler
    assert _term_coverage([_doc("a", "Illinois flooding")], "flooding illinois") == 1.0


# --- refinement -----------------------------------------------------------------

def test_results_are_poor_detection():
    assert _results_are_poor([], "flooding in Illinois")                                # empty
    assert _results_are_poor([_doc("a", "Kansas City Crime")], "flooding in Illinois")  # off topic
    assert not _results_are_poor([_doc("a", "Illinois flooding")], "flooding in Illinois")


def test_fallback_refinement_narrows_then_broadens():
    q = "find any datasets about flooding in Illinois"
    first = _fallback_refinement(q, [q])
    assert first == "flooding illinois"                       # filler stripped
    assert _fallback_refinement("flooding illinois", ["flooding illinois"]) is None


def test_refinement_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_SEARCH_REFINE", raising=False)
    queries = []

    def search_fn(q, s):
        queries.append(q)
        return []
    run_supervisor("find any datasets about flooding in Illinois", llm=lambda p: "x",
                   decide_fn=lambda s, d: "done", search_fn=search_fn,
                   synthesize_fn=lambda *a: "ans", do_rerank=False, do_audit=False)
    assert queries == []          # decider went straight to done; no implicit search


def test_refinement_retries_with_a_different_query(monkeypatch):
    """An off-topic first result triggers ONE retry with a reformulated query, and the
    on-topic hits are merged in."""
    monkeypatch.setenv("AGENT_SEARCH_REFINE", "1")
    monkeypatch.setenv("AGENT_SEARCH_REFINE_MAX", "1")
    queries = []

    def search_fn(q, s):
        queries.append(q)
        if len(queries) == 1:
            return [_doc("off", "Kansas City Crime Summary", "crime")]
        return [_doc("hit", "Illinois flooding dataset", "flooding in Illinois")]

    state = run_supervisor("find any datasets about flooding in Illinois", llm=None,
                           decide_fn=_scripted_once(), search_fn=search_fn,
                           synthesize_fn=lambda *a: "ans", do_rerank=False, do_audit=False)
    assert len(queries) == 2                       # retried
    assert queries[1] != queries[0]                # with a DIFFERENT query
    assert queries[1] == "flooding illinois"       # deterministic fallback (llm=None)
    ids = {d["doc_id"] for d in state["evidence"]}
    assert ids == {"off", "hit"}                   # both result sets merged
    assert state["searched_queries"] == queries    # recorded for the supervisor


def test_refinement_stops_when_results_are_good(monkeypatch):
    monkeypatch.setenv("AGENT_SEARCH_REFINE", "1")
    queries = []

    def search_fn(q, s):
        queries.append(q)
        return [_doc("hit", "Illinois flooding dataset", "flooding in Illinois")]
    run_supervisor("datasets about flooding in Illinois", llm=None, decide_fn=_scripted_once(),
                   search_fn=search_fn, synthesize_fn=lambda *a: "ans",
                   do_rerank=False, do_audit=False)
    assert queries == ["datasets about flooding in Illinois"]     # no pointless retry


def test_llm_written_refinement_is_used(monkeypatch):
    monkeypatch.setenv("AGENT_SEARCH_REFINE", "1")
    queries = []

    def llm(prompt: str) -> str:
        if "refining a search query" in prompt.lower():
            return "levee failure inundation"
        return "x"

    def search_fn(q, s):
        queries.append(q)
        return [] if len(queries) == 1 else [_doc("h", "Levee failure inundation model")]

    run_supervisor("find any datasets about flooding in Illinois", llm=llm,
                   decide_fn=_scripted_once(), search_fn=search_fn,
                   synthesize_fn=lambda *a: "ans", do_rerank=False, do_audit=False)
    assert queries[1] == "levee failure inundation"     # the model's reformulation, not the fallback


def _scripted_once():
    box = {"i": 0}

    def decide(state, distilled):
        box["i"] += 1
        return "search" if box["i"] == 1 else "done"
    return decide
