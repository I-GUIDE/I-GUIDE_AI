"""Tests for the vector / shapefile (TIGER) tools.

Build a tiny real shapefile with geopandas, register the components as uploads, and
exercise read / visualize / analyze across the zip and extracted-sidecars paths.
Skipped if the geo stack isn't installed (it is in the agent runtime image).
"""

from __future__ import annotations

import glob
import json
import zipfile
from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")
pytest.importorskip("pyogrio")
from shapely.geometry import Point  # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402


def _tools():
    from agent_runtime.langchain_geo_tools import make_langchain_geo_tools
    return {t.name: t for t in make_langchain_geo_tools()}


def _upload(path):
    from agent_runtime.file_store import save_uploaded_file
    with open(path, "rb") as fh:
        return save_uploaded_file(FileStorage(stream=fh, filename=Path(path).name))["file_id"]


@pytest.fixture()
def shapefile(monkeypatch, tmp_path):
    """Write a 3-point shapefile (EPSG:4326) and return uploaded id variants."""
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path / "store"))
    work = tmp_path / "shp"
    work.mkdir()
    gpd.GeoDataFrame(
        {"name": ["a", "b", "c"], "val": [1, 2, 3]},
        geometry=[Point(-88.24, 40.11), Point(-88.20, 40.12), Point(-88.18, 40.10)],
        crs="EPSG:4326",
    ).to_file(work / "pts.shp")
    comps = sorted(glob.glob(str(work / "pts.*")))
    zp = work / "pts.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for c in comps:
            z.write(c, Path(c).name)
    ids = {Path(c).suffix: _upload(c) for c in comps}
    return {
        "zip_id": _upload(zp),
        "shp_id": ids[".shp"],
        "siblings": [v for k, v in ids.items() if k != ".shp"],
        "work": work,
    }


def test_factory_shape():
    tools = _tools()
    assert set(tools) == {"inspect_vector", "plot_vector", "vector_to_geojson",
                          "reproject_vector", "vector_spatial_join"}
    assert all(getattr(t, "metadata", {}).get("category") == "geo" for t in tools.values())


def test_inspect_zip(shapefile):
    r = json.loads(_tools()["inspect_vector"].invoke({"file_id": shapefile["zip_id"]}))
    assert r["ok"] is True
    assert r["feature_count"] == 3
    assert r["crs"] == "EPSG:4326"
    assert {"name", "val"} <= {c["name"] for c in r["columns"]}
    assert len(r["bounds"]) == 4


def test_inspect_extracted_siblings(shapefile):
    """The hard case: .shp + sidecars uploaded as SEPARATE files must be re-staged."""
    r = json.loads(_tools()["inspect_vector"].invoke(
        {"file_id": shapefile["shp_id"], "sibling_file_ids": shapefile["siblings"]}))
    assert r["ok"] is True and r["feature_count"] == 3
    assert "val" in {c["name"] for c in r["columns"]}  # .dbf attributes resolved


def test_plot_creates_downloadable_png(shapefile):
    from agent_runtime.file_store import resolve_file_id
    p = json.loads(_tools()["plot_vector"].invoke({"file_id": shapefile["zip_id"], "column": "val"}))
    assert p["ok"] is True and p["download_url"]
    assert resolve_file_id(p["file_id"]).stat().st_size > 0  # a real PNG was written


def test_plot_downsamples(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path / "store"))
    work = tmp_path / "many"; work.mkdir()
    pts = [Point(-88 + i * 0.001, 40 + i * 0.001) for i in range(100)]
    gpd.GeoDataFrame({"i": list(range(100))}, geometry=pts, crs="EPSG:4326").to_file(work / "m.shp")
    comps = glob.glob(str(work / "m.*"))  # capture BEFORE creating the zip
    zp = work / "m.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for c in comps:
            z.write(c, Path(c).name)
    p = json.loads(_tools()["plot_vector"].invoke({"file_id": _upload(zp), "max_features": 10}))
    assert p["ok"] is True and p["downsampled"] is True and p["plotted_features"] == 10


def test_vector_to_geojson_reprojects_to_wgs84(monkeypatch, tmp_path):
    from agent_runtime.file_store import resolve_file_id
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path / "store"))
    work = tmp_path / "merc"; work.mkdir()
    gpd.GeoDataFrame({"v": [1]}, geometry=[Point(-9825000, 4880000)], crs="EPSG:3857").to_file(work / "m.shp")
    comps = glob.glob(str(work / "m.*"))  # capture BEFORE creating the zip
    zp = work / "m.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for c in comps:
            z.write(c, Path(c).name)
    g = json.loads(_tools()["vector_to_geojson"].invoke({"file_id": _upload(zp)}))
    assert g["ok"] is True
    out = gpd.read_file(resolve_file_id(g["file_id"]))
    assert out.crs.to_epsg() == 4326  # reprojected from 3857


def test_spatial_join(shapefile):
    r = json.loads(_tools()["vector_spatial_join"].invoke(
        {"left_file_id": shapefile["zip_id"], "right_file_id": shapefile["zip_id"]}))
    assert r["ok"] is True and r["feature_count"] == 3 and r["download_url"]


def test_graceful_failure_no_raise():
    r = json.loads(_tools()["inspect_vector"].invoke({"file_id": "file_does_not_exist"}))
    assert r["ok"] is False and r.get("error")


# --- wiring into the peers -------------------------------------------------

def test_geo_tools_wired_into_peers_only_with_files(monkeypatch):
    import agent_runtime.executor_factory as ef
    import agent_runtime.supervisor_graph as sg

    captured = {}

    def fake_build(**kwargs):
        captured["tools"] = [getattr(t, "name", "") for t in (kwargs.get("preloaded_tools") or [])]
        return object()

    monkeypatch.setattr(ef, "build_agent_executor", fake_build)
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})
    # avoid heavy QGIS/MCP imports in the analyze peer
    import agent_runtime.langchain_granular_tools as gt
    monkeypatch.setattr(gt, "make_langchain_qgis_tools", lambda **k: [])
    monkeypatch.delenv("AGENT_CODE_EXEC", raising=False)

    # analyze peer WITH files -> geo tools present
    sg.default_analyze_fn(include_mcp_tools=False, input_file_ids=["file_x"])("q", [], {"thread_id": None})
    assert "inspect_vector" in captured["tools"] and "plot_vector" in captured["tools"]

    # analyze peer WITHOUT files -> no geo tools
    captured.clear()
    sg.default_analyze_fn(include_mcp_tools=False, input_file_ids=None)("q", [], {"thread_id": None})
    assert "inspect_vector" not in captured["tools"]

    # code peer WITH files -> geo tools present
    captured.clear()
    sg.default_code_fn(input_file_ids=["file_x"])("q", [], {"thread_id": None})
    assert "inspect_vector" in captured["tools"]
