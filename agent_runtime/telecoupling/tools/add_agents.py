"""
Tool: Add Agents Interactively — Create a point feature layer representing telecoupling agents
from a CSV table with Name, X (longitude), Y (latitude), and optional Text/description columns.

Replaces the ArcGIS interactive click-to-place workflow: user provides coordinates in CSV.
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


async def run_add_agents_interactively(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    input_csv = params["input_csv"]
    x_field = params["x_field"].strip()
    y_field = params["y_field"].strip()
    name_field = params.get("name_field", "Name")
    text_field = params.get("text_field", None)
    crs = params.get("crs", "EPSG:4326")

    if not os.path.exists(input_csv):
        raise CSISError(f"Input CSV not found: {input_csv}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading agent table...")
    df = pd.read_csv(input_csv)

    for col in [x_field, y_field]:
        if col not in df.columns:
            raise CSISError(f"Column '{col}' not found. Available: {list(df.columns)}", "INVALID_PARAMS")

    progress_callback(40, "Creating agent point features...")
    geoms = []
    for _, row in df.iterrows():
        try:
            geoms.append(Point(float(row[x_field]), float(row[y_field])))
        except (ValueError, TypeError):
            geoms.append(None)

    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs=crs)
    gdf = gdf[gdf.geometry.notna()].copy()

    # Standardise field names to match telecoupling schema
    if name_field in gdf.columns and name_field != "Name":
        gdf = gdf.rename(columns={name_field: "Name"})
    if text_field and text_field in gdf.columns and text_field != "Text":
        gdf = gdf.rename(columns={text_field: "Text"})

    gdf["POINT_X"] = gdf.geometry.x
    gdf["POINT_Y"] = gdf.geometry.y

    workspace_dir, _ = generate_output_dir("add_agents", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(70, "Writing outputs...")

    geojson_path = os.path.join(workspace_dir, "agents.geojson")
    gdf.to_file(geojson_path, driver="GeoJSON")

    shp_path = os.path.join(workspace_dir, "agents.shp")
    gdf.to_file(shp_path)

    csv_path = os.path.join(workspace_dir, "agents_table.csv")
    gdf.drop(columns="geometry").to_csv(csv_path, index=False)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "add_agents")
    return {"files": files}
