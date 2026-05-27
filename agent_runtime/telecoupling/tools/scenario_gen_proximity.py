"""
Tool: Scenario Generator — Proximity Based (natcap.invest.scenario_gen_proximity).

Required: base_lulc_path, replacement_lucode, area_to_convert,
          focal_landcover_codes, convertible_landcover_codes.
At least one of convert_nearest_to_edge or convert_farthest_from_edge must be True.
Optional: aoi_path, n_workers.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.scenario_gen_proximity

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "base_lulc_path",
    "replacement_lucode",
    "area_to_convert",
    "focal_landcover_codes",
    "convertible_landcover_codes",
]


def _normalize_codes(value) -> str:
    """Coerce LULC codes into the space-separated string InVEST expects.

    The LLM may pass a list, a comma-separated string, or a space-separated
    string. InVEST's scenario_gen_proximity parses these via str.split() and
    int(), so anything but space-separated integers raises ValueError.
    """
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).strip("[]()").replace(",", " ").split()
    return " ".join(str(int(x)) for x in items)


async def run_scenario_gen_proximity(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    nearest  = bool(params.get("convert_nearest_to_edge", True))
    farthest = bool(params.get("convert_farthest_from_edge", False))
    if not nearest and not farthest:
        raise CSISError(
            "At least one of convert_nearest_to_edge or convert_farthest_from_edge must be True.",
            "INVALID_PARAMS",
        )
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("scenario_gen_proximity", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":               workspace_dir,
        "results_suffix":              params.get("results_suffix", ""),
        "base_lulc_path":              params["base_lulc_path"],
        "replacement_lucode":          int(params["replacement_lucode"]),
        "area_to_convert":             float(params["area_to_convert"]),
        "focal_landcover_codes":       _normalize_codes(params["focal_landcover_codes"]),
        "convertible_landcover_codes": _normalize_codes(params["convertible_landcover_codes"]),
        "convert_nearest_to_edge":     nearest,
        "convert_farthest_from_edge":  farthest,
        "aoi_path":                    params.get("aoi_path", ""),
        "n_fragmentation_steps":       int(params.get("n_steps", 1)),
    }

    progress_callback(20, "Running InVEST Scenario Generator (Proximity)...")
    try:
        natcap.invest.scenario_gen_proximity.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Scenario Generator failed: {e}", "TOOL_FAILED")
    progress_callback(85, "Scenario generation completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "scenario_gen_proximity")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
