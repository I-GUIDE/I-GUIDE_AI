"""
Tool: Habitat Quality (natcap.invest.habitat_quality).

Required: lulc_cur_path, threats_table_path, sensitivity_table_path.
Optional: lulc_fut_path, lulc_bas_path, access_vector_path, half_saturation_constant.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.habitat_quality

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["lulc_cur_path", "threats_table_path", "sensitivity_table_path"]


async def run_habitat_quality(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("habitat_quality", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":          workspace_dir,
        "results_suffix":         params.get("results_suffix", ""),
        "lulc_cur_path":          params["lulc_cur_path"],
        "threats_table_path":     params["threats_table_path"],
        "sensitivity_table_path": params["sensitivity_table_path"],
        "lulc_fut_path":          params.get("lulc_fut_path", ""),
        "lulc_bas_path":          params.get("lulc_bas_path", ""),
        "access_vector_path":     params.get("access_vector_path", ""),
        "half_saturation_constant": params.get("half_saturation_constant", 0.5),
    }

    progress_callback(20, "Running InVEST Habitat Quality model...")
    try:
        natcap.invest.habitat_quality.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Habitat Quality model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "habitat_quality")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
