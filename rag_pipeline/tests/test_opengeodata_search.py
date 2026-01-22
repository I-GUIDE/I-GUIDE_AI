from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from rag_pipeline.opengeodata_search import (
    OpenGeoDataError,
    get_opengeodata_results,
    run_opengeodata_search,
)
from rag_pipeline.state import ensure_state_shapes

SAMPLE_PAYLOAD = {
    "query": "water datasets",
    "bbox": [-10.0, -10.0, 10.0, 10.0],
    "timer": ["2020-01-01", "2020-12-31"],
    "count": 2,
    "assets": [
        {
            "id": "asset-1",
            "title": "Water availability in Utah",
            "abstract": "Detailed raster data about Utah water resources.",
            "keywords": ["water", "utah"],
            "bbox": [-1, -1, 1, 1],
            "datetime": ["2020-01-01", "2020-01-31"],
            "links": {"landing": "https://example.com/asset-1"},
            "license": "CC-BY",
            "provider": "Example Space Agency",
            "source": "stac",
        },
        {
            "id": "asset-2",
            "title": "Hydrology observations",
            "abstract": "Sensor data captured globally.",
            "keywords": ["hydrology"],
            "bbox": [0, 0, 5, 5],
            "datetime": ["2021-05-01", "2021-05-15"],
            "links": {"landing": "https://example.com/asset-2"},
            "license": "CC-BY",
            "provider": "Another Org",
            "source": "ogc",
        },
    ],
}


@patch("rag_pipeline.opengeodata_search.run_opengeodata")
def test_get_opengeodata_results_normalizes_assets(mock_run):
    mock_run.return_value = SAMPLE_PAYLOAD
    hits = get_opengeodata_results("water datasets", limit=5)
    assert len(hits) == 2
    assert hits[0]["_source"]["doc_id"] == "asset-1"
    assert hits[0]["_source"]["tags"] == ["water", "utah"]
    assert hits[1]["_source"]["doc_id"] == "asset-2"
    assert hits[0]["_score"] > hits[1]["_score"]


@patch("rag_pipeline.opengeodata_search.run_opengeodata")
def test_run_opengeodata_search_merges_into_state(mock_run):
    mock_run.return_value = SAMPLE_PAYLOAD
    state = ensure_state_shapes({"query_information": {"raw_text": "water datasets"}, "session_context": {}})
    entries = run_opengeodata_search(state, limit=3)
    assert len(entries) == 2
    docs = state["evidence"]["retrieved_documents"]
    assert len(docs) == 2
    assert docs[0]["source"] == "opengeodata"
    assert docs[0]["document"]["doc_id"] == "asset-1"


@patch("rag_pipeline.opengeodata_search.run_opengeodata")
def test_get_opengeodata_results_handles_errors(mock_run):
    mock_run.side_effect = OpenGeoDataError("failure")
    hits = get_opengeodata_results("water")
    assert hits == []


@patch("rag_pipeline.opengeodata_search.run_opengeodata")
def test_get_opengeodata_results_includes_nl_block(mock_run, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.com/api")
    monkeypatch.setenv("OPENGEODATA_NL_MODEL", "gpt-test")
    mock_run.return_value = SAMPLE_PAYLOAD
    session_ctx = {"opengeodata": {"default_bbox": [-10, -10, 10, 10]}}

    get_opengeodata_results("nl test query", limit=2, session_ctx=session_ctx)

    assert mock_run.called, "Expected run_opengeodata to be invoked"
    kwargs = mock_run.call_args.kwargs
    nl = kwargs.get("nl")
    assert nl is not None, "NL block should be attached automatically"
    assert nl["user_query"] == "nl test query"
    assert nl["model"] == "gpt-test"
    assert nl["api_key"] == "sk-test"
    assert nl["api_base"] == "https://example.com/api"
    assert nl.get("default_bbox") == [-10, -10, 10, 10]


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("RUN_REAL_OPEN_GEODATA_TEST"),
    reason="Set RUN_REAL_OPEN_GEODATA_TEST=1 to enable the live OpenGeoData integration test.",
)
def test_opengeodata_real_query_returns_results():
    """
    Executes a real discovery request similar to the notebook examples to validate full integration.
    Requires network access and pystac-client dependency.
    """
    session_ctx = {
        "opengeodata": {
            "bbox": [-91.6, 36.9, -87.4, 42.5],  # Illinois bounds from the notebook example
            "timer": ["2020-01-01", "2025-11-06"],
            "providers": {
                "stac": ["https://planetarycomputer.microsoft.com/api/stac/v1"],
                "records": [],
                "ckan": [("https://api.gsa.gov/technology/datagov/v3/action", None)],
                "cmr": True,
            },
        }
    }
    hits = get_opengeodata_results("land cover for Illinois", limit=3, session_ctx=session_ctx)
    print(hits)
    assert hits, "Expected at least one OpenGeoData result"
    doc = hits[0]["_source"]
    assert doc.get("doc_id")
    assert doc.get("title")
