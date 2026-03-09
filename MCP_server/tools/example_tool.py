"""
Example tool demonstrating the unified server workflow.

This tool shows how a single function with @mcp_tool decorator
automatically becomes accessible via both MCP protocol and REST API.
"""

from server import mcp_tool


@mcp_tool(
    summary="Calculate area of a rectangle",
    description="Simple example tool that calculates rectangular area"
)
def calculate_rectangle_area(width: float, height: float, unit: str = "meters") -> dict:
    """Calculate the area of a rectangle.
    
    This is a simple example tool to demonstrate the unified server workflow.
    When you decorate a function with @mcp_tool, it automatically becomes
    accessible via:
    - MCP protocol at /mcp (for AI assistants)
    - REST API at /api/tool/calculate_rectangle_area (for developers)
    - Swagger UI at /api/docs (for interactive testing)
    
    Args:
        width: Width of the rectangle
        height: Height of the rectangle
        unit: Unit of measurement (default: "meters")
    
    Returns:
        Dictionary with area and perimeter calculations
    
    Example:
        # Via REST API:
        curl -X POST http://localhost:8000/api/tool/calculate_rectangle_area \
          -H "Content-Type: application/json" \
          -d '{"width": 5.0, "height": 3.0, "unit": "meters"}'
        
        # Via Swagger UI:
        Open http://localhost:8000/api/docs and try it interactively
    """
    area = width * height
    perimeter = 2 * (width + height)
    
    return {
        "area": area,
        "perimeter": perimeter,
        "unit": unit,
        "unit_squared": f"{unit}²",
        "dimensions": {
            "width": width,
            "height": height
        },
        "message": f"Rectangle of {width}×{height} {unit} has area {area} {unit}² and perimeter {perimeter} {unit}"
    }


@mcp_tool(
    summary="Convert temperature between units",
    description="Convert temperature between Celsius, Fahrenheit, and Kelvin"
)
def convert_temperature(value: float, from_unit: str, to_unit: str) -> dict:
    """Convert temperature between different units.
    
    Supports conversion between Celsius (C), Fahrenheit (F), and Kelvin (K).
    
    Args:
        value: Temperature value to convert
        from_unit: Source unit (C, F, or K)
        to_unit: Target unit (C, F, or K)
    
    Returns:
        Dictionary with original and converted values
    
    Example:
        # Convert 100°C to Fahrenheit
        curl -X POST http://localhost:8000/api/tool/convert_temperature \
          -H "Content-Type: application/json" \
          -d '{"value": 100, "from_unit": "C", "to_unit": "F"}'
    """
    # Normalize units
    from_unit = from_unit.upper()
    to_unit = to_unit.upper()
    
    # Convert to Celsius first
    if from_unit == "C":
        celsius = value
    elif from_unit == "F":
        celsius = (value - 32) * 5/9
    elif from_unit == "K":
        celsius = value - 273.15
    else:
        raise ValueError(f"Invalid from_unit: {from_unit}. Use C, F, or K")
    
    # Convert from Celsius to target
    if to_unit == "C":
        result = celsius
    elif to_unit == "F":
        result = celsius * 9/5 + 32
    elif to_unit == "K":
        result = celsius + 273.15
    else:
        raise ValueError(f"Invalid to_unit: {to_unit}. Use C, F, or K")
    
    return {
        "original": {
            "value": value,
            "unit": from_unit
        },
        "converted": {
            "value": round(result, 2),
            "unit": to_unit
        },
        "message": f"{value}°{from_unit} = {round(result, 2)}°{to_unit}"
    }
