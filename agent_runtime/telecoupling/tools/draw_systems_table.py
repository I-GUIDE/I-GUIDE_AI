"""
Tool: Draw Systems from Table — Upload a systems coordinate CSV and render as
point features. Batch version of Add Systems Interactively.
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["input_csv", "x_field", "y_field"]


async def run_draw_systems_from_table(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    input_csv = params["input_csv"]
    x_field = params["x_field"].strip()
    y_field = params["y_field"].strip()
    crs = params.get("crs", "EPSG:4326")

    if not os.path.exists(input_csv):
        raise CSISError(f"Input CSV not found: {input_csv}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading systems table...")
    df = pd.read_csv(input_csv)

    for col in [x_field, y_field]:
        if col not in df.columns:
            raise CSISError(f"Column '{col}' not found. Available: {list(df.columns)}", "INVALID_PARAMS")

    progress_callback(40, "Creating system point features from table...")
    geoms = [Point(float(row[x_field]), float(row[y_field]))
             if pd.notna(row[x_field]) and pd.notna(row[y_field]) else None
             for _, row in df.iterrows()]

    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs=crs)
    gdf = gdf[gdf.geometry.notna()].copy()
    gdf["POINT_X"] = gdf.geometry.x
    gdf["POINT_Y"] = gdf.geometry.y

    workspace_dir, _ = generate_output_dir("draw_systems_table", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(70, "Writing outputs...")

    gdf.to_file(os.path.join(workspace_dir, "systems_from_table.geojson"), driver="GeoJSON")
    gdf.to_file(os.path.join(workspace_dir, "systems_from_table.shp"))
    gdf.drop(columns="geometry").to_csv(os.path.join(workspace_dir, "systems_from_table.csv"), index=False)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "draw_systems_table")
    return {"files": files}
