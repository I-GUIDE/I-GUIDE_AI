"""Render TIF/SHP tool — renders a spatial raster or vector file to a PNG.

Adapted for i-GUIDE: instead of the original QGIS-Desktop subprocess renderer,
this delegates to i-GUIDE's headless **PyQGIS** renderer
(``rag_pipeline.qgis_headless_tools.pyqgis_render_map_tool``), which runs
standalone QGIS bindings in an isolated per-session job directory and registers
the PNG in the managed agent file store (returning a ``download_url``).
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Callable

from agent_runtime.telecoupling.shared.utils import CSISError

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["file_path"]

_RASTER_EXTS = {".tif", ".tiff"}
_VECTOR_EXTS = {".shp", ".geojson", ".gpkg"}


async def run_render_tif(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    """Render a TIF or SHP file to a PNG image using headless PyQGIS."""
    # Imported lazily so the vendored package never hard-depends on PyQGIS.
    from rag_pipeline.qgis_headless_tools import pyqgis_render_map_tool

    file_path = str(params.get("file_path", "")).strip()
    if not file_path:
        raise CSISError("Missing required parameter: file_path", "MISSING_PARAMS")

    if not os.path.isfile(file_path):
        raise CSISError(
            f"File not found: {file_path}. "
            "Please provide the absolute path to the file on disk.",
            "FILE_NOT_FOUND",
        )

    ext = Path(file_path).suffix.lower()
    if ext in _RASTER_EXTS:
        provider = "gdal"
    elif ext in _VECTOR_EXTS:
        provider = "ogr"
    else:
        raise CSISError(
            f"Unsupported file type '{ext}'. Supported: .tif, .tiff, .shp, .geojson, .gpkg",
            "UNSUPPORTED_TYPE",
        )

    progress_callback(20, f"Rendering {Path(file_path).name} with headless PyQGIS...")

    stem = Path(file_path).stem
    layers_json = json.dumps([
        {"path": file_path, "provider": provider, "name": stem}
    ])

    raw = pyqgis_render_map_tool(
        layers_json=layers_json,
        output_filename=f"{stem}_render.png",
        width=1920,
        height=1080,
        basemap="osm" if provider == "ogr" else "none",
        session_id=session_id,
    )
    try:
        result = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception as exc:  # pragma: no cover - defensive
        raise CSISError(f"PyQGIS render returned malformed output: {exc}", "QGIS_FAILED")

    if not result.get("ok"):
        message = result.get("error") or result.get("stderr") or "PyQGIS render failed"
        raise CSISError(f"PyQGIS render failed: {message}", "QGIS_FAILED")

    output_path = result.get("output_path")
    if not output_path or not os.path.isfile(output_path):
        raise CSISError("Render produced no output file.", "QGIS_FAILED")

    progress_callback(100, "Render complete")

    managed = result.get("managed_output") or {}
    file_entry = {
        "filename": Path(output_path).name,
        "path": output_path,
        "render_type": "image",
    }
    # Pass through managed-store identifiers so the LangChain wrapper does not
    # register the file a second time.
    if managed.get("download_url"):
        file_entry["download_url"] = managed["download_url"]
    if managed.get("file_id"):
        file_entry["file_id"] = managed["file_id"]

    return {"success": True, "files": [file_entry]}
