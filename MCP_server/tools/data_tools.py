"""Data loading tools for Chicago geospatial data.

These tools load and summarize Chicago community areas and crime data.
They return compact summaries instead of full GeoJSON to avoid context overflow.
"""

import datetime as _dt
import json
from typing import Dict, List, Any
import pandas as pd
import geopandas as gpd

from server import mcp_tool

# In-memory cache for loaded data (used when called locally via adapter)
_dataframe_cache: Dict[str, gpd.GeoDataFrame] = {}


@mcp_tool
def load_chicago_community_areas() -> Dict[str, Any]:
    """
    Loads the official boundaries for Chicago's 77 community areas.
    Returns a compact summary with metadata and sample features to avoid context overflow.
    
    Returns:
        dict: Summary containing:
            - type: "FeatureCollection"
            - feature_count: Total number of community areas (77)
            - columns: List of available data columns
            - bounds: Geographic bounding box [minx, miny, maxx, maxy]
            - crs: Coordinate reference system
            - community_names: List of all community area names
            - sample_features: First 3 community areas as examples
    """
    url = (
        "https://raw.githubusercontent.com/RandomFractals/ChicagoCrimes/refs/heads/"
        "master/data/chicago-community-areas.geojson"
    )
    gdf = gpd.read_file(url)
    
    # Cache for local use
    _dataframe_cache['chicago_community_areas'] = gdf
    
    # Return compact summary without full GeoJSON to avoid huge payloads
    sample_cols = [c for c in ["community", "area_num_1"] if c in gdf.columns]
    sample_rows = (
        gdf[sample_cols].head(3).to_dict(orient="records")
        if sample_cols
        else gdf.head(3).drop(columns="geometry", errors="ignore").to_dict(orient="records")
    )
    
    return {
        "type": "FeatureCollection",
        "feature_count": len(gdf),
        "columns": list(gdf.columns),
        "bounds": gdf.total_bounds.tolist(),
        "crs": str(gdf.crs),
        "community_names": gdf["community"].tolist() if "community" in gdf.columns else [],
        "sample_rows": sample_rows,
        "_note": "Full data cached. Use spatial analysis tools to work with complete dataset.",
        "_cache_key": "chicago_community_areas"
    }


@mcp_tool
def load_chicago_crime_data() -> Dict[str, Any]:
    """
    Loads reported crime incidents in Chicago from the last 365 days.
    Returns a compact summary with statistics and sample records to avoid context overflow.
    
    Returns:
        dict: Summary containing:
            - type: "FeatureCollection"
            - feature_count: Total number of crime records
            - columns: List of available data columns
            - bounds: Geographic bounding box [minx, miny, maxx, maxy]
            - crs: Coordinate reference system
            - crime_types: Count of records by crime type (top 10)
            - date_range: Earliest and latest crime dates
            - sample_features: First 5 crime records as examples
    """
    start_date = (_dt.datetime.utcnow() - _dt.timedelta(days=365)).strftime(
        "%Y-%m-%dT00:00:00.000"
    )
    url = (
        "https://data.cityofchicago.org/resource/ijzp-q8t2.json"
        f"?$where=date%20%3E%20'{start_date}'&$limit=50000"
    )
    df = pd.read_json(url)
    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df.longitude, df.latitude), crs="EPSG:4326"
    )
    
    # Cache for local use
    _dataframe_cache['chicago_crime_data'] = gdf
    
    # Convert datetime columns to strings
    safe_gdf = gdf.copy()
    datetime_cols = safe_gdf.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns
    for col in datetime_cols:
        safe_gdf[col] = safe_gdf[col].astype("datetime64[ns]").dt.strftime("%Y-%m-%dT%H:%M:%S")
    
    # Replace NaN/inf values with None
    safe_gdf = safe_gdf.replace([float("inf"), float("-inf")], None)
    safe_gdf = safe_gdf.where(safe_gdf.notna(), None)
    
    # Return compact sample rows without full GeoJSON to avoid huge payloads
    sample_cols = [
        c
        for c in ["id", "date", "primary_type", "description", "location_description", "arrest"]
        if c in safe_gdf.columns
    ]
    sample_rows = (
        safe_gdf[sample_cols].head(5).to_dict(orient="records")
        if sample_cols
        else safe_gdf.head(5).drop(columns="geometry", errors="ignore").to_dict(orient="records")
    )
    
    # Get crime type distribution
    crime_types = {}
    if "primary_type" in gdf.columns:
        crime_counts = gdf["primary_type"].value_counts().head(10)
        crime_types = crime_counts.to_dict()
    
    # Get date range
    date_range = {"start": None, "end": None}
    if "date" in gdf.columns:
        date_col = pd.to_datetime(gdf["date"], errors='coerce')
        date_range = {
            "start": date_col.min().isoformat() if not pd.isna(date_col.min()) else None,
            "end": date_col.max().isoformat() if not pd.isna(date_col.max()) else None
        }
    
    return {
        "type": "FeatureCollection",
        "feature_count": len(gdf),
        "columns": list(gdf.columns),
        "bounds": gdf.total_bounds.tolist(),
        "crs": str(gdf.crs),
        "crime_types": crime_types,
        "date_range": date_range,
        "sample_rows": sample_rows,
        "_note": "Full data cached. Use spatial analysis tools to work with complete dataset.",
        "_cache_key": "chicago_crime_data"
    }


@mcp_tool
def get_crime_statistics(crime_type: str = None) -> Dict[str, Any]:
    """
    Get summary statistics for Chicago crime data.
    If crime_type is specified, filters to that type only.
    
    Args:
        crime_type: Optional crime type to filter (e.g., "THEFT", "BATTERY"). Case-insensitive.
    
    Returns:
        dict: Statistics including:
            - total_count: Total number of crimes
            - crime_type_distribution: Count by crime type
            - location_distribution: Count by location description (top 10)
            - arrest_rate: Percentage of crimes resulting in arrest
    """
    if 'chicago_crime_data' not in _dataframe_cache:
        return {"error": "Crime data not loaded. Call load_chicago_crime_data() first."}
    
    gdf = _dataframe_cache['chicago_crime_data']
    
    # Filter by crime type if specified
    if crime_type:
        if "primary_type" in gdf.columns:
            gdf = gdf[gdf["primary_type"].str.upper() == crime_type.upper()]
        else:
            return {"error": "primary_type column not found in data"}
    
    # Calculate statistics
    stats = {
        "total_count": len(gdf),
        "crime_type_distribution": {},
        "location_distribution": {},
        "arrest_rate": None
    }
    
    if "primary_type" in gdf.columns:
        stats["crime_type_distribution"] = gdf["primary_type"].value_counts().head(10).to_dict()
    
    if "location_description" in gdf.columns:
        stats["location_distribution"] = gdf["location_description"].value_counts().head(10).to_dict()
    
    if "arrest" in gdf.columns:
        arrest_count = gdf["arrest"].sum()
        stats["arrest_rate"] = round((arrest_count / len(gdf)) * 100, 2) if len(gdf) > 0 else 0
    
    if crime_type:
        stats["filtered_by"] = crime_type.upper()
    
    return stats
