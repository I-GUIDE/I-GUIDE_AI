"""
Tool: Urban Stormwater Retention (natcap.invest.stormwater).

Required: lulc_path, soil_group_path, precipitation_path, biophysical_table.
Optional: adjust_retention_ratios (bool; if true, also retention_radius + road_centerlines_path),
          aggregate_areas_path, replacement_cost.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.stormwater

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "lulc_path",
    "soil_group_path",
    "precipitation_path",
    "biophysical_table",
]


async def run_urban_stormwater(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    adjust = bool(params.get("adjust_retention_ratios", False))
    if adjust and not params.get("retention_radius"):
        raise CSISError(
            "adjust_retention_ratios=true requires 'retention_radius'.", "INVALID_PARAMS"
        )
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("urban_stormwater", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":             workspace_dir,
        "results_suffix":            params.get("results_suffix", ""),
        "lulc_path":                 params["lulc_path"],
        "soil_group_path":           params["soil_group_path"],
        "precipitation_path":        params["precipitation_path"],
        "biophysical_table":         params["biophysical_table"],
        "adjust_retention_ratios":   adjust,
        "aggregate_areas_path":      params.get("aggregate_areas_path", ""),
        "replacement_cost":          params.get("replacement_cost", ""),
    }

    if adjust:
        invest_args["retention_radius"]        = params["retention_radius"]
        invest_args["road_centerlines_path"]   = params.get("road_centerlines_path", "")

    progress_callback(20, "Running InVEST Urban Stormwater Retention model...")
    try:
        natcap.invest.stormwater.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Urban Stormwater model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "urban_stormwater")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
