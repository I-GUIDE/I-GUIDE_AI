"""
Tool: Urban Cooling Model (natcap.invest.urban_cooling_model).

Required: lulc_raster_path, ref_eto_raster_path, aoi_vector_path,
          biophysical_table_path, green_area_cooling_distance, t_ref, uhi_max.
Optional: cc_method ('factors'|'intensity'), t_air_average_radius,
          building_vector_path, avg_rel_humidity, energy_consumption_table_path,
          cc_weight_shade, cc_weight_albedo, cc_weight_eti.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.urban_cooling_model

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "lulc_raster_path",
    "ref_eto_raster_path",
    "aoi_vector_path",
    "biophysical_table_path",
    "green_area_cooling_distance",
    "t_ref",
    "uhi_max",
]


async def run_urban_cooling(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("urban_cooling", session_id)
    progress_callback(10, "Created output directory")

    cc_method = params.get("cc_method", "factors")

    invest_args = {
        "workspace_dir":               workspace_dir,
        "results_suffix":              params.get("results_suffix", ""),
        "lulc_raster_path":            params["lulc_raster_path"],
        "ref_eto_raster_path":         params["ref_eto_raster_path"],
        "aoi_vector_path":             params["aoi_vector_path"],
        "biophysical_table_path":      params["biophysical_table_path"],
        "green_area_cooling_distance": params["green_area_cooling_distance"],
        "t_ref":                       params["t_ref"],
        "uhi_max":                     params["uhi_max"],
        "cc_method":                   cc_method,
        "t_air_average_radius":        params.get("t_air_average_radius", 2000),
        "building_vector_path":        params.get("building_vector_path", ""),
        "avg_rel_humidity":            params.get("avg_rel_humidity", 30),
        "energy_consumption_table_path": params.get("energy_consumption_table_path", ""),
        "do_energy_valuation":         bool(params.get("do_energy_valuation", False)),
        "do_productivity_valuation":   bool(params.get("do_productivity_valuation", False)),
    }

    if cc_method == "factors":
        invest_args.update({
            "cc_weight_shade":  params.get("cc_weight_shade", 0.6),
            "cc_weight_albedo": params.get("cc_weight_albedo", 0.2),
            "cc_weight_eti":    params.get("cc_weight_eti", 0.2),
        })

    progress_callback(20, "Running InVEST Urban Cooling model...")
    try:
        natcap.invest.urban_cooling_model.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Urban Cooling model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "urban_cooling")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
