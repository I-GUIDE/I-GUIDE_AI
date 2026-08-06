"""DataCite connector + the external-data discovery gate.

Two follow-ups to the relevance work: broaden coverage beyond three hard-coded portals with a
keyless global dataset search, and stop running external catalog search at all for requests that
are not data discovery (where relevance scoring cannot help because the terms genuinely match).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rag_pipeline.search.opengeodata import wants_external_data
from rag_pipeline.search.opengeodata_connectors import search_datacite

_RECORD = {
    "attributes": {
        "doi": "10.5285/abc123",
        "titles": [{"title": "Sedimentological data from the Limpopo"}],
        "descriptions": [
            {"descriptionType": "Other", "description": "short note"},
            {"descriptionType": "Abstract", "description": "A long abstract " * 40},
        ],
        "subjects": [{"subject": "rivers"}, {"subject": "sediment"}],
        "geoLocations": [{"geoLocationBox": {"westBoundLongitude": 22.236, "eastBoundLongitude": 36.475,
                                             "southBoundLatitude": -32.547, "northBoundLatitude": -14.945}}],
        "dates": [{"date": "2018-01-01/2019-12-31", "dateType": "Collected"}],
        "rightsList": [{"rightsIdentifier": "cc-by-4.0"}],
        "publisher": "NERC EDS",
        "url": "https://catalogue.ceh.ac.uk/id/abc123",
        "publicationYear": 2020,
    }
}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_session(payload):
    class _S:
        def get(self, url, params=None, headers=None):
            _S.last = {"url": url, "params": params}
            return _Resp(payload)
    return _S()


def test_datacite_maps_every_geoasset_field():
    with patch("rag_pipeline.search.opengeodata_connectors.session",
               return_value=_fake_session({"data": [_RECORD]})):
        assets = search_datacite(q="limpopo sediment", limit=5)
    assert len(assets) == 1
    a = assets[0]
    assert a.id == "10.5285/abc123"                       # DOI is a stable id
    assert a.title == "Sedimentological data from the Limpopo"
    assert a.abstract.startswith("A long abstract")       # Abstract preferred over the short note
    assert len(a.abstract) > 100                          # full text, not a snippet
    assert a.keywords == ["rivers", "sediment"]
    assert a.bbox == (22.236, -32.547, 36.475, -14.945)   # geoLocationBox -> bbox
    assert a.datetime == ("2018-01-01", "2019-12-31")     # ISO interval split
    assert a.license == "cc-by-4.0"
    assert a.provider == "NERC EDS"
    assert a.links["Landing Page"].endswith("abc123")
    assert a.source == "datacite"


def test_datacite_point_and_missing_geolocations():
    from rag_pipeline.search.opengeodata_connectors import _datacite_bbox
    assert _datacite_bbox([{"geoLocationPoint": {"pointLongitude": -89.6, "pointLatitude": 39.8}}]) == \
        (-89.6, 39.8, -89.6, 39.8)                        # point -> degenerate box
    assert _datacite_bbox([{"geoLocationPlace": "Illinois"}]) is None   # a name is not coordinates
    assert _datacite_bbox([]) is None and _datacite_bbox(None) is None
    assert _datacite_bbox([{"geoLocationBox": {"westBoundLongitude": 999}}]) is None  # malformed


def test_datacite_dedupes_repository_versions():
    """Zenodo mints a DOI per version; the same dataset must not fill several result slots."""
    v1 = {"attributes": dict(_RECORD["attributes"], doi="10.5281/zenodo.1")}
    v2 = {"attributes": dict(_RECORD["attributes"], doi="10.5281/zenodo.2")}
    other = {"attributes": dict(_RECORD["attributes"], doi="10.5281/zenodo.3",
                                titles=[{"title": "A different dataset"}])}
    with patch("rag_pipeline.search.opengeodata_connectors.session",
               return_value=_fake_session({"data": [v1, v2, other]})):
        assets = search_datacite(q="x", limit=5)
    assert [a.title for a in assets] == ["Sedimentological data from the Limpopo", "A different dataset"]


def test_datacite_empty_query_short_circuits():
    with patch("rag_pipeline.search.opengeodata_connectors.session") as sess:
        assert search_datacite(q="   ", limit=5) == []
        sess.assert_not_called()


def test_datacite_is_a_default_provider():
    from rag_pipeline.search.opengeodata_utils import DEFAULT_PROVIDERS
    assert DEFAULT_PROVIDERS.get("datacite") is True


# --- discovery-intent gate ------------------------------------------------------

@pytest.mark.parametrize("query", [
    "Find open geospatial datasets about dams in Illinois",
    "datasets about urban heat exposure in Chicago",
    "any public data on air quality",
    "look for satellite imagery of California wildfires",
    "is there open data for groundwater levels in Kansas",
    "find datasets to make a map of flood risk",          # discovery wins over the map verb
])
def test_gate_allows_data_discovery(query):
    assert wants_external_data(query) is True


@pytest.mark.parametrize("query", [
    "What are the related elements of 5e9c7566-1be5-49ea-aaec-fa304f401dd2",
    "How to build the I-GUIDE Smart Search",
    "what are the most popular knowledge elements",
    "Please produce a cartographic bubble map based on the locations of the institutions and "
    "their numbers of knowledge elements",
    "Draw a 25km buffer around the city center points and visualize it on a map layer using qgis",
    "Map this CSV and convert it to GeoJSON",
    "who are you",
])
def test_gate_skips_platform_and_task_requests(query):
    assert wants_external_data(query) is False


def test_gate_short_circuits_the_search(monkeypatch):
    """A non-discovery request must not even reach run_opengeodata."""
    import rag_pipeline.search.opengeodata as og

    def boom(**kwargs):
        raise AssertionError("external catalog search must not run for a non-discovery request")
    monkeypatch.setattr(og, "run_opengeodata", boom)
    assert og.get_opengeodata_results("what are the most popular knowledge elements") == []
    assert og.get_opengeodata_results("") == []
