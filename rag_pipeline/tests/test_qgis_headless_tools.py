from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rag_pipeline import qgis_headless_tools
from rag_pipeline.qgis_headless_tools import (
    pyqgis_render_map_tool,
    pyqgis_layer_summary_tool,
    qgis_metric_buffer_tool,
    qgis_processing_run_tool,
)


@pytest.fixture
def qgis_job_root(tmp_path, monkeypatch):
    root = tmp_path / "agent_files"
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(root))
    # A developer .env may set AGENT_PUBLIC_BASE_URL (loaded via load_dotenv on import) —
    # these tests assert the host-relative download_url form.
    monkeypatch.delenv("AGENT_PUBLIC_BASE_URL", raising=False)
    return root


def test_qgis_processing_run_uses_session_job_dir_and_rewrites_relative_output(qgis_job_root, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, "kwargs": kwargs})
        return subprocess.CompletedProcess(command, 0, stdout='{"OUTPUT": "done"}', stderr="")

    monkeypatch.setattr(qgis_headless_tools.subprocess, "run", fake_run)

    result = json.loads(
        qgis_processing_run_tool(
            "native:buffer",
            json.dumps(
                {
                    "INPUT": "/data/roads.geojson",
                    "DISTANCE": 100,
                    "DISSOLVE": False,
                    "OUTPUT": "roads_buffer.geojson",
                }
            ),
            session_id="thread::abc/search",
        )
    )

    assert result["ok"] is True
    assert result["algorithm"] == "native:buffer"
    assert "/thread_abc_search/" in result["job_dir"]
    assert result["effective_parameters"]["OUTPUT"].endswith("roads_buffer.geojson")
    assert str(Path(result["effective_parameters"]["OUTPUT"]).parent) == result["job_dir"]
    assert calls[0]["command"][0].endswith("qgis_process")
    assert calls[0]["command"][1:4] == ["--json", "run", "native:buffer"]
    assert "DISSOLVE=false" in calls[0]["command"]
    assert calls[0]["kwargs"]["cwd"] == result["job_dir"]


