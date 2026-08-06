"""Coverage floor: a retrieval method the QUERY implies must run even if the LLM skipped it.

Observed live: "satellite imagery of wildfires in California" used neither spatial_search nor
opengeodata_search, though it names a place and asks for external imagery.
"""

from __future__ import annotations

from agent_runtime.supervisor.graph import (
    _direct_search_sweep,
    _mentions_place,
    _wants_external_data,
)


def test_place_detection():
    for q in ("satellite imagery of wildfires in California", "flood data for Illinois counties",
              "census data for Cook County", "elevation model of the Amazon basin",
              "dams along the Mississippi river"):
        assert _mentions_place(q), q
    for q in ("datasets about flooding", "how do I compute a buffer in python",
              "notebooks by Shaowen Wang", "papers by Wang in Nature",   # venue, not a place
              "what is a shapefile"):
        assert not _mentions_place(q), q


def test_external_data_detection():
    for q in ("satellite imagery of wildfires", "lidar elevation data", "census tracts for Chicago",
              "climate reanalysis data", "open data about dams", "NASA earth observation products"):
        assert _wants_external_data(q), q
    for q in ("notebooks by Shaowen Wang", "what is a shapefile",
              "most popular knowledge elements", "explain this dataset"):
        assert not _wants_external_data(q), q


def test_sweep_adds_implied_methods(monkeypatch):
    """The floor runs spatial + opengeodata when the query implies them — on top of the
    always-on keyword/semantic pair."""
    import rag_pipeline.search.keyword as kw
    import rag_pipeline.search.semantic as sem
    import rag_pipeline.search.spatial as sp
    import rag_pipeline.search.opengeodata as ogd

    def hit(did, etype="dataset", **extra):
        src = {"title": did, "element_type": etype, "contents": "x"}
        src.update(extra)
        return {"_id": did, "_score": 1.0, "_source": src}

    monkeypatch.setattr(kw, "get_keyword_search_results", lambda q, size=8: [hit("k1")])
    monkeypatch.setattr(sem, "semantic_search", lambda q, size=8: [hit("s1")])
    monkeypatch.setattr(sp, "get_spatial_search_results", lambda q, size=8: [hit("sp1")])
    monkeypatch.setattr(ogd, "get_opengeodata_results", lambda q, limit=8, session_ctx=None: [
        hit("og1", etype="opengeodata", url="https://cmr.earthdata.nasa.gov/x")])

    docs = _direct_search_sweep("satellite imagery of wildfires in California", None)
    by_source = {d["doc_id"]: d.get("source") for d in docs}
    assert set(by_source) == {"k1", "s1", "sp1", "og1"}          # all four methods contributed
    og = [d for d in docs if d["doc_id"] == "og1"][0]
    assert og["url"] == "https://cmr.earthdata.nasa.gov/x"        # external link preserved


def test_sweep_skips_unimplied_methods(monkeypatch):
    """A plain topical query keeps the cheap keyword+semantic floor only — no spurious calls."""
    import rag_pipeline.search.keyword as kw
    import rag_pipeline.search.semantic as sem
    import rag_pipeline.search.spatial as sp
    import rag_pipeline.search.opengeodata as ogd
    called = {"spatial": 0, "ogd": 0}

    monkeypatch.setattr(kw, "get_keyword_search_results", lambda q, size=8: [])
    monkeypatch.setattr(sem, "semantic_search", lambda q, size=8: [])
    monkeypatch.setattr(sp, "get_spatial_search_results",
                        lambda q, size=8: called.__setitem__("spatial", called["spatial"] + 1) or [])
    monkeypatch.setattr(ogd, "get_opengeodata_results",
                        lambda q, limit=8, session_ctx=None: called.__setitem__("ogd", called["ogd"] + 1) or [])
    _direct_search_sweep("what is a shapefile", None)
    assert called == {"spatial": 0, "ogd": 0}


def test_allowlist_still_wins(monkeypatch):
    """A client allowlist bounds the floor: implied methods it excludes are not run."""
    import rag_pipeline.search.keyword as kw
    import rag_pipeline.search.semantic as sem
    import rag_pipeline.search.spatial as sp
    import rag_pipeline.search.opengeodata as ogd
    seen = []
    monkeypatch.setattr(kw, "get_keyword_search_results", lambda q, size=8: seen.append("kw") or [])
    monkeypatch.setattr(sem, "semantic_search", lambda q, size=8: seen.append("sem") or [])
    monkeypatch.setattr(sp, "get_spatial_search_results", lambda q, size=8: seen.append("sp") or [])
    monkeypatch.setattr(ogd, "get_opengeodata_results",
                        lambda q, limit=8, session_ctx=None: seen.append("ogd") or [])
    _direct_search_sweep("satellite imagery in California", ["keyword_search", "semantic_search"])
    assert seen == ["kw", "sem"]
