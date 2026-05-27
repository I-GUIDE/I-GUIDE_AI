"""
Tool 3: Coastal Blue Carbon Main Model (natcap.invest).

InVEST CBC outputs are placed in workspace_dir/output/.
Only that subdirectory is scanned — intermediate/ and taskgraph_cache/ are ignored.
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import natcap.invest.coastal_blue_carbon.coastal_blue_carbon

from agent_runtime.telecoupling.shared.utils import (
    CSISError, validate_required, generate_output_dir, scan_output_directory
)

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "landcover_snapshot_csv",
    "landcover_transitions_table",
    "biophysical_table_path",
]


async def run_cbc_main(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, folder_name = generate_output_dir("coastal_blue_carbon", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "landcover_snapshot_csv":      params["landcover_snapshot_csv"],
        "landcover_transitions_table": params["landcover_transitions_table"],
        "biophysical_table_path":      params["biophysical_table_path"],
        "results_suffix":              params.get("results_suffix", ""),
        "workspace_dir":               workspace_dir,
        "do_economic_analysis":        params.get("do_economic_analysis", False),
        "use_price_table":             params.get("use_price_table", False),
        "price_table_path":            params.get("price_table_path", ""),
        "price":                       params.get("price", 0.0),
        "discount_rate":               params.get("discount_rate", 0.0),
        "inflation_rate":              params.get("inflation_rate", 0.0),
    }
    if params.get("analysis_year"):
        invest_args["analysis_year"] = int(params["analysis_year"])

    progress_callback(20, "Running InVEST Coastal Blue Carbon model...")
    try:
        natcap.invest.coastal_blue_carbon.coastal_blue_carbon.execute(invest_args)
    except Exception as e:
        raise CSISError(f"CBC Main model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    # InVEST CBC places final results in workspace_dir/output/
    # Scan only that subdirectory to avoid intermediate/ files
    output_subdir = os.path.join(workspace_dir, "output")
    scan_dir = output_subdir if os.path.isdir(output_subdir) else workspace_dir

    progress_callback(90, "Generating previews...")
    files = await scan_output_directory(scan_dir, "coastal_blue_carbon")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
