"""
Tool: Urban Flood Risk Mitigation (natcap.invest.urban_flood_risk_mitigation).

Required: aoi_watersheds_path, rainfall_depth, lulc_path,
          soils_hydrological_group_raster_path, curve_number_table_path.
Optional: built_infrastructure_vector_path, infrastructure_damage_loss_table_path.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.urban_flood_risk_mitigation

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "aoi_watersheds_path",
    "rainfall_depth",
    "lulc_path",
    "soils_hydrological_group_raster_path",
    "curve_number_table_path",
]


async def run_urban_flood(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("urban_flood", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":                       workspace_dir,
        "results_suffix":                      params.get("results_suffix", ""),
        "aoi_watersheds_path":                 params["aoi_watersheds_path"],
        "rainfall_depth":                      params["rainfall_depth"],
        "lulc_path":                           params["lulc_path"],
        "soils_hydrological_group_raster_path": params["soils_hydrological_group_raster_path"],
        "curve_number_table_path":             params["curve_number_table_path"],
        "built_infrastructure_vector_path":    params.get("built_infrastructure_vector_path", ""),
        "infrastructure_damage_loss_table_path": params.get(
            "infrastructure_damage_loss_table_path", ""
        ),
    }

    progress_callback(20, "Running InVEST Urban Flood Risk Mitigation model...")
    try:
        natcap.invest.urban_flood_risk_mitigation.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Urban Flood model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "urban_flood")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
