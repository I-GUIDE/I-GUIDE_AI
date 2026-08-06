"""OpenGeoData relevance gating: unrelated catalog hits must be dropped, not merely ranked last.

Reported symptom: "the overall search results do not fit the question" — e.g. "Kansas City Crime
(NIBRS) Summary" and "Procurement Contracts" surfaced for queries about I-GUIDE knowledge
elements. Causes: medium words ("geospatial datasets") matched nearly every record and dominated
the query; substring matching produced incidental hits; and nothing was ever discarded.
"""

from __future__ import annotations

import pytest

import rag_pipeline.search.opengeodata_new as ND


def _asset(title="", abstract="", keywords=None, bbox=None):
    return ND.GeoAsset(id="x", title=title, abstract=abstract, keywords=keywords or [],
                       bbox=bbox, datetime=None, license=None, links={}, source="test",
                       provider=None)


def test_meaningful_terms_strips_filler_and_medium_words():
    assert ND.meaningful_terms("Find open geospatial datasets about dams in Illinois") == ["dams", "illinois"]
    assert ND.meaningful_terms("show me the map layers") == []          # all filler
    assert ND.meaningful_terms("urban heat exposure") == ["urban", "heat", "exposure"]


def test_focus_query_sends_the_subject_to_the_catalogs():
    assert ND.focus_query("geospatial datasets dams") == "dams"
    # a query of pure filler falls back to the original rather than searching for nothing
    assert ND.focus_query("open geospatial data map") == "open geospatial data map"


def test_single_term_query_needs_title_or_abstract_evidence():
    assert ND.is_relevant(_asset(title="Major Dams in the United States"), ["dams"])
    assert ND.is_relevant(_asset(title="Inundation Extents", abstract="behind dams"), ["dams"])
    # keyword-list-only match never suffices (catalog tag vocabularies are generic)
    assert not ND.is_relevant(_asset(title="Generic Records", keywords=["dams"]), ["dams"])


def test_multi_term_query_needs_a_title_hit_or_two_distinct_terms():
    # one incidental word in a long abstract is not relevance
    assert not ND.is_relevant(_asset(title="Procurement Contracts",
                                     abstract="a smart initiative"), ["i-guide", "smart"])
    # two distinct terms in the abstract is enough (a real Baltimore heat study)
    assert ND.is_relevant(_asset(title="Baltimore Supersite",
                                 abstract="urban heat exposure study"),
                          ["urban", "heat", "exposure"])
    # a title hit alone is enough
    assert ND.is_relevant(_asset(title="Urban Heat Islands"), ["urban", "heat", "exposure"])


def test_word_boundary_matching_rejects_substring_hits():
    assert not ND.is_relevant(_asset(title="Damsel Fly Survey", abstract="insects"), ["dams"])
    assert ND.is_relevant(_asset(title="Dam Safety"), ["dams"])          # plural/singular tolerated


def test_no_meaningful_terms_disables_filtering():
    a = _asset(title="Anything", abstract="unrelated")
    assert ND.is_relevant(a, [])                                        # cannot judge -> keep


def test_bbox_conflicts_only_when_both_extents_known():
    illinois = (-91.5, 36.97, -87.0, 42.5)
    kansas_city = (-94.8, 38.8, -94.4, 39.3)
    assert ND.bbox_conflicts(kansas_city, illinois)                     # disjoint -> drop
    assert not ND.bbox_conflicts((-89.0, 40.0, -88.0, 41.0), illinois)  # inside -> keep
    assert not ND.bbox_conflicts(None, illinois)                        # global dataset -> keep
    assert not ND.bbox_conflicts(kansas_city, None)                     # no place asked -> keep
    assert not ND.bbox_conflicts("bad", illinois)                       # unparseable -> keep


def test_run_opengeodata_reports_and_applies_the_gate(monkeypatch):
    """The pipeline searches the SUBJECT, drops non-matching/out-of-area hits, and says how many."""
    illinois = [-91.5, 36.97, -87.0, 42.5]
    captured = {}

    def fake_discover(query, bbox=None, time_range=None, limit=6, providers=None):
        captured["query"] = query
        return [
            _asset(title="Major Dams in the United States", abstract="all known dams"),
            _asset(title="Kansas City Crime Summary", abstract="incidents",
                   bbox=(-94.8, 38.8, -94.4, 39.3)),          # out of area -> dropped
            _asset(title="Procurement Contracts", abstract="a smart initiative"),  # no overlap
        ]
    monkeypatch.setattr(ND, "discover", fake_discover)

    res = ND.run_opengeodata(query="geospatial datasets dams", call_llm=0, limit=5, bbox=illinois)
    assert captured["query"] == "dams"                          # medium words stripped
    assert res["search_query"] == "dams"
    assert [a["title"] for a in res["assets"]] == ["Major Dams in the United States"]
    assert res["candidates_found"] == 3 and res["filtered_out"] == 2 and res["count"] == 1


def test_gate_can_return_zero_results(monkeypatch):
    """No match is an honest answer — better than filler the user must sift through."""
    monkeypatch.setattr(ND, "discover",
                        lambda *a, **k: [_asset(title="Procurement Contracts", abstract="smart")])
    res = ND.run_opengeodata(query="I-GUIDE Smart Search", call_llm=0, limit=5)
    assert res["count"] == 0 and res["assets"] == [] and res["filtered_out"] == 1
