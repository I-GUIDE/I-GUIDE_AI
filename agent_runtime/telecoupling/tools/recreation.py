"""
Tool: Visitation: Recreation and Tourism (natcap.invest.recreation.recmodel_client).

Connects to NatCap's remote server to fetch Flickr/Twitter photo-user-day (PUD/TUD)
data for the given AOI, then optionally runs a regression against local predictors.

Required: aoi_path, start_year, end_year.
Optional: grid_aoi, grid_type, cell_size, compute_regression,
          predictor_table_path, scenario_predictor_table_path.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.recreation.recmodel_client

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["aoi_path", "start_year", "end_year"]

VALID_GRID_TYPES = {"square", "hexagon"}


async def run_recreation(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    start_year = int(params["start_year"])
    end_year = int(params["end_year"])
    if not (2005 <= start_year <= 2017):
        raise CSISError("start_year must be between 2005 and 2017", "INVALID_PARAMS")
    if not (2005 <= end_year <= 2017):
        raise CSISError("end_year must be between 2005 and 2017", "INVALID_PARAMS")
    if end_year < start_year:
        raise CSISError("end_year must be >= start_year", "INVALID_PARAMS")

    grid_aoi = bool(params.get("grid_aoi", False))
    if grid_aoi:
        grid_type = params.get("grid_type", "hexagon")
        if grid_type not in VALID_GRID_TYPES:
            raise CSISError(
                f"grid_type must be one of {VALID_GRID_TYPES}", "INVALID_PARAMS"
            )
        if not params.get("cell_size"):
            raise CSISError("cell_size is required when grid_aoi=True", "INVALID_PARAMS")

    compute_regression = bool(params.get("compute_regression", False))
    if compute_regression and not params.get("predictor_table_path"):
        raise CSISError(
            "predictor_table_path is required when compute_regression=True", "INVALID_PARAMS"
        )

    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("recreation", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":      workspace_dir,
        "results_suffix":     params.get("results_suffix", ""),
        "aoi_path":           params["aoi_path"],
        "start_year":         start_year,
        "end_year":           end_year,
        "grid_aoi":           grid_aoi,
        "compute_regression": compute_regression,
    }

    if grid_aoi:
        invest_args["grid_type"] = params.get("grid_type", "hexagon")
        invest_args["cell_size"] = float(params["cell_size"])

    if compute_regression:
        invest_args["predictor_table_path"] = params["predictor_table_path"]
        if params.get("scenario_predictor_table_path"):
            invest_args["scenario_predictor_table_path"] = params["scenario_predictor_table_path"]

    progress_callback(20, "Contacting NatCap recreation server (this may take several minutes)...")
    try:
        natcap.invest.recreation.recmodel_client.execute(invest_args)
    except ConnectionError as e:
        raise CSISError(
            "Cannot reach NatCap recreation server. The server may be temporarily unavailable — please try again later.",
            "TOOL_FAILED"
        )
    except Exception as e:
        err = str(e)
        if "cannot connect" in err.lower() or "connection" in err.lower() or "timeout" in err.lower():
            raise CSISError(
                "Cannot reach NatCap recreation server. The server may be temporarily unavailable — please try again later.",
                "TOOL_FAILED"
            )
        raise CSISError(f"Recreation model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "recreation")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
