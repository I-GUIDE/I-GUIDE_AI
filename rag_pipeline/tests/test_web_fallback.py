"""Last-resort open-web fallback: consult the web only when the I-GUIDE platform found nothing.

The web is otherwise LLM-elected and never part of the deterministic every-turn sweep, because it is
a third-party network hop. This fallback is the single exception, and its whole value depends on the
trigger being narrow: it must fire when the knowledge base returns nothing, and must NOT fire when
the KB returned anything at all, when the request excluded web_search, or when web access is off.
"""

from __future__ import annotations

import pytest

import agent_runtime.supervisor.graph as G
import rag_pipeline.search.web_utils as WU


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for var in ("AGENT_WEB_ENABLED", "AGENT_WEB_FALLBACK", "AGENT_WEB_MAX_SEARCHES_PER_TURN",
                "AGENT_WEB_MAX_FETCHES_PER_TURN"):
        monkeypatch.delenv(var, raising=False)
    WU.begin_turn()


def _platform_doc(source="keyword"):
    return {"doc_id": "kb-1", "source": source, "element_type": "dataset",
            "title": "Illinois Dam Inventory", "contents": "…", "url": ""}


def _catalog_doc():
    return {"doc_id": "ogd-1", "source": "opengeodata", "element_type": "opengeodata",
            "title": "Major Dams", "contents": "…", "url": "https://example.org/dams"}


def _web_doc():
    return {"doc_id": "web-1", "source": "web", "element_type": "web",
            "title": "Dam Safety", "contents": "…", "url": "https://example.com/dams"}


# --- what counts as platform evidence --------------------------------------------


def test_platform_evidence_is_recognized():
    assert G._has_platform_evidence([_platform_doc()]) is True
    assert G._has_platform_evidence([_platform_doc("semantic")]) is True
    assert G._has_platform_evidence([{"document": _platform_doc("neo4j")}]) is True


def test_external_results_are_not_platform_evidence():
    """A catalog or web hit answers the question, but it is not OUR holdings — the fallback's
    trigger is specifically 'the platform had nothing'."""
    assert G._has_platform_evidence([_catalog_doc()]) is False
    assert G._has_platform_evidence([_web_doc()]) is False
    assert G._has_platform_evidence([_catalog_doc(), _web_doc()]) is False
    assert G._has_platform_evidence([]) is False


def test_junk_entries_do_not_count_as_evidence():
    assert G._has_platform_evidence(["a string", None, 7]) is False


# --- when the fallback runs -------------------------------------------------------


def _stub_web(monkeypatch, *, results=2, error=None):
    """Stub the web search + fetch the fallback calls, recording invocations."""
    calls = {"search": 0, "fetch": []}

    def fake_run_web_search(query, *, limit=6, recency_days=None):
        calls["search"] += 1
        if error:
            return {"query": query, "error": error, "count": 0, "results": []}
        return {"query": query, "count": results, "provider": "stub",
                "results": [{"doc_id": f"web-{i}", "title": f"Page {i}", "snippet": "short snippet",
                             "url": f"https://example.com/p{i}", "source": "web"}
                            for i in range(results)]}

    def fake_results_to_hits(result):
        return [{"_id": r["doc_id"], "_score": 1.0,
                 "_source": {"doc_id": r["doc_id"], "title": r["title"], "contents": r["snippet"],
                             "url": r["url"], "element_type": "web", "source": "web",
                             "visibility": "public"}}
                for r in result.get("results", [])]

    def fake_fetch(url, *, focus=None):
        calls["fetch"].append(url)
        return {"url": url, "text": "THE FULL EXTRACTED PAGE TEXT", "chars": 27}

    import rag_pipeline.search.web as W
    import rag_pipeline.search.web_fetch as WF

    monkeypatch.setattr(W, "run_web_search", fake_run_web_search)
    monkeypatch.setattr(W, "results_to_hits", fake_results_to_hits)
    monkeypatch.setattr(WF, "fetch_and_extract", fake_fetch)
    return calls


