"""
Tool: SDR — Sediment Delivery Ratio (natcap.invest.sdr.sdr).

Required: dem_path, erosivity_path, erodibility_path, lulc_path,
          watersheds_path, biophysical_table_path, threshold_flow_accumulation,
          k_param, sdr_max, ic_0_param, l_max.
Optional: drainage_path.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.sdr.sdr

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "dem_path",
    "erosivity_path",
    "erodibility_path",
    "lulc_path",
    "watersheds_path",
    "biophysical_table_path",
    "threshold_flow_accumulation",
]


async def run_sdr(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("sdr", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":               workspace_dir,
        "results_suffix":              params.get("results_suffix", ""),
        "dem_path":                    params["dem_path"],
        "erosivity_path":              params["erosivity_path"],
        "erodibility_path":            params["erodibility_path"],
        "lulc_path":                   params["lulc_path"],
        "watersheds_path":             params["watersheds_path"],
        "biophysical_table_path":      params["biophysical_table_path"],
        "threshold_flow_accumulation": params["threshold_flow_accumulation"],
        "k_param":                     params.get("k_param", 2),
        "sdr_max":                     params.get("sdr_max", 0.8),
        "ic_0_param":                  params.get("ic_0_param", 0.5),
        "l_max":                       params.get("l_max", 122),
        "drainage_path":               params.get("drainage_path", ""),
    }

    progress_callback(20, "Running InVEST SDR model...")
    try:
        natcap.invest.sdr.sdr.execute(invest_args)
    except Exception as e:
        raise CSISError(f"SDR model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "sdr")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
