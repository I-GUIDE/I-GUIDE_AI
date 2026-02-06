from server import mcp_tool

@mcp_tool
def estimate_biomass(region: str, year: int) -> dict:
    """
    Estimate biomass for a region and year.
    """
    return {
        "region": region,
        "year": year,
        "biomass_tons": 12345.6
    }
