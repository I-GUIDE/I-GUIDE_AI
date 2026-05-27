"""
Tool 1: Network Analysis Grouping (R + igraph).
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Callable

from agent_runtime.telecoupling.config import settings
from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, run_r_script, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "nodes_table", "links_table", "shapefile_path",
    "nodes_join_attri", "layer_join_attri", "clustering_algorithm",
]


async def run_network_analysis(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, folder_name = generate_output_dir("network_analysis", session_id)
    progress_callback(10, "Created output directory")

    script_path = str(Path(__file__).parent.parent / "r_scripts" / "network_analysis.R")

    config = {
        "nodes_table":          params["nodes_table"],
        "links_table":          params["links_table"],
        "shapefile_path":       params["shapefile_path"],
        "nodes_join_attri":     params["nodes_join_attri"],
        "layer_join_attri":     params["layer_join_attri"],
        "clustering_algorithm": params["clustering_algorithm"],
        "weight_within":        params.get("weight_within_clusters", 10),
        "weight_between":       params.get("weight_between_clusters", 2),
        "color_set":            params.get("color_set", "Set3"),
        "node_size":            params.get("node_size", 0.05),
        "edge_width":           params.get("edge_width", 0.833333),
        "label_size":           params.get("label_size", 0.8),
        "out_pdf":              os.path.join(workspace_dir, f"network_plot_{task_id}.pdf"),
        "out_csv":              os.path.join(workspace_dir, f"network_stats_{task_id}.csv"),
        "out_shp":              os.path.join(workspace_dir, f"output_{task_id}.shp"),
    }

    progress_callback(20, "Running R network analysis...")
    run_r_script(script_path, config, session_id, task_id)
    progress_callback(80, "R script completed")

    progress_callback(85, "Generating previews...")
    files = await scan_output_directory(workspace_dir, "network_analysis")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
