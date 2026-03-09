from server import mcp_tool

@mcp_tool
def estimate_biomass(region: str, year: int) -> dict:
    """Estimate biomass for a region and year.
    
    Args:
        region: The geographic region name (e.g., "Iowa", "California")
        year: The year for estimation (e.g., 2023)
    
    Returns:
        Dictionary with region, year, and estimated biomass in tons
    """
    return {
        "region": region,
        "year": year,
        "biomass_tons": 12345.6
    }
