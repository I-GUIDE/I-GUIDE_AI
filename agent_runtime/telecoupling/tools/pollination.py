"""
Tool: Crop Pollination (natcap.invest.pollination).

Required: lulc_path, guild_table_path, landcover_biophysical_table_path.
Optional: farm_vector_path.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.pollination

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["landcover_raster_path", "guild_table_path", "landcover_biophysical_table_path"]


async def run_pollination(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("pollination", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":                    workspace_dir,
        "results_suffix":                   params.get("results_suffix", ""),
        "landcover_raster_path":            params["landcover_raster_path"],
        "guild_table_path":                 params["guild_table_path"],
        "landcover_biophysical_table_path": params["landcover_biophysical_table_path"],
        "farm_vector_path":                 params.get("farm_vector_path", ""),
    }

    progress_callback(20, "Running InVEST Pollination model...")
    try:
        natcap.invest.pollination.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Pollination model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "pollination")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
