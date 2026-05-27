"""
Tool: Scenic Quality (natcap.invest.scenic_quality.scenic_quality).

Required: aoi_vector_path, structure_vector_path, dem_path.
Optional: refractivity_coefficient (default 0.13), do_valuation,
          valuation_function, a_coef, b_coef, max_valuation_radius.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.scenic_quality.scenic_quality

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["aoi_vector_path", "structure_vector_path", "dem_path"]


async def run_scenic_quality(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    do_valuation = bool(params.get("do_valuation", False))
    if do_valuation:
        for key in ["valuation_function", "a_coef", "b_coef"]:
            if params.get(key) is None:
                raise CSISError(
                    f"do_valuation=true requires '{key}'", "INVALID_PARAMS"
                )
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("scenic_quality", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":  workspace_dir,
        "results_suffix": params.get("results_suffix", ""),
        "aoi_path":       params["aoi_vector_path"],
        "structure_path": params["structure_vector_path"],
        "dem_path":       params["dem_path"],
        "refraction":     params.get("refractivity_coefficient", 0.13),
        "do_valuation":   do_valuation,
    }

    if do_valuation:
        invest_args.update({
            "valuation_function":   params["valuation_function"],
            "a_coef":               params["a_coef"],
            "b_coef":               params["b_coef"],
            "max_valuation_radius": params.get("max_valuation_radius", ""),
        })

    progress_callback(20, "Running InVEST Scenic Quality model...")
    try:
        natcap.invest.scenic_quality.scenic_quality.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Scenic Quality model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "scenic_quality")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
