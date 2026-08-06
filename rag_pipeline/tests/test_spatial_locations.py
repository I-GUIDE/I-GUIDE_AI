"""Place extraction for spatial_search: the step that decided the method returned nothing.

Observed on the deployed agent: `spatial_search` reported 0 results for both place-scoped questions
in a five-question geospatial run, while keyword and semantic search returned 8 each. Neither cause
was in the OpenSearch query — the index has the geo_shape field mapped and 181 of 619 documents
carry a bounding box. Both were in turning the question into a geocodable place:

* "land cover change in the Amazon basin" -> spaCy tagged "Amazon" (LOC) and the extractor stopped
  there, discarding "basin". Google geocodes "Amazon basin"; it returns nothing for "Amazon".
* "UTM zone Champaign Illinois" -> the retrieval peer rewrites questions into keyword form, which
  strips the determiners and prepositions NER leans on. spaCy returned NO entities for that string
  while extracting both places from the original sentence.

And because only ``locations[0]`` was ever geocoded, one unresolvable candidate ended the search
instead of falling through to one that would have resolved.
"""

from __future__ import annotations

import pytest

import rag_pipeline.search.spatial as SP


@pytest.fixture(autouse=True)
def _clear_bbox_cache():
    SP._BBOX_CACHE.clear()
    yield
    SP._BBOX_CACHE.clear()


BOX = {"type": "polygon", "coordinates": [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]]}


# --- extraction -------------------------------------------------------------------


@pytest.mark.parametrize("query,expected_first", [
    ("land cover change in the Amazon basin", "Amazon basin"),
    ("datasets for the Mississippi River basin", "Mississippi River basin"),
    ("water quality in the Chesapeake Bay watershed", "Chesapeake Bay watershed"),
    ("soil moisture in the Great Plains", "Great Plains"),
])
def test_a_named_feature_keeps_its_feature_word(query, expected_first):
    """The geocodable form comes FIRST; the bare entity may follow as a fallback."""
    got = SP.extract_locations_from_query(query)
    assert got, f"nothing extracted from {query!r}"
    assert got[0] == expected_first


def test_the_bare_entity_survives_as_a_later_candidate():
    got = SP.extract_locations_from_query("land cover change in the Amazon basin")
    assert got[0] == "Amazon basin" and "Amazon" in got


def test_a_keyword_style_query_still_yields_a_place():
    """This is the form the search peer actually sends, and NER finds nothing in it."""
    got = SP.extract_locations_from_query("UTM zone Champaign Illinois")
    assert any("Champaign" in g for g in got)


@pytest.mark.parametrize("query", [
    "EPSG code for WGS 84",
    "convert a GeoJSON to a COG with GDAL",
    "what does the STAC API spec cover",
])
def test_technical_terms_are_not_offered_as_places(query):
    assert SP.extract_locations_from_query(query) == []


def test_plain_place_names_are_unaffected():
    assert SP.extract_locations_from_query("flood data for Illinois") == ["Illinois"]
    assert SP.extract_locations_from_query("urban heat in Chicago") == ["Chicago"]


# --- resolution -------------------------------------------------------------------


def test_resolution_falls_through_to_a_candidate_that_geocodes(monkeypatch):
    """One unresolvable candidate must not end the search."""
    tried = []

    def fake_geocode(location):
        tried.append(location)
        return BOX if location == "Amazon basin" else None

    monkeypatch.setattr(SP, "get_bounding_box", fake_geocode)
    monkeypatch.setattr(SP, "extract_locations_from_query", lambda q: ["Amazonia", "Amazon basin"])

    assert SP.resolve_query_bbox("anything") == BOX
    assert tried == ["Amazonia", "Amazon basin"]      # stopped at the first that resolved


def test_resolution_returns_none_when_nothing_geocodes(monkeypatch):
    monkeypatch.setattr(SP, "get_bounding_box", lambda loc: None)
    monkeypatch.setattr(SP, "extract_locations_from_query", lambda q: ["Nowhere", "Nowhere else"])
    assert SP.resolve_query_bbox("q") is None


def test_no_place_means_no_geocode_call_at_all(monkeypatch):
    def boom(_loc):
        raise AssertionError("geocoding must not be attempted without a candidate place")

    monkeypatch.setattr(SP, "get_bounding_box", boom)
    monkeypatch.setattr(SP, "extract_locations_from_query", lambda q: [])
    assert SP.resolve_query_bbox("EPSG code for WGS 84") is None


def test_geocode_results_are_cached_including_failures(monkeypatch):
    """Geocoding is paid and rate-limited; an unresolvable candidate must not be retried on every
    query that mentions it."""
    calls = {"n": 0}

    def counting(location):
        calls["n"] += 1
        return None if location == "Nowhere" else BOX

    monkeypatch.setattr(SP, "get_bounding_box", counting)

    assert SP._cached_bounding_box("Illinois") == BOX
    assert SP._cached_bounding_box("Illinois") == BOX
    assert calls["n"] == 1                       # hit served from cache

    assert SP._cached_bounding_box("Nowhere") is None
    assert SP._cached_bounding_box("Nowhere") is None
    assert calls["n"] == 2                       # the FAILURE was cached too

    assert SP._cached_bounding_box("ILLINOIS") == BOX
    assert calls["n"] == 2                       # keyed case-insensitively


def test_spatial_search_returns_empty_without_a_resolvable_place(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("OpenSearch must not be queried without a bounding box")

    monkeypatch.setattr(SP, "resolve_query_bbox", lambda q: None)
    monkeypatch.setattr(SP, "_os_client", boom)
    assert SP.get_spatial_search_results("EPSG code for WGS 84") == []
