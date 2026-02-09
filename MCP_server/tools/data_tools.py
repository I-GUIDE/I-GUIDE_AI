import datetime as _dt
import json
import pandas as pd
import geopandas as gpd

from server import mcp_tool


@mcp_tool
def load_chicago_community_areas() -> gpd.GeoDataFrame:
    """
    Loads the official boundaries for Chicago's 77 community areas.
    Returns a GeoDataFrame with geometry and community area information.
    """
    url = (
        "https://raw.githubusercontent.com/RandomFractals/ChicagoCrimes/refs/heads/"
        "master/data/chicago-community-areas.geojson"
    )
    gdf = gpd.read_file(url)
    # Return GeoJSON as dict to keep FastAPI JSON serialization happy.
    return json.loads(gdf.to_json())


@mcp_tool
def load_chicago_crime_data() -> gpd.GeoDataFrame:
    """
    Loads reported crime incidents in Chicago from the last 365 days.
    The data includes coordinates for each crime, which can be used for mapping.
    Returns a GeoDataFrame with crime details and point geometries.
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
    # Return GeoJSON as dict to keep FastAPI JSON serialization happy.
    # Convert datetime columns to ISO strings to avoid Timestamp JSON issues.
    safe_gdf = gdf.copy()
    datetime_cols = safe_gdf.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns
    for col in datetime_cols:
        safe_gdf[col] = safe_gdf[col].astype("datetime64[ns]").dt.strftime("%Y-%m-%dT%H:%M:%S")
    # Replace NaN/inf values with None for JSON compliance.
    safe_gdf = safe_gdf.replace([float("inf"), float("-inf")], None)
    safe_gdf = safe_gdf.where(safe_gdf.notna(), None)
    return json.loads(safe_gdf.to_json())


@mcp_tool
def filter_dataframe_by_value(df: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
    """
    Filters a DataFrame to only include rows where the specified column matches a value.
    This is useful for isolating specific types of crime, for example.
    Args:
        df: The DataFrame to filter.
        column: The name of the column to check.
        value: The value to match in the column.
    Returns:
        A filtered DataFrame.
    """
    series = df[column].astype(str)
    return df[series.str.upper() == value.upper()]
