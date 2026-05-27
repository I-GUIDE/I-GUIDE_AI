"""
Tool: DelineateIt — watershed delineation (natcap.invest.delineateit).

Required: dem_path. Either outlet_vector_path OR detect_pour_points=true.
Optional: snap_points (bool), flow_threshold, snap_distance.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.delineateit.delineateit

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["dem_path"]


async def run_delineateit(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    detect_pour = bool(params.get("detect_pour_points", False))
    if not detect_pour and not params.get("outlet_vector_path"):
        raise CSISError(
            "Either outlet_vector_path or detect_pour_points=true is required.",
            "INVALID_PARAMS",
        )
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("delineateit", session_id)
    progress_callback(10, "Created output directory")

    snap_points = bool(params.get("snap_points", False))
    invest_args = {
        "workspace_dir":      workspace_dir,
        "results_suffix":     params.get("results_suffix", ""),
        "dem_path":           params["dem_path"],
        "detect_pour_points": detect_pour,
        "outlet_vector_path": params.get("outlet_vector_path", ""),
        "snap_points":        snap_points,
    }
    if snap_points:
        invest_args["flow_threshold"] = params.get("flow_threshold", 1000)
        invest_args["snap_distance"]  = params.get("snap_distance", 20)

    progress_callback(20, "Running InVEST DelineateIt...")
    try:
        natcap.invest.delineateit.delineateit.execute(invest_args)
    except Exception as e:
        raise CSISError(f"DelineateIt failed: {e}", "TOOL_FAILED")
    progress_callback(85, "Delineation completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "delineateit")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
