"""Unlisted elements (visibility private / legacy 1) must never surface in search results.

Covers the shared is_public_visibility helper, the visibility predicate + param on every
tier-1 pattern query, and the post-filter at each hit-normalization chokepoint (which also
guards Text2Cypher-generated queries that carry no predicate).
"""

from __future__ import annotations

from rag_pipeline.search.neo4j_graph_tools import build_tool_query, is_public_visibility


def test_is_public_visibility_semantics():
    for v in ("public", "PUBLIC", 10, "10", None, ""):
        assert is_public_visibility(v), v          # public (string or legacy numeric) / absent
    for v in ("private", 1, "1", "unlisted", 99, "internal"):
        assert not is_public_visibility(v), v      # anything else = unlisted -> hidden


def test_every_pattern_query_filters_visibility():
    cases = [
        ("by_author", {"name": "Jane Doe"}),
        ("by_organization", {"org": "UIUC"}),
        ("by_tag", {"tag": "flood"}),
        ("by_resource_type", {"rtype": "datasets"}),
        ("related_to", {"title": "dams"}),
        ("in_collection", {"collection": "hydrology"}),
        ("by_popularity", {"rtype": ""}),
    ]
    for name, captured in cases:
        cypher, params = build_tool_query(name, captured, limit=10)
        assert "toString(r.visibility) IN $public_visibilities" in cypher, name
        assert params["public_visibilities"] == ["public", "10"], name


def test_records_to_hits_drops_unlisted():
    from rag_pipeline.search.neo4j import _records_to_hits
    records = [
        {"doc_id": "pub1", "title": "Public", "visibility": "public", "score": 1.0},
        {"doc_id": "unl1", "title": "Unlisted", "visibility": "private", "score": 2.0},
        {"doc_id": "leg1", "title": "Legacy numeric", "visibility": 1, "score": 3.0},
        {"doc_id": "nov1", "title": "No visibility field", "score": 0.5},
    ]
    ids = [h["_id"] for h in _records_to_hits(records)]
    assert ids == ["pub1", "nov1"]


def test_normalize_hits_drops_unlisted():
    from agent_runtime.langchain_granular_tools import _normalize_hits
    hits = [
        {"_id": "a", "_score": 1.0, "_source": {"title": "Pub", "visibility": "public"}},
        {"_id": "b", "_score": 1.0, "_source": {"title": "Unl", "visibility": "private"}},
        {"_id": "c", "_score": 1.0, "_source": {"title": "NoVis"}},
    ]
    ids = [d["doc_id"] for d in _normalize_hits(hits, "keyword")]
    assert ids == ["a", "c"]


def test_direct_search_sweep_drops_unlisted(monkeypatch):
    import rag_pipeline.search.keyword as kw
    import rag_pipeline.search.semantic as sem
    from agent_runtime.supervisor.graph import _direct_search_sweep
    monkeypatch.setattr(kw, "get_keyword_search_results", lambda q, size=8: [
        {"_id": "pub", "_score": 1.0, "_source": {"title": "P", "visibility": "public", "contents": "x"}},
        {"_id": "unl", "_score": 9.0, "_source": {"title": "U", "visibility": 1, "contents": "y"}},
    ])
    monkeypatch.setattr(sem, "semantic_search", lambda q, size=8: [])
    assert [d["doc_id"] for d in _direct_search_sweep("q", None)] == ["pub"]


def test_node_to_hit_drops_unlisted():
    from rag_pipeline.search.agents import _rows_to_hits
    rows = [
        {"score": 1.0, "node": None, "doc_id": "row-pub", "title": "P", "visibility": "public"},
        {"score": 1.0, "node": None, "doc_id": "row-unl", "title": "U", "visibility": "private"},
    ]
    ids = [h["_id"] for h in _rows_to_hits(rows)]
    assert "row-unl" not in " ".join(ids)
    assert any("row-pub" in i for i in ids)
