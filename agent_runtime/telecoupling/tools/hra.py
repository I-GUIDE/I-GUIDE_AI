"""
Tool: Habitat Risk Assessment (natcap.invest.hra).

Required: info_table_path, criteria_table_path, resolution, max_rating,
          risk_eq, decay_eq, n_overlapping_stressors, aoi_vector_path.
Optional: visualize_outputs.
"""
from __future__ import annotations
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Callable

import natcap.invest.hra
import pygeoprocessing

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)


@contextmanager
def _patched_zonal_statistics():
    """Patch pygeoprocessing.zonal_statistics to accept wkbUnknown layer types.

    InVEST HRA._simplify creates GPKG with wkbUnknown layer type (by design,
    to support mixed geometries), but pygeoprocessing.zonal_statistics rejects
    any layer whose GetGeomType() is not Polygon or MultiPolygon.  This patch
    intercepts calls where the layer type is wkbUnknown and rewrites the vector
    to a temp GPKG with an explicit MultiPolygon type before passing it onward.
    """
    from osgeo import gdal, ogr

    original = pygeoprocessing.zonal_statistics

    def _fixed(base_raster_path_band, aggregate_vector_path, *args, **kwargs):
        ds = gdal.OpenEx(aggregate_vector_path, gdal.OF_VECTOR)
        if ds is not None:
            lyr = ds.GetLayer(0)
            if lyr is not None and lyr.GetGeomType() == ogr.wkbUnknown:
                tmp_path = tempfile.mktemp(suffix=".gpkg")
                driver = gdal.GetDriverByName("GPKG")
                out_ds = driver.Create(tmp_path, 0, 0, 0, gdal.GDT_Unknown)
                out_lyr = out_ds.CreateLayer(
                    "subregions", lyr.GetSpatialRef(), ogr.wkbMultiPolygon
                )
                for field in lyr.schema:
                    out_lyr.CreateField(field)
                out_lyr.StartTransaction()
                for feat in lyr:
                    out_lyr.CreateFeature(feat.Clone())
                out_lyr.CommitTransaction()
                out_lyr = out_ds = lyr = ds = None
                try:
                    return original(base_raster_path_band, tmp_path, *args, **kwargs)
                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
            lyr = ds = None
        return original(base_raster_path_band, aggregate_vector_path, *args, **kwargs)

    pygeoprocessing.zonal_statistics = _fixed
    try:
        yield
    finally:
        pygeoprocessing.zonal_statistics = original

REQUIRED_KEYS = [
    "info_table_path",
    "criteria_table_path",
    "resolution",
    "max_rating",
    "risk_eq",
    "decay_eq",
    "n_overlapping_stressors",
    "aoi_vector_path",
]

VALID_RISK_EQ  = {"Euclidean", "Multiplicative"}
VALID_DECAY_EQ = {"exponential", "linear", "none"}


async def run_hra(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    risk_eq = params["risk_eq"]
    if risk_eq not in VALID_RISK_EQ:
        raise CSISError(
            f"risk_eq must be one of {VALID_RISK_EQ}, got '{risk_eq}'",
            "INVALID_PARAMS",
        )
    decay_eq = params["decay_eq"]
    if decay_eq not in VALID_DECAY_EQ:
        raise CSISError(
            f"decay_eq must be one of {VALID_DECAY_EQ}, got '{decay_eq}'",
            "INVALID_PARAMS",
        )
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("hra", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":            workspace_dir,
        "results_suffix":           params.get("results_suffix", ""),
        "info_table_path":          params["info_table_path"],
        "criteria_table_path":      params["criteria_table_path"],
        "resolution":               float(params["resolution"]),
        "max_rating":               float(params["max_rating"]),
        "risk_eq":                  risk_eq,
        "decay_eq":                 decay_eq,
        "n_overlapping_stressors":  int(params["n_overlapping_stressors"]),
        "aoi_vector_path":          params["aoi_vector_path"],
        "visualize_outputs":        bool(params.get("visualize_outputs", False)),
    }

    progress_callback(20, "Running InVEST Habitat Risk Assessment model...")
    try:
        with _patched_zonal_statistics():
            natcap.invest.hra.execute(invest_args)
    except Exception as e:
        raise CSISError(f"HRA model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "hra")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
