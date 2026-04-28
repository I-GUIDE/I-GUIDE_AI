"""Shared in-memory cache for MCP geospatial tools."""

from typing import Dict

import geopandas as gpd


_dataframe_cache: Dict[str, gpd.GeoDataFrame] = {}
