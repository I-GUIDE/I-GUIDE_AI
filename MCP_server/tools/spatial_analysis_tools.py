"""Spatial analysis tools for Chicago geospatial data.

These tools perform spatial operations on cached data to avoid
passing large GeoDataFrames through the MCP protocol.
"""

from typing import Dict, Any
import geopandas as gpd
import matplotlib.pyplot as plt
import io
import base64

from ddgs import DDGS

from server import mcp_tool

# Import the cache from data_tools (module loaded without a package)
from tools.data_tools import _dataframe_cache


@mcp_tool
def count_crimes_per_community(crime_type: str = None) -> Dict[str, Any]:
    """
    Performs a spatial join to count crimes in each Chicago community area.
    
    Args:
        crime_type: Optional crime type to filter (e.g., "THEFT", "BATTERY"). 
                   If None, counts all crimes.
    
    Returns:
        dict: Summary with:
            - total_communities: Number of community areas
            - crime_counts: Dictionary mapping community name to crime count
            - top_communities: Top 10 communities by crime count
            - filtered_by: Crime type if filtered
    """
    if 'chicago_community_areas' not in _dataframe_cache:
        return {"error": "Community areas not loaded. Call load_chicago_community_areas() first."}
    
    if 'chicago_crime_data' not in _dataframe_cache:
        return {"error": "Crime data not loaded. Call load_chicago_crime_data() first."}
    
    gdf_polygons = _dataframe_cache['chicago_community_areas']
    gdf_points = _dataframe_cache['chicago_crime_data']
    
    # Filter by crime type if specified
    if crime_type and "primary_type" in gdf_points.columns:
        gdf_points = gdf_points[gdf_points["primary_type"].str.upper() == crime_type.upper()]
    
    # Perform spatial join
    gdf_points = gdf_points.to_crs(gdf_polygons.crs)
    joined_gdf = gpd.sjoin(gdf_polygons, gdf_points, how="left", predicate="contains")
    
    # Count points per community
    point_counts = joined_gdf.groupby("community").size()
    point_counts.name = "crime_count"
    
    # Merge back to polygons
    result_gdf = (
        gdf_polygons.merge(
            point_counts, left_on="community", right_index=True, how="left"
        )
        .fillna(0)
        .copy()
    )
    result_gdf["crime_count"] = result_gdf["crime_count"].astype(int)
    
    # Cache result for plotting
    _dataframe_cache['crime_counts_by_community'] = result_gdf
    
    # Create summary
    crime_dict = dict(zip(result_gdf["community"], result_gdf["crime_count"]))
    sorted_communities = sorted(crime_dict.items(), key=lambda x: x[1], reverse=True)
    
    summary = {
        "total_communities": len(result_gdf),
        "total_crimes": int(result_gdf["crime_count"].sum()),
        "crime_counts": crime_dict,
        "top_communities": [
            {"name": name, "count": int(count)} 
            for name, count in sorted_communities[:10]
        ],
        "_note": "Results cached. Use generate_crime_map() to visualize.",
        "_cache_key": "crime_counts_by_community"
    }
    
    if crime_type:
        summary["filtered_by"] = crime_type.upper()
    
    return summary


@mcp_tool
def generate_crime_map(title: str = "Crime Counts by Community Area") -> str:
    """
    Generates a choropleth map of crime counts per community area.
    Returns the map as a base64-encoded PNG image.
    
    Args:
        title: Title for the map.
    
    Returns:
        str: Base64-encoded PNG image data (can be displayed in notebooks/apps).
    """
    if 'crime_counts_by_community' not in _dataframe_cache:
        return "Error: No crime count data available. Call count_crimes_per_community() first."
    
    gdf = _dataframe_cache['crime_counts_by_community']
    
    # Create the plot
    fig, ax = plt.subplots(1, 1, figsize=(12, 12))
    gdf.plot(
        column="crime_count",
        ax=ax,
        legend=True,
        legend_kwds={"label": "Number of Crimes", "orientation": "horizontal"},
        cmap="YlOrRd"
    )
    ax.set_title(title, fontdict={"fontsize": "16", "fontweight": "3"})
    ax.set_axis_off()
    
    # Save to base64
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    
    return f"data:image/png;base64,{img_base64}"


@mcp_tool
def search_geospatial_resources(topic: str, resource_type: str) -> list:
    """
    Searches for geospatial resources like datasets, notebooks, or publications on a given topic.

    Args:
        topic: The geospatial topic to search for.
        resource_type: The type of resource to find ('datasets', 'notebooks', or 'publications').

    Returns:
        A list of search results with titles, links, and snippets.
    """
    query_map = {
        "datasets": f"geospatial open data {topic}",
        "notebooks": f"jupyter notebook {topic} github",
        "publications": f"research paper {topic} pdf",
    }
    query = query_map.get(resource_type, f"geospatial {topic}")

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))
    return results


@mcp_tool
def analyze_and_organize_results(results: list, topic: str) -> str:
    """
    Analyzes a list of search results and organizes metadata into a Markdown table.

    Args:
        results: A list of search results from the search_geospatial_resources tool.
        topic: The original geospatial topic for context.

    Returns:
        A Markdown formatted string with the organized metadata.
    """
    if not results:
        return "No results to analyze."

    lines = ["| Title | Summary | URL |", "| --- | --- | --- |"]
    for item in results:
        title = str(item.get("title", "")).replace("|", "\\|")
        summary = str(item.get("body", "")).replace("|", "\\|")
        url = str(item.get("href", "")).replace("|", "\\|")
        if topic and topic.lower() not in (title + summary).lower():
            summary = f"{summary}"
        lines.append(f"| {title} | {summary} | {url} |")

    return "\n".join(lines)
