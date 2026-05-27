"""
Tool: Commodity Trade — Map bilateral trade flows from a CSV of country-pair trade data.
Outputs flow lines (GeoJSON) and a summary table.

User provides a trade CSV with from/to country codes and a country centroids CSV
(or we use a built-in world centroids lookup).
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["trade_csv", "from_country_field", "to_country_field", "value_field"]

# Approximate centroids for ISO-3 country codes (subset — common trading nations)
# Users can override by providing a centroids_csv
_FALLBACK_CENTROIDS: dict[str, tuple[float, float]] = {
    "USA": (-98.58, 39.83), "CHN": (104.19, 35.86), "DEU": (10.45, 51.17),
    "GBR": (-3.44, 55.38), "FRA": (2.21, 46.23), "JPN": (138.25, 36.20),
    "IND": (78.96, 20.59), "BRA": (-51.93, -14.24), "CAN": (-96.80, 60.10),
    "AUS": (133.78, -25.27), "RUS": (105.32, 61.52), "KOR": (127.77, 35.91),
    "MEX": (-102.55, 23.63), "IDN": (113.92, -0.79), "NLD": (5.29, 52.13),
    "SAU": (45.08, 23.89), "TUR": (35.24, 38.96), "CHE": (8.23, 46.82),
    "ARG": (-63.62, -38.42), "ZAF": (25.08, -29.00), "NGA": (8.68, 9.08),
    "EGY": (30.80, 26.82), "KEN": (37.91, -0.02), "GHA": (-1.02, 7.95),
    "ESP": (-3.75, 40.46), "ITA": (12.57, 41.87), "POL": (19.15, 51.92),
    "SWE": (18.64, 59.33), "NOR": (8.47, 60.47), "DNK": (10.00, 56.26),
    "THA": (100.99, 15.87), "VNM": (108.28, 14.06), "MYS": (109.70, 4.21),
    "PHL": (121.77, 12.88), "PAK": (69.35, 30.38), "BGD": (90.36, 23.68),
    "ETH": (40.49, 9.15), "TZA": (34.89, -6.37), "COD": (23.66, -2.88),
}


def _load_centroids(centroids_csv: str | None, from_field: str, to_field: str, df: pd.DataFrame) -> dict:
    """Returns dict of {country_code: (lon, lat)}."""
    if centroids_csv and os.path.exists(centroids_csv):
        cdf = pd.read_csv(centroids_csv)
        code_col = next((c for c in cdf.columns if c.lower() in ["iso3", "code", "country_code", "iso"]), None)
        lon_col = next((c for c in cdf.columns if c.lower() in ["lon", "longitude", "x"]), None)
        lat_col = next((c for c in cdf.columns if c.lower() in ["lat", "latitude", "y"]), None)
        if code_col and lon_col and lat_col:
            return dict(zip(cdf[code_col], zip(cdf[lon_col].astype(float), cdf[lat_col].astype(float))))
    return _FALLBACK_CENTROIDS


async def run_commodity_trade(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    trade_csv = params["trade_csv"]
    from_field = params["from_country_field"].strip()
    to_field = params["to_country_field"].strip()
    value_field = params["value_field"].strip()
    year_field = params.get("year_field", None)
    filter_year = params.get("year", None)
    top_n = int(params.get("top_n_partners", 0))
    centroids_csv = params.get("centroids_csv", None)
    crs = params.get("crs", "EPSG:4326")

    if not os.path.exists(trade_csv):
        raise CSISError(f"Trade CSV not found: {trade_csv}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading trade data...")
    df = pd.read_csv(trade_csv)

    for col in [from_field, to_field, value_field]:
        if col not in df.columns:
            raise CSISError(f"Column '{col}' not found. Available: {list(df.columns)}", "INVALID_PARAMS")

    if year_field and year_field in df.columns and filter_year:
        df = df[df[year_field].astype(str) == str(filter_year)]

    df[value_field] = pd.to_numeric(df[value_field], errors="coerce").fillna(0)

    if top_n > 0:
        df = df.nlargest(top_n, value_field)

    progress_callback(40, "Loading country centroids...")
    centroids = _load_centroids(centroids_csv, from_field, to_field, df)

    progress_callback(60, "Building trade flow lines...")
    geoms = []
    valid_rows = []
    for _, row in df.iterrows():
        fc = str(row[from_field]).upper()
        tc = str(row[to_field]).upper()
        if fc in centroids and tc in centroids:
            line = LineString([centroids[fc], centroids[tc]])
            geoms.append(line)
            valid_rows.append(row)
        else:
            logger.warning(f"Centroid missing for {fc} or {tc}, skipping flow.")

    if not valid_rows:
        raise CSISError(
            "No flows could be mapped — country codes not found in centroids. "
            "Provide a centroids_csv with columns: iso3, lon, lat.",
            "TOOL_FAILED",
        )

    gdf = gpd.GeoDataFrame(valid_rows, geometry=geoms, crs=crs)

    workspace_dir, _ = generate_output_dir("commodity_trade", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(80, "Writing outputs...")

    geojson_path = os.path.join(workspace_dir, "commodity_trade_flows.geojson")
    gdf.to_file(geojson_path, driver="GeoJSON")

    shp_path = os.path.join(workspace_dir, "commodity_trade_flows.shp")
    gdf.to_file(shp_path)

    summary = df.groupby(from_field)[value_field].sum().reset_index()
    summary.columns = ["country", "total_trade_value"]
    summary = summary.sort_values("total_trade_value", ascending=False)
    summary_csv = os.path.join(workspace_dir, "commodity_trade_summary.csv")
    summary.to_csv(summary_csv, index=False)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "commodity_trade")
    return {"files": files}