def test_pyqgis_worker_invocation_is_per_session_and_reads_result(qgis_job_root, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, "kwargs": kwargs})
        spec_path = Path(command[-1])
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        Path(spec["result_path"]).write_text(
            json.dumps({"ok": True, "name": "roads", "feature_count": 3}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="worker ok", stderr="")

    monkeypatch.setattr(qgis_headless_tools.subprocess, "run", fake_run)

    result = json.loads(
        pyqgis_layer_summary_tool(
            "/data/roads.geojson",
            provider="ogr",
            session_id="memory:demo",
        )
    )

    assert result["ok"] is True
    assert result["name"] == "roads"
    assert result["feature_count"] == 3
    assert "/memory_demo/" in result["job_dir"]
    assert calls[0]["command"][1].endswith("rag_pipeline/qgis_pyqgis_worker.py")
    assert calls[0]["command"][2] == "layer_summary"


def test_pyqgis_layer_summary_resolves_uploaded_file_id(qgis_job_root, monkeypatch):
    uploads = qgis_job_root / "uploads"
    metadata = qgis_job_root / "metadata"
    uploads.mkdir(parents=True)
    metadata.mkdir(parents=True)
    uploaded_path = uploads / "file_demo__points.geojson"
    uploaded_path.write_text("{}", encoding="utf-8")
    (metadata / "file_demo.json").write_text(
        json.dumps(
            {
                "file_id": "file_demo",
                "filename": "points.geojson",
                "kind": "upload",
                "path": str(uploaded_path),
                "size_bytes": 2,
                "download_url": "/agent/files/file_demo/download",
            }
        ),
        encoding="utf-8",
    )
    captured_specs = []

    def fake_run(command, **kwargs):
        spec_path = Path(command[-1])
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        captured_specs.append(spec)
        Path(spec["result_path"]).write_text(json.dumps({"ok": True}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="worker ok", stderr="")

    monkeypatch.setattr(qgis_headless_tools.subprocess, "run", fake_run)

    result = json.loads(pyqgis_layer_summary_tool("file_demo", provider="ogr", session_id="memory:demo"))

    assert result["ok"] is True
    assert captured_specs[0]["layer_path"] == str(uploaded_path)


def test_pyqgis_render_map_registers_binary_output(qgis_job_root, monkeypatch):
    captured_specs = []

    def fake_run(command, **kwargs):
        spec_path = Path(command[-1])
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        captured_specs.append(spec)
        output_path = Path(spec["job_dir"]) / "map.png"
        output_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")
        Path(spec["result_path"]).write_text(
            json.dumps({"ok": True, "output_path": str(output_path)}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="worker ok", stderr="")

    monkeypatch.setattr(qgis_headless_tools.subprocess, "run", fake_run)

    result = json.loads(
        pyqgis_render_map_tool(
            json.dumps([{"path": "/data/points.geojson", "provider": "ogr"}]),
            output_filename="map.png",
            basemap="osm",
            session_id="memory:demo",
        )
    )

    managed = result["managed_output"]
    managed_path = Path(managed["path"])
    if not managed_path.is_absolute():
        managed_path = qgis_job_root / managed_path

    assert result["ok"] is True
    assert managed["filename"] == "map.png"
    assert managed["download_url"].startswith("/agent/files/")
    assert managed_path.read_bytes().startswith(b"\x89PNG")
    assert captured_specs[0]["basemap"] == "osm"


def test_pyqgis_render_map_resolves_uploaded_file_id_in_layer_specs(qgis_job_root, monkeypatch):
    uploads = qgis_job_root / "uploads"
    metadata = qgis_job_root / "metadata"
    uploads.mkdir(parents=True)
    metadata.mkdir(parents=True)
    uploaded_path = uploads / "file_demo__points.geojson"
    uploaded_path.write_text("{}", encoding="utf-8")
    (metadata / "file_demo.json").write_text(
        json.dumps(
            {
                "file_id": "file_demo",
                "filename": "points.geojson",
                "kind": "upload",
                "path": str(uploaded_path),
                "size_bytes": 2,
                "download_url": "/agent/files/file_demo/download",
            }
        ),
        encoding="utf-8",
    )
    captured_specs = []

    def fake_run(command, **kwargs):
        spec_path = Path(command[-1])
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        captured_specs.append(spec)
        output_path = Path(spec["job_dir"]) / "map.png"
        output_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")
        Path(spec["result_path"]).write_text(
            json.dumps({"ok": True, "output_path": str(output_path)}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="worker ok", stderr="")

    monkeypatch.setattr(qgis_headless_tools.subprocess, "run", fake_run)

    result = json.loads(
        pyqgis_render_map_tool(
            json.dumps([{"path": "file_demo", "provider": "ogr"}]),
            output_filename="map.png",
            session_id="memory:demo",
        )
    )

    assert result["ok"] is True
    assert captured_specs[0]["layers"][0]["path"] == str(uploaded_path)


def test_qgis_metric_buffer_reprojects_before_buffering(qgis_job_root, monkeypatch):
    uploads = qgis_job_root / "uploads"
    metadata = qgis_job_root / "metadata"
    uploads.mkdir(parents=True)
    metadata.mkdir(parents=True)
    uploaded_path = uploads / "file_demo__points.geojson"
    uploaded_path.write_text("{}", encoding="utf-8")
    (metadata / "file_demo.json").write_text(
        json.dumps(
            {
                "file_id": "file_demo",
                "filename": "points.geojson",
                "kind": "upload",
                "path": str(uploaded_path),
                "size_bytes": 2,
                "download_url": "/agent/files/file_demo/download",
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, "kwargs": kwargs})
        if "OUTPUT=buffer.geojson" not in command:
            for item in command:
                if item.startswith("OUTPUT="):
                    Path(item.removeprefix("OUTPUT=")).write_text("{}", encoding="utf-8")
        else:
            Path(kwargs["cwd"]) / "buffer.geojson"
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(qgis_headless_tools.subprocess, "run", fake_run)

    result = json.loads(
        qgis_metric_buffer_tool(
            "file_demo",
            distance_meters=500,
            output_filename="sites_buffer.geojson",
            projected_crs="EPSG:26916",
            session_id="thread::abc",
        )
    )

    assert result["ok"] is True
    assert result["input_layer"] == str(uploaded_path)
    assert result["projected_crs"] == "EPSG:26916"
    assert result["target_crs"] == "EPSG:4326"
    assert [call["command"][3] for call in calls] == [
        "native:reprojectlayer",
        "native:buffer",
        "native:reprojectlayer",
    ]
    assert f"INPUT={uploaded_path}" in calls[0]["command"]
    assert "DISTANCE=500.0" in calls[1]["command"]
    assert result["managed_output"]["filename"] == "sites_buffer.geojson"


# --- availability probes + tool gating -------------------------------------

def test_qgis_probes_force_override(monkeypatch):
    from rag_pipeline.qgis_headless_tools import (
        qgis_available, qgis_process_available, pyqgis_available,
    )
    monkeypatch.setenv("AGENT_QGIS_ENABLED", "0")
    assert qgis_process_available() is False and pyqgis_available() is False
    assert qgis_available() is False
    monkeypatch.setenv("AGENT_QGIS_ENABLED", "1")
    assert qgis_process_available() is True and pyqgis_available() is True
    assert qgis_available() is True


def test_qgis_cli_probe_uses_path(monkeypatch):
    from rag_pipeline import qgis_headless_tools as q
    monkeypatch.delenv("AGENT_QGIS_ENABLED", raising=False)
    monkeypatch.setenv("QGIS_PROCESS_BIN", "qgis_process_definitely_absent_zzz")
    assert q.qgis_process_available() is False


def test_qgis_tools_gated_out_when_unavailable(monkeypatch):
    """When neither backend is present, the QGIS tool set is empty so the agent falls
    back to the geopandas geo tools (plot_vector) instead of failing on QGIS."""
    import agent_runtime.langchain_granular_tools as g
    # patch the probes as bound in the granular-tools module (where make_* reads them)
    monkeypatch.setattr(g, "qgis_process_available", lambda: False)
    monkeypatch.setattr(g, "pyqgis_available", lambda: False)
    assert g.make_langchain_qgis_tools() == []


def test_qgis_tools_split_by_backend(monkeypatch):
    """CLI-only deployment exposes processing tools but NOT the PyQGIS render/summary tools
    (the exact 'can't plot' case); PyQGIS-only exposes only render/summary."""
    import agent_runtime.langchain_granular_tools as g
    monkeypatch.delenv("AGENT_QGIS_ENABLED", raising=False)

    monkeypatch.setattr(g, "qgis_process_available", lambda: True)
    monkeypatch.setattr(g, "pyqgis_available", lambda: False)
    names = {t.name for t in g.make_langchain_qgis_tools()}
    assert names == {"qgis_processing_help", "qgis_processing_run", "qgis_metric_buffer"}
    assert "pyqgis_render_map" not in names  # plotting needs PyQGIS, which is absent

    monkeypatch.setattr(g, "qgis_process_available", lambda: False)
    monkeypatch.setattr(g, "pyqgis_available", lambda: True)
    names = {t.name for t in g.make_langchain_qgis_tools()}
    assert names == {"pyqgis_layer_summary", "pyqgis_render_map"}


# --- uploaded shapefile resolution (stage extracted siblings / vsizip) ------

def _make_shapefile_uploads(tmp_path, monkeypatch):
    """Build a real 3-feature shapefile, upload each component as a SEPARATE file, and
    return {ext: file_id} plus a zip upload id. Skips if geopandas isn't installed."""
    import glob
    import zipfile
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Point
    from werkzeug.datastructures import FileStorage
    from agent_runtime.file_store import save_uploaded_file

    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path / "store"))
    work = tmp_path / "shp"; work.mkdir()
    gpd.GeoDataFrame({"name": ["a", "b", "c"], "v": [1, 2, 3]},
                     geometry=[Point(-88.2, 40.1), Point(-88.1, 40.2), Point(-88.0, 40.0)],
                     crs="EPSG:4326").to_file(work / "pts.shp")
    comps = sorted(glob.glob(str(work / "pts.*")))

    def up(p):
        with open(p, "rb") as fh:
            return save_uploaded_file(FileStorage(stream=fh, filename=Path(p).name))["file_id"]
    ids = {Path(c).suffix: up(c) for c in comps}
    zp = work / "pts.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for c in comps:
            z.write(c, Path(c).name)
    with open(zp, "rb") as fh:
        ids["__zip__"] = save_uploaded_file(FileStorage(stream=fh, filename="pts.zip"))["file_id"]
    return ids


def test_resolve_layer_ref_stages_extracted_shapefile(tmp_path, monkeypatch):
    gpd = pytest.importorskip("geopandas")
    ids = _make_shapefile_uploads(tmp_path, monkeypatch)
    staged = qgis_headless_tools._resolve_layer_ref(ids[".shp"])
    assert staged.endswith(".shp")
    gdf = gpd.read_file(staged)               # opens only if .shx/.dbf are co-located
    assert len(gdf) == 3 and "v" in gdf.columns


def test_resolve_layer_ref_from_sidecar_reconstructs_shp(tmp_path, monkeypatch):
    gpd = pytest.importorskip("geopandas")
    ids = _make_shapefile_uploads(tmp_path, monkeypatch)
    staged = qgis_headless_tools._resolve_layer_ref(ids[".dbf"])  # point at a sidecar
    assert staged.endswith(".shp") and len(gpd.read_file(staged)) == 3


def test_resolve_layer_ref_zip_uses_vsizip(tmp_path, monkeypatch):
    ids = _make_shapefile_uploads(tmp_path, monkeypatch)
    assert qgis_headless_tools._resolve_layer_ref(ids["__zip__"]).startswith("/vsizip/")


def test_resolve_layer_ref_passthrough_for_raw_path():
    assert qgis_headless_tools._resolve_layer_ref("/data/already/on/disk.shp") == "/data/already/on/disk.shp"


def test_pyqgis_available_probes_worker_python(monkeypatch):
    monkeypatch.delenv("AGENT_QGIS_ENABLED", raising=False)
    qgis_headless_tools._PYQGIS_PROBE_CACHE.clear()
    monkeypatch.setenv("QGIS_PYTHON_BIN", "/nonexistent/python_zzz")   # cannot import qgis
    assert qgis_headless_tools.pyqgis_available() is False
    monkeypatch.setenv("AGENT_QGIS_ENABLED", "1")                      # override wins
    assert qgis_headless_tools.pyqgis_available() is True
    monkeypatch.setenv("AGENT_QGIS_ENABLED", "0")
    assert qgis_headless_tools.pyqgis_available() is False
