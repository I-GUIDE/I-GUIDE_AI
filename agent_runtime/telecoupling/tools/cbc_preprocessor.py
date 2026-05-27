"""
Tool 2: Coastal Blue Carbon Preprocessor (natcap.invest).
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.coastal_blue_carbon.preprocessor

from agent_runtime.telecoupling.shared.utils import (
    CSISError, validate_required, generate_output_dir, scan_output_directory
)

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["landcover_snapshot_csv", "landcover_lookup_table"]


async def run_cbc_preprocessor(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, folder_name = generate_output_dir("coastal_blue_carbon_preprocessor", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "landcover_snapshot_csv": params["landcover_snapshot_csv"],
        "lulc_lookup_table_path": params["landcover_lookup_table"],
        "results_suffix":         params.get("results_suffix", ""),
        "workspace_dir":          workspace_dir,
    }

    progress_callback(20, "Running InVEST CBC Preprocessor...")
    try:
        natcap.invest.coastal_blue_carbon.preprocessor.execute(invest_args)
    except Exception as e:
        raise CSISError(f"CBC Preprocessor failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Generating previews...")
    files = await scan_output_directory(workspace_dir, "coastal_blue_carbon_preprocessor")
    progress_callback(100, "Done")

    warning = (
        "transitions CSV requires manual editing before running Tool 3 (CBC Main). "
        "Edit the transitions_*.csv file to assign 'accumulation', 'disturb', or 'NCC' "
        "for each LULC transition cell."
    )
    return {"success": True, "files": files, "warning": warning}
