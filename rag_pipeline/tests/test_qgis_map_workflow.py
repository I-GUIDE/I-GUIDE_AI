"""Deterministic QGIS map workflow: an explicit QGIS/basemap request must not fall back to
matplotlib (no basemap) or a degree-space buffer (~21.5 km instead of 25 km).
"""

from __future__ import annotations

import json

from agent_runtime.supervisor.graph import _detect_qgis_map_request


def test_detects_qgis_and_basemap_requests_with_metric_distance():
    d = _detect_qgis_map_request("Draw a 25km buffer around the city centers and show it on a map layer using qgis")
    assert d and d["distance_meters"] == 25000.0
    assert _detect_qgis_map_request("buffer 500 m around the points with QGIS")["distance_meters"] == 500.0
    assert round(_detect_qgis_map_request("10 miles buffer, qgis please")["distance_meters"]) == 16093
    # basemap request without a distance -> render only
    assert _detect_qgis_map_request("show these cities on a basemap") == {"distance_meters": None}
    # unrelated visualization requests are untouched
    assert _detect_qgis_map_request("plot the crime points as a heatmap") is None
    assert _detect_qgis_map_request("summarize this dataset") is None


def test_workflow_runs_buffer_then_render_with_basemap(monkeypatch, tmp_path):
    """The chain must call the METRIC buffer tool and render with an OSM basemap, feeding the
    buffer's output_path into the render layers."""
    import agent_runtime.supervisor.graph as sg
    import rag_pipeline.qgis_headless_tools as q

    src = tmp_path / "cities.geojson"
    src.write_text('{"type":"FeatureCollection","features":[]}')
    monkeypatch.setattr(sg, "_first_vector_path", lambda ids: str(src))
    monkeypatch.setattr(q, "qgis_process_available", lambda: True)
    monkeypatch.setattr(q, "pyqgis_available", lambda: True)
    seen = {}

    def fake_buffer(**kw):
        seen["buffer"] = kw
        return json.dumps({"ok": True, "output_path": str(tmp_path / "buffer.geojson"),
                           "projected_crs": kw["projected_crs"] if "projected_crs" in kw else "EPSG:26916",
                           "managed_output": {"file_id": "file_buf", "filename": "buffer.geojson",
                                              "download_url": "/agent/files/file_buf/download"}})

    def fake_render(**kw):
        seen["render"] = kw
        return json.dumps({"ok": True, "basemap": "OpenStreetMap", "crs": "EPSG:3857",
                           "managed_output": {"file_id": "file_png", "filename": "qgis_map.png",
                                              "download_url": "/agent/files/file_png/download"}})
    monkeypatch.setattr(q, "qgis_metric_buffer_tool", fake_buffer)
    monkeypatch.setattr(q, "pyqgis_render_map_tool", fake_render)

    out = sg._run_qgis_map_workflow("25 km buffer on a basemap with qgis",
                                    input_file_ids=["file_x"], thread_id="t",
                                    distance_meters=25000.0)
    assert out and out["qgis_workflow"] is True
    assert seen["buffer"]["distance_meters"] == 25000.0          # METRIC buffer, not degrees
    layers = json.loads(seen["render"]["layers_json"])
    assert layers[0]["path"].endswith("buffer.geojson")          # buffer output chained in
    assert seen["render"]["basemap"] == "osm"                    # rendered over a basemap
    assert out["basemap"] == "OpenStreetMap"
    assert "25 km buffer" in out["summary"] and "EPSG:26916" in out["summary"]

    # the produced PNG must be discoverable as an inline image artifact for the answer
    arts = sg._collect_image_artifacts(out, None)
    assert [a["file_id"] for a in arts] == ["file_png"]


def test_workflow_declines_when_backends_or_file_missing(monkeypatch, tmp_path):
    """No uploaded vector, or no PyQGIS -> return None so the normal LLM peer still runs."""
    import agent_runtime.supervisor.graph as sg
    import rag_pipeline.qgis_headless_tools as q
    monkeypatch.setattr(q, "pyqgis_available", lambda: True)
    monkeypatch.setattr(q, "qgis_process_available", lambda: True)
    monkeypatch.setattr(sg, "_first_vector_path", lambda ids: None)
    assert sg._run_qgis_map_workflow("qgis map", input_file_ids=[], thread_id=None) is None

    src = tmp_path / "a.geojson"; src.write_text("{}")
    monkeypatch.setattr(sg, "_first_vector_path", lambda ids: str(src))
    monkeypatch.setattr(q, "pyqgis_available", lambda: False)
    assert sg._run_qgis_map_workflow("qgis map", input_file_ids=["f"], thread_id=None) is None


def test_analyze_peer_short_circuits_to_qgis(monkeypatch, tmp_path):
    """default_analyze_fn must run the QGIS chain instead of building the LLM analysis agent."""
    import agent_runtime.executor_factory as ef
    import agent_runtime.supervisor.graph as sg

    def boom(**kw):
        raise AssertionError("must NOT build the LLM analysis agent for an explicit QGIS request")
    monkeypatch.setattr(ef, "build_agent_executor", boom)
    monkeypatch.setattr(sg, "_run_qgis_map_workflow",
                        lambda *a, **k: {"summary": "qgis done", "qgis_workflow": True})

    out = sg.default_analyze_fn(include_mcp_tools=False, input_file_ids=["file_x"])(
        "Draw a 25km buffer and show it on a map layer using qgis", [], {"thread_id": "t"})
    assert out["qgis_workflow"] is True and out["summary"] == "qgis done"
