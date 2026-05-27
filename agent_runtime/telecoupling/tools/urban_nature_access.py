"""
Tool: Urban Nature Access (natcap.invest.urban_nature_access).

Required: lulc_raster_path, lulc_attribute_table, population_raster_path,
          admin_boundaries_vector_path, search_radius_mode, decay_function.
- search_radius_mode='uniform radius': also requires search_radius (meters).
- search_radius_mode='radius per population group': also requires population_group_radii_table.
- search_radius_mode='radius per urban nature class': radius encoded in lulc_attribute_table.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.urban_nature_access

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "lulc_raster_path",
    "lulc_attribute_table",
    "population_raster_path",
    "admin_boundaries_vector_path",
]

RADIUS_UNIFORM   = "uniform radius"
RADIUS_POP_GROUP = "radius per population group"
RADIUS_NATURE    = "radius per urban nature class"


async def run_urban_nature_access(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    mode = params.get("search_radius_mode", RADIUS_UNIFORM)
    if mode == RADIUS_UNIFORM and params.get("search_radius") is None:
        raise CSISError(
            "search_radius_mode='uniform radius' requires 'search_radius' (meters).",
            "INVALID_PARAMS",
        )
    if mode == RADIUS_POP_GROUP and not params.get("population_group_radii_table"):
        raise CSISError(
            "search_radius_mode='radius per population group' requires "
            "'population_group_radii_table'.",
            "INVALID_PARAMS",
        )
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("urban_nature_access", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":                  workspace_dir,
        "results_suffix":                 params.get("results_suffix", ""),
        "lulc_raster_path":               params["lulc_raster_path"],
        "lulc_attribute_table":           params["lulc_attribute_table"],
        "population_raster_path":         params["population_raster_path"],
        "admin_boundaries_vector_path":   params["admin_boundaries_vector_path"],
        "search_radius_mode":             mode,
        "decay_function":                 params.get("decay_function", "gaussian"),
        "urban_nature_demand":            float(params.get("urban_nature_demand", 250)),
    }

    if mode == RADIUS_UNIFORM:
        invest_args["search_radius"] = params["search_radius"]
    elif mode == RADIUS_POP_GROUP:
        invest_args["population_group_radii_table"] = params["population_group_radii_table"]

    progress_callback(20, "Running InVEST Urban Nature Access model...")
    try:
        natcap.invest.urban_nature_access.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Urban Nature Access model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "urban_nature_access")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