def test_fallback_returns_web_docs_when_permitted(monkeypatch):
    calls = _stub_web(monkeypatch)
    docs = G._web_fallback_evidence("who regulates dams in Illinois", None)
    assert len(docs) == 2 and calls["search"] == 1
    assert all(d["element_type"] == "web" for d in docs)


def test_fallback_reads_the_top_page_rather_than_relying_on_snippets(monkeypatch):
    """On this path documents go straight into evidence — the synthesizer never gets to call
    web_fetch — so without a fetch the whole answer would rest on 300-char snippets."""
    calls = _stub_web(monkeypatch)
    docs = G._web_fallback_evidence("q", None)
    assert calls["fetch"] == ["https://example.com/p0"]        # exactly the top result, once
    top = next(d for d in docs if d["url"] == "https://example.com/p0")
    assert top["contents"] == "THE FULL EXTRACTED PAGE TEXT"
    others = [d for d in docs if d["url"] != "https://example.com/p0"]
    assert all(d["contents"] == "short snippet" for d in others)


def test_fallback_is_skipped_when_the_request_excluded_web_search(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not touch the web when web_search is not in the allowlist")

    import rag_pipeline.search.web as W
    monkeypatch.setattr(W, "run_web_search", boom)
    assert G._web_fallback_evidence("q", ["keyword_search", "semantic_search"]) == []


def test_fallback_is_skipped_when_web_access_is_disabled(monkeypatch):
    _stub_web(monkeypatch)
    monkeypatch.setenv("AGENT_WEB_ENABLED", "false")
    assert G._web_fallback_evidence("q", None) == []


def test_fallback_has_its_own_kill_switch(monkeypatch):
    _stub_web(monkeypatch)
    monkeypatch.setenv("AGENT_WEB_FALLBACK", "off")
    assert G._web_fallback_evidence("q", None) == []
    monkeypatch.setenv("AGENT_WEB_FALLBACK", "true")
    assert len(G._web_fallback_evidence("q", None)) == 2


def test_a_provider_error_yields_nothing_rather_than_raising(monkeypatch):
    _stub_web(monkeypatch, error="web search provider unavailable: boom")
    assert G._web_fallback_evidence("q", None) == []


def test_fallback_never_raises(monkeypatch):
    import rag_pipeline.search.web as W

    def explode(*a, **k):
        raise RuntimeError("network on fire")

    monkeypatch.setattr(W, "run_web_search", explode)
    assert G._web_fallback_evidence("q", None) == []


def test_the_every_turn_sweep_still_never_touches_the_web(monkeypatch):
    """The fallback must not have quietly become an every-turn web call."""
    import rag_pipeline.search.keyword as KW
    import rag_pipeline.search.opengeodata as OGD
    import rag_pipeline.search.semantic as SEM
    import rag_pipeline.search.spatial as SP
    import rag_pipeline.search.web as W

    def boom(*a, **k):
        raise AssertionError("the direct sweep must never touch the open web")

    monkeypatch.setattr(W, "run_web_search", boom)
    # Every in-house method stubbed, so the assertion is about the WEB and the test needs no network.
    monkeypatch.setattr(KW, "get_keyword_search_results", lambda *a, **k: [])
    monkeypatch.setattr(SEM, "semantic_search", lambda *a, **k: [])
    monkeypatch.setattr(SP, "get_spatial_search_results", lambda *a, **k: [])
    monkeypatch.setattr(OGD, "get_opengeodata_results", lambda *a, **k: [])

    assert G._direct_search_sweep("dams in Illinois", None) == []


def test_an_unrecognized_document_counts_as_platform_evidence():
    """Fail closed. An earlier version keyed on a positive list of platform source names, so any
    document lacking a `source` field read as "nothing found" and sent the turn to the web even
    though evidence existed — which broke three supervisor tests whose fixtures are bare dicts."""
    assert G._has_platform_evidence([{"doc_id": "d1", "title": "t", "contents": "c"}]) is True
    assert G._has_platform_evidence([{"doc_id": "d1", "source": "some_new_method"}]) is True


@pytest.mark.parametrize("query", [
    "find datasets about q on I-GUIDE",
    "what knowledge elements does I-GUIDE have about urban heat islands",
    "what are the related elements of 5e9c7566-1234-4321-8888-abcdefabcdef",
    "what are the most popular datasets",
])
def test_fallback_does_not_fire_for_questions_about_the_platform(monkeypatch, query):
    """Only I-GUIDE knows what I-GUIDE contains. For a question about our own holdings an empty
    result IS the answer — substituting web pages would dress up a miss as a hit."""
    def boom(*a, **k):
        raise AssertionError(f"must not consult the web for a platform question: {query!r}")

    import rag_pipeline.search.web as W
    monkeypatch.setattr(W, "run_web_search", boom)
    assert G._web_fallback_evidence(query, None) == []


@pytest.mark.parametrize("query", [
    "who regulates dam safety inspections in Illinois",
    "what is the current version of the OGC API Features standard",
    "recent news about reservoir sedimentation",
])
def test_fallback_does_fire_for_general_questions(monkeypatch, query):
    calls = _stub_web(monkeypatch)
    assert len(G._web_fallback_evidence(query, None)) == 2
    assert calls["search"] == 1


# --- "nothing useful", not merely "nothing at all" ---------------------------------
# Reported: "explain training-free gpro" answered "the provided evidence does not include specific
# resources ... explaining training-free GPRO" with no web search. Keyword search is a nearest-match
# engine — it returns its eight closest documents for ANY query — so an unknown subject comes back
# with a FULL result set that mentions none of it. A trigger keyed on "no evidence at all" never
# fires for that, which is the case the fallback is most needed for.


def _offtopic(n=8):
    return [{"doc_id": f"kb-{i}", "source": "keyword", "element_type": "dataset",
             "title": f"Flood Risk Map: County {i}",
             "contents": "FEMA flood hazard mapping for a county in Illinois."} for i in range(n)]


def _ontopic():
    return [{"doc_id": "kb-1", "source": "keyword", "element_type": "publication",
             "title": "Training-free GRPO for policy optimization",
             "contents": "A training-free variant of group relative policy optimization."}]


def test_a_full_but_off_topic_result_set_counts_as_unhelpful():
    docs = _offtopic()
    assert G._has_platform_evidence(docs) is True          # there IS evidence ...
    assert G._platform_evidence_is_unhelpful(docs, "explain training-free gpro") is True   # ... but it is useless


def test_on_topic_platform_evidence_is_helpful():
    assert G._platform_evidence_is_unhelpful(_ontopic(), "explain training-free grpo") is False


def test_no_platform_evidence_is_still_unhelpful():
    assert G._platform_evidence_is_unhelpful([], "explain training-free gpro") is True
    assert G._platform_evidence_is_unhelpful([_web_doc(), _catalog_doc()],
                                            "explain training-free gpro") is True


def test_external_hits_do_not_rescue_an_off_topic_platform_result():
    """Catalog hits are not platform evidence, so they neither satisfy nor block the judgement."""
    docs = _offtopic() + [_catalog_doc()]
    assert G._platform_evidence_is_unhelpful(docs, "explain training-free gpro") is True


def test_the_reported_query_reaches_the_web(monkeypatch):
    """End to end over the gates: the reported question must not be classed as a platform question
    and must survive the unhelpful-evidence check."""
    calls = _stub_web(monkeypatch)
    q = "explain training-free gpro"
    assert G._asks_about_platform_holdings(q) is False
    assert G._platform_evidence_is_unhelpful(_offtopic(), q) is True
    assert len(G._web_fallback_evidence(q, None)) == 2 and calls["search"] == 1
