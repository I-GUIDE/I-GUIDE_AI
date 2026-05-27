"""
Tool 4: Seasonal Water Yield (natcap.invest).
IMPORTANT: precip/et0 files may have non-standard names, use glob+copy rename.
  Input files: *_N.tif (N=1..12)
  Required:    precip_mN.tif / et0_mN.tif (N=1..12)
"""
from __future__ import annotations
import glob
import logging
import os
import shutil
import sys
import tempfile
from typing import Callable

import natcap.invest.seasonal_water_yield.seasonal_water_yield

from agent_runtime.telecoupling.shared.utils import (
    CSISError, validate_required, generate_output_dir, scan_output_directory
)

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "aoi_path", "lulc_raster_path", "dem_raster_path", "soil_group_path",
    "biophysical_table_path", "precip_dir", "et0_dir",
    "rain_events_table_path", "threshold_flow_accumulation",
]


def prepare_monthly_dir(src_dir: str, prefix_out: str) -> str:
    """
    Create a temp directory with monthly rasters renamed to InVEST-expected format.
    src files: *_N.tif (N=1..12)
    dst files: {prefix_out}N.tif  e.g. precip_m1.tif, et0_m1.tif
    Returns path to temp directory.

    When precip and et0 files share the same directory, prefer files whose
    basename contains the type keyword ("precip" or "et0").
    """
    # Derive a keyword to prefer when multiple files match (e.g. "precip" or "et0")
    type_keyword = "precip" if "precip" in prefix_out.lower() else (
        "et0" if "et0" in prefix_out.lower() else None
    )

    tmp = tempfile.mkdtemp(prefix=f"csis_{prefix_out}_")
    for month in range(1, 13):
        pattern = os.path.join(src_dir, f"*_{month}.tif")
        matches = glob.glob(pattern)
        if not matches:
            shutil.rmtree(tmp, ignore_errors=True)
            raise CSISError(
                f"Missing month {month} raster in {src_dir} (pattern: *_{month}.tif)",
                "FILE_NOT_FOUND",
            )
        # If multiple candidates, prefer the one whose name contains the type keyword
        if type_keyword and len(matches) > 1:
            preferred = [m for m in matches if type_keyword.lower() in os.path.basename(m).lower()]
            src = preferred[0] if preferred else matches[0]
        else:
            src = matches[0]
        dst = os.path.join(tmp, f"{prefix_out}{month}.tif")
        if sys.platform != "win32":
            os.symlink(os.path.abspath(src), dst)
        else:
            shutil.copy2(src, dst)
    return tmp


async def run_seasonal_water_yield(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, folder_name = generate_output_dir("seasonal_water_yield", session_id)
    progress_callback(10, "Created output directory")

    tmp_precip = None
    tmp_et0 = None
    try:
        progress_callback(15, "Preparing monthly precipitation rasters...")
        tmp_precip = prepare_monthly_dir(params["precip_dir"], "precip_m")

        progress_callback(20, "Preparing monthly ET0 rasters...")
        tmp_et0 = prepare_monthly_dir(params["et0_dir"], "et0_m")

        invest_args = {
            "alpha_m":                     float(params.get("alpha_m", 0.083333)),
            "aoi_path":                    params["aoi_path"],
            "beta_i":                      float(params.get("beta_i", 1.0)),
            "biophysical_table_path":      params["biophysical_table_path"],
            "climate_zone_raster_path":    params.get("climate_zone_raster_path", ""),
            "climate_zone_table_path":     params.get("climate_zone_table_path", ""),
            "dem_raster_path":             params["dem_raster_path"],
            "et0_dir":                     tmp_et0,
            "gamma":                       float(params.get("gamma", 1.0)),
            "l_path":                      params.get("l_path", ""),
            "lulc_raster_path":            params["lulc_raster_path"],
            "monthly_alpha":               bool(params.get("monthly_alpha", False)),
            "monthly_alpha_path":          params.get("monthly_alpha_path", ""),
            "precip_dir":                  tmp_precip,
            "rain_events_table_path":      params["rain_events_table_path"],
            "results_suffix":              params.get("results_suffix", ""),
            "soil_group_path":             params["soil_group_path"],
            "threshold_flow_accumulation": int(params["threshold_flow_accumulation"]),
            "user_defined_climate_zones":  bool(params.get("user_defined_climate_zones", False)),
            "user_defined_local_recharge": bool(params.get("user_defined_local_recharge", False)),
            "workspace_dir":               workspace_dir,
        }

        progress_callback(25, "Running InVEST Seasonal Water Yield model...")
        try:
            natcap.invest.seasonal_water_yield.seasonal_water_yield.execute(invest_args)
        except Exception as e:
            raise CSISError(f"Seasonal Water Yield failed: {e}", "TOOL_FAILED")
        progress_callback(85, "InVEST model completed")

    finally:
        if tmp_precip and os.path.isdir(tmp_precip):
            shutil.rmtree(tmp_precip, ignore_errors=True)
        if tmp_et0 and os.path.isdir(tmp_et0):
            shutil.rmtree(tmp_et0, ignore_errors=True)

    progress_callback(90, "Generating previews...")
    files = await scan_output_directory(workspace_dir, "seasonal_water_yield")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
