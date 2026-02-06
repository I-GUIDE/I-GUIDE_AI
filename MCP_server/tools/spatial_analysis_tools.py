import geopandas as gpd
import matplotlib.pyplot as plt

from ddgs import DDGS

from server import mcp_tool


@mcp_tool
def spatial_join_and_count(
    gdf_polygons: gpd.GeoDataFrame, gdf_points: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """
    Performs a spatial join, counting how many points from one GeoDataFrame fall within the polygons of another.
    For example, it can count the number of thefts (points) in each community area (polygons).
    Args:
        gdf_polygons: The GeoDataFrame with polygons (e.g., community areas).
        gdf_points: The GeoDataFrame with points to be counted (e.g., crimes).
    Returns:
        The polygon GeoDataFrame with a new 'crime_count' column.
    """
    gdf_points = gdf_points.to_crs(gdf_polygons.crs)
    joined_gdf = gpd.sjoin(gdf_polygons, gdf_points, how="left", predicate="contains")
    point_counts = joined_gdf.groupby("community").size()
    point_counts.name = "crime_count"
    result_gdf = (
        gdf_polygons.merge(
            point_counts, left_on="community", right_index=True, how="left"
        )
        .fillna(0)
        .copy()
    )
    result_gdf["crime_count"] = result_gdf["crime_count"].astype(int)
    return result_gdf


@mcp_tool
def plot_choropleth_map(gdf: gpd.GeoDataFrame, column: str, title: str) -> None:
    """
    Generates and displays a choropleth map, where polygons are colored based on a numeric value.
    This is excellent for visualizing data like crime rates across different areas.
    Args:
        gdf: The GeoDataFrame containing the geographic data and the values to plot.
        column: The name of the column to use for coloring the polygons.
        title: The title to display above the map.
    """
    fig, ax = plt.subplots(1, 1, figsize=(12, 12))
    gdf.plot(
        column=column,
        ax=ax,
        legend=True,
        legend_kwds={"label": "Number of Crimes", "orientation": "horizontal"},
    )
    ax.set_title(title, fontdict={"fontsize": "16", "fontweight": "3"})
    ax.set_axis_off()
    plt.show()


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
