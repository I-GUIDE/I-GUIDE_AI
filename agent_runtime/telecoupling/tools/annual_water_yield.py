"""
Tool: Annual Water Yield (natcap.invest.annual_water_yield).

Required: lulc_path, depth_to_root_rest_layer_path, precipitation_path,
          pawc_path, eto_path, watersheds_path, biophysical_table_path.
Optional: sub_watersheds_path, demand_table_path, valuation_table_path.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.annual_water_yield

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "lulc_path",
    "depth_to_root_rest_layer_path",
    "precipitation_path",
    "pawc_path",
    "eto_path",
    "watersheds_path",
    "biophysical_table_path",
]


async def run_annual_water_yield(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("annual_water_yield", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":                  workspace_dir,
        "results_suffix":                 params.get("results_suffix", ""),
        "lulc_path":                      params["lulc_path"],
        "depth_to_root_rest_layer_path":  params["depth_to_root_rest_layer_path"],
        "precipitation_path":             params["precipitation_path"],
        "pawc_path":                      params["pawc_path"],
        "eto_path":                       params["eto_path"],
        "watersheds_path":                params["watersheds_path"],
        "biophysical_table_path":         params["biophysical_table_path"],
        "seasonality_constant":           params.get("seasonality_constant", 15),
        "sub_watersheds_path":            params.get("sub_watersheds_path", ""),
        "demand_table_path":              params.get("demand_table_path", ""),
        "valuation_table_path":           params.get("valuation_table_path", ""),
    }

    progress_callback(20, "Running InVEST Annual Water Yield model...")
    try:
        natcap.invest.annual_water_yield.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Annual Water Yield model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "annual_water_yield")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
