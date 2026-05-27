"""
Tool: Forest Carbon Edge Effect (natcap.invest.forest_carbon_edge_effect).

Required: lulc_raster_path, biophysical_table_path,
          tropical_forest_edge_carbon_model_vector_path.
Optional: aoi_vector_path, pools_to_calculate, n_nearest_model_points,
          biomass_to_carbon_conversion_factor, compute_forest_edge_effects.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.forest_carbon_edge_effect

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "lulc_raster_path",
    "biophysical_table_path",
    "tropical_forest_edge_carbon_model_vector_path",
]


async def run_forest_carbon_edge(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("forest_carbon_edge_effect", session_id)
    progress_callback(10, "Created output directory")

    invest_args = {
        "workspace_dir":    workspace_dir,
        "results_suffix":   params.get("results_suffix", ""),
        "lulc_raster_path": params["lulc_raster_path"],
        "biophysical_table_path": params["biophysical_table_path"],
        "tropical_forest_edge_carbon_model_vector_path":
            params["tropical_forest_edge_carbon_model_vector_path"],
        "aoi_vector_path":  params.get("aoi_vector_path", ""),
        "pools_to_calculate": params.get("pools_to_calculate", "all"),
        "compute_forest_edge_effects": params.get("compute_forest_edge_effects", True),
        "n_nearest_model_points": params.get("n_nearest_model_points", 10),
        "biomass_to_carbon_conversion_factor": params.get(
            "biomass_to_carbon_conversion_factor", 0.47
        ),
    }

    progress_callback(20, "Running InVEST Forest Carbon Edge Effect model...")
    try:
        natcap.invest.forest_carbon_edge_effect.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Forest Carbon Edge Effect model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "forest_carbon_edge_effect")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
