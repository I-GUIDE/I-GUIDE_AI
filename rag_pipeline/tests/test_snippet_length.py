"""Search hits must not truncate real abstracts.

Reported: OpenGeoData results came back with a cut-off abstract. Both normalizers hard-capped
document text at 800 characters, but real OpenGeoData descriptions routinely run 1-3k chars
(measured: 5 of 6 results for "dams" exceeded 800; longest 3061).
"""

from __future__ import annotations

from agent_runtime.langchain_granular_tools import _normalize_hits
from rag_pipeline.search.agents import _hit_to_document
from rag_pipeline.search.utils import snippet_chars

LONG = "A" * 3061


def _hit(element_type="opengeodata"):
    return {"_id": "og1", "_score": 1.0, "_source": {
        "title": "Major Dams in the United States", "element_type": element_type,
        "contents": LONG, "url": "https://cmr.earthdata.nasa.gov/x", "provider": "NASA CMR"}}


def test_snippet_cap_is_generous_and_env_tunable(monkeypatch):
    monkeypatch.delenv("AGENT_SEARCH_SNIPPET_CHARS", raising=False)
    assert snippet_chars() == 4000                     # was 800
    monkeypatch.setenv("AGENT_SEARCH_SNIPPET_CHARS", "1500")
    assert snippet_chars() == 1500
    monkeypatch.setenv("AGENT_SEARCH_SNIPPET_CHARS", "junk")
    assert snippet_chars() == 4000                     # bad value -> default
    monkeypatch.setenv("AGENT_SEARCH_SNIPPET_CHARS", "10")
    assert snippet_chars() == 200                      # floor


def test_tool_payload_keeps_a_full_length_abstract(monkeypatch):
    monkeypatch.delenv("AGENT_SEARCH_SNIPPET_CHARS", raising=False)
    doc = _normalize_hits([_hit()], "opengeodata")[0]
    assert len(doc["contents"]) == len(LONG)           # no longer cut at 800
    assert doc["abstract"] == LONG                      # full text preserved for clients


def test_graph_path_uses_the_same_cap(monkeypatch):
    monkeypatch.delenv("AGENT_SEARCH_SNIPPET_CHARS", raising=False)
    assert len(_hit_to_document(_hit())["contents"]) == len(LONG)


def test_internal_hits_do_not_duplicate_text(monkeypatch):
    """Only external (user-facing) results carry the extra abstract copy."""
    monkeypatch.delenv("AGENT_SEARCH_SNIPPET_CHARS", raising=False)
    doc = _normalize_hits([_hit(element_type="dataset")], "semantic")[0]
    assert "abstract" not in doc


def test_cap_still_applies_beyond_the_limit(monkeypatch):
    monkeypatch.setenv("AGENT_SEARCH_SNIPPET_CHARS", "500")
    doc = _normalize_hits([_hit()], "opengeodata")[0]
    assert len(doc["contents"]) == 500                  # bounded for prompt economy
    assert doc["abstract"] == LONG                      # but the client still gets it all
