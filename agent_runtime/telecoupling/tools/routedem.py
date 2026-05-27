"""
Tool: RouteDEM — DEM hydrological routing (natcap.invest.routedem).

Required: dem_path. At least one of calculate_* flags must be True.
Optional: algorithm ('D8'|'MFD'), calculate_flow_direction, calculate_flow_accumulation,
          calculate_stream_threshold (+ threshold_flow_accumulation), calculate_slope,
          calculate_stream_order, calculate_downstream_distance.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.routedem

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["dem_path"]


async def run_routedem(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("routedem", session_id)
    progress_callback(10, "Created output directory")

    calc_flow_dir  = bool(params.get("calculate_flow_direction", True))
    calc_flow_acc  = bool(params.get("calculate_flow_accumulation", True))
    calc_stream    = bool(params.get("calculate_stream_threshold", False))
    calc_slope     = bool(params.get("calculate_slope", False))
    calc_order     = bool(params.get("calculate_stream_order", False))
    calc_downstream = bool(params.get("calculate_downstream_distance", False))

    invest_args = {
        "workspace_dir":                   workspace_dir,
        "results_suffix":                  params.get("results_suffix", ""),
        "dem_path":                        params["dem_path"],
        "algorithm":                       params.get("algorithm", "D8"),
        "calculate_flow_direction":        calc_flow_dir,
        "calculate_flow_accumulation":     calc_flow_acc,
        "calculate_stream_threshold":      calc_stream,
        "calculate_slope":                 calc_slope,
        "calculate_stream_order":          calc_order,
        "calculate_downstream_distance":   calc_downstream,
    }
    if calc_stream:
        invest_args["threshold_flow_accumulation"] = params.get(
            "threshold_flow_accumulation", 1000
        )

    progress_callback(20, "Running InVEST RouteDEM...")
    try:
        natcap.invest.routedem.execute(invest_args)
    except Exception as e:
        raise CSISError(f"RouteDEM failed: {e}", "TOOL_FAILED")
    progress_callback(85, "Routing completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "routedem")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
