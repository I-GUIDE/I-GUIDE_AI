"""
Tool: Draw Radial Flows — Generate flow lines (GeoJSON/shapefile) from a CSV table
of origin-destination XY coordinate pairs.
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["input_csv", "from_x_field", "from_y_field", "to_x_field", "to_y_field"]


async def run_draw_radial_flows(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    input_csv = params["input_csv"]
    from_x = params["from_x_field"].strip()
    from_y = params["from_y_field"].strip()
    to_x = params["to_x_field"].strip()
    to_y = params["to_y_field"].strip()
    crs = params.get("crs", "EPSG:4326")

    if not os.path.exists(input_csv):
        raise CSISError(f"Input CSV not found: {input_csv}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading flow table...")
    df = pd.read_csv(input_csv)

    for col in [from_x, from_y, to_x, to_y]:
        if col not in df.columns:
            raise CSISError(f"Column '{col}' not found. Available: {list(df.columns)}", "INVALID_PARAMS")

    progress_callback(40, "Building flow lines...")
    geoms = []
    for _, row in df.iterrows():
        try:
            line = LineString([(float(row[from_x]), float(row[from_y])),
                               (float(row[to_x]), float(row[to_y]))])
            geoms.append(line)
        except (ValueError, TypeError):
            geoms.append(None)

    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs=crs)
    gdf = gdf[gdf.geometry.notna()]

    workspace_dir, _ = generate_output_dir("radial_flows", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(70, "Writing outputs...")

    geojson_path = os.path.join(workspace_dir, "radial_flows.geojson")
    gdf.to_file(geojson_path, driver="GeoJSON")

    shp_path = os.path.join(workspace_dir, "radial_flows.shp")
    gdf.to_file(shp_path)

    summary_csv = os.path.join(workspace_dir, "radial_flows_summary.csv")
    pd.DataFrame([{
        "total_flows": len(gdf),
        "null_skipped": len(df) - len(gdf),
        "crs": crs,
    }]).to_csv(summary_csv, index=False)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "radial_flows")
    return {"files": files}
