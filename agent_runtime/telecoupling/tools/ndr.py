"""
Tool: NDR — Nutrient Delivery Ratio (natcap.invest.ndr.ndr).

Required: dem_path, lulc_path, runoff_proxy_path, watersheds_path,
          biophysical_table_path, threshold_flow_accumulation, k_param.
At least one of calc_n or calc_p must be True.
Nitrogen optional: subsurface_critical_length_n, subsurface_eff_n.
Phosphorus optional: subsurface_critical_length_p, subsurface_eff_p.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.ndr.ndr

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "dem_path",
    "lulc_path",
    "runoff_proxy_path",
    "watersheds_path",
    "biophysical_table_path",
    "threshold_flow_accumulation",
]


async def run_ndr(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    calc_n = bool(params.get("calc_n", True))
    calc_p = bool(params.get("calc_p", False))
    if not calc_n and not calc_p:
        raise CSISError("At least one of calc_n or calc_p must be True.", "INVALID_PARAMS")
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("ndr", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":               workspace_dir,
        "results_suffix":              params.get("results_suffix", ""),
        "dem_path":                    params["dem_path"],
        "lulc_path":                   params["lulc_path"],
        "runoff_proxy_path":           params["runoff_proxy_path"],
        "watersheds_path":             params["watersheds_path"],
        "biophysical_table_path":      params["biophysical_table_path"],
        "threshold_flow_accumulation": params["threshold_flow_accumulation"],
        "k_param":                     params.get("k_param", 2),
        "calc_n":                      calc_n,
        "calc_p":                      calc_p,
    }

    if calc_n:
        invest_args["subsurface_critical_length_n"] = params.get(
            "subsurface_critical_length_n", 150
        )
        invest_args["subsurface_eff_n"] = params.get("subsurface_eff_n", 0.8)

    if calc_p:
        invest_args["subsurface_critical_length_p"] = params.get(
            "subsurface_critical_length_p", 150
        )
        invest_args["subsurface_eff_p"] = params.get("subsurface_eff_p", 0.8)

    progress_callback(20, "Running InVEST NDR model...")
    try:
        natcap.invest.ndr.ndr.execute(invest_args)
    except Exception as e:
        raise CSISError(f"NDR model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "ndr")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
