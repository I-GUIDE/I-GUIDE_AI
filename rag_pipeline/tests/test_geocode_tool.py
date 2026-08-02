"""Tests for the agent-side geocode_places tool (mocked Nominatim — no network)."""

from __future__ import annotations

import json

import rag_pipeline.search.opengeodata_new as og


def _fake_geocode(name):
    known = {
        "University of Illinois Urbana-Champaign": (-88.3, 40.0, -88.1, 40.2),
        "Michigan State University": (-84.5, 42.6, -84.4, 42.8),
    }
    return known.get(name)


def test_geocode_places_list_input(monkeypatch):
    monkeypatch.setattr(og, "geocode_place", _fake_geocode)
    from agent_runtime.langchain_granular_tools import geocode_places_tool
    out = json.loads(geocode_places_tool(
        ["University of Illinois Urbana-Champaign", "ORCID", "Michigan State University"]))
    assert out["count"] == 2
    assert out["not_found"] == ["ORCID"]
    uiuc = out["results"][0]
    assert uiuc["found"] and abs(uiuc["lat"] - 40.1) < 1e-6 and abs(uiuc["lon"] - (-88.2)) < 1e-6
    assert uiuc["bbox"] == [-88.3, 40.0, -88.1, 40.2]
    assert out["results"][1] == {"place": "ORCID", "found": False}


def test_geocode_places_string_inputs(monkeypatch):
    monkeypatch.setattr(og, "geocode_place", _fake_geocode)
    from agent_runtime.langchain_granular_tools import geocode_places_tool
    # comma-separated string
    out = json.loads(geocode_places_tool("Michigan State University, ORCID"))
    assert out["count"] == 1 and out["not_found"] == ["ORCID"]
    # JSON-encoded list string
    out2 = json.loads(geocode_places_tool('["Michigan State University"]'))
    assert out2["count"] == 1


def test_geocode_places_caps_input_and_never_raises(monkeypatch):
    calls = {"n": 0}

    def boom(name):
        calls["n"] += 1
        raise RuntimeError("nominatim down")
    monkeypatch.setattr(og, "geocode_place", boom)
    from agent_runtime.langchain_granular_tools import _GEOCODE_MAX_PLACES, geocode_places_tool
    out = json.loads(geocode_places_tool([f"place-{i}" for i in range(_GEOCODE_MAX_PLACES + 10)]))
    assert calls["n"] == _GEOCODE_MAX_PLACES            # capped
    assert out["count"] == 0                             # errors degrade to found=false
    assert "truncated" in out.get("note", "")


def test_geocode_tool_wired_into_code_and_analyze_peers(monkeypatch):
    """Both peers must expose geocode_places so named-place maps never ask the user."""
    from types import SimpleNamespace
    import agent_runtime.executor_factory as ef
    import agent_runtime.langchain_granular_tools as gt
    import agent_runtime.langchain_file_tools as ft
    import agent_runtime.supervisor_graph as sg

    monkeypatch.delenv("AGENT_CODE_EXEC", raising=False)
    monkeypatch.setattr(gt, "make_langchain_qgis_tools", lambda **k: [])
    monkeypatch.setattr(ft, "make_langchain_file_tools", lambda: [SimpleNamespace(name="read_text_file")])
    captured = {}

    def fake_build(**kwargs):
        captured["tools"] = [getattr(t, "name", "") for t in (kwargs.get("preloaded_tools") or [])]
        return object()
    monkeypatch.setattr(ef, "build_agent_executor", fake_build)
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})

    sg.default_code_fn()("bubble map of institutions", [], {"thread_id": None})
    assert "geocode_places" in captured["tools"]

    captured.clear()
    sg.default_analyze_fn(include_mcp_tools=False)("map institutions", [], {"thread_id": None})
    assert "geocode_places" in captured["tools"]
