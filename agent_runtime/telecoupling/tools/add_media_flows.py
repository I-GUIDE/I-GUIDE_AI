"""
Tool: Add Media Information Flows — Parse an HTML file for country/place name mentions,
compute mention frequencies, and generate flow lines from a source point to each mentioned location.

Requires: a country reference CSV (with iso3, country_name, lon, lat) and an HTML file.
"""
from __future__ import annotations
import logging
import os
import re
from typing import Callable

import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["html_file", "source_lon", "source_lat", "country_reference_csv"]


async def run_add_media_flows(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    html_file = params["html_file"]
    source_lon = float(params["source_lon"])
    source_lat = float(params["source_lat"])
    ref_csv = params["country_reference_csv"]
    source_name = params.get("source_name", "Source")
    crs = params.get("crs", "EPSG:4326")
    min_mentions = int(params.get("min_mentions", 1))

    for path in [html_file, ref_csv]:
        if not os.path.exists(path):
            raise CSISError(f"File not found: {path}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading HTML content...")
    try:
        from bs4 import BeautifulSoup
        with open(html_file, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        text = soup.get_text(separator=" ")
    except ImportError:
        # Fallback: plain text extraction without BeautifulSoup
        with open(html_file, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        text = re.sub(r"<[^>]+>", " ", raw)

    progress_callback(25, "Loading country reference data...")
    ref = pd.read_csv(ref_csv)
    name_col = next((c for c in ref.columns if c.lower() in ["country", "country_name", "name"]), None)
    lon_col = next((c for c in ref.columns if c.lower() in ["lon", "longitude", "x"]), None)
    lat_col = next((c for c in ref.columns if c.lower() in ["lat", "latitude", "y"]), None)

    if not all([name_col, lon_col, lat_col]):
        raise CSISError(
            "country_reference_csv must have columns for: country name, longitude, latitude. "
            f"Found: {list(ref.columns)}",
            "INVALID_PARAMS",
        )

    progress_callback(45, "Counting country mentions in HTML...")
    text_lower = text.lower()
    freq: dict[str, int] = {}
    for _, row in ref.iterrows():
        cname = str(row[name_col])
        count = len(re.findall(r'\b' + re.escape(cname.lower()) + r'\b', text_lower))
        if count >= min_mentions:
            freq[cname] = count

    if not freq:
        raise CSISError(
            "No country names found in the HTML with the minimum mention threshold. "
            "Try lowering min_mentions or checking the HTML content.",
            "TOOL_FAILED",
        )

    progress_callback(65, "Building media flow lines...")
    rows = []
    geoms = []
    source_pt = (source_lon, source_lat)

    ref_idx = {str(row[name_col]): row for _, row in ref.iterrows()}
    for cname, count in freq.items():
        row = ref_idx.get(cname)
        if row is None:
            continue
        try:
            tlon, tlat = float(row[lon_col]), float(row[lat_col])
        except (ValueError, TypeError):
            continue
        geoms.append(LineString([source_pt, (tlon, tlat)]))
        rows.append({
            "source": source_name,
            "target_country": cname,
            "mentions": count,
            "from_lon": source_lon,
            "from_lat": source_lat,
            "to_lon": tlon,
            "to_lat": tlat,
        })

    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=crs)

    workspace_dir, _ = generate_output_dir("add_media_flows", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(80, "Writing outputs...")

    gdf.to_file(os.path.join(workspace_dir, "media_flows.geojson"), driver="GeoJSON")
    gdf.to_file(os.path.join(workspace_dir, "media_flows.shp"))

    freq_df = pd.DataFrame(rows)[["target_country", "mentions"]].sort_values("mentions", ascending=False)
    freq_df.to_csv(os.path.join(workspace_dir, "media_mention_frequency.csv"), index=False)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "add_media_flows")
    return {"files": files}
