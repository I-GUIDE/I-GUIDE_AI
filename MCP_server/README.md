# I-GUIDE MCP Server

A Model Context Protocol (MCP) server exposing geospatial analysis tools for AI assistants.

## Overview

This server implements the [Model Context Protocol](https://modelcontextprotocol.io) to make I-GUIDE's geospatial tools available to AI assistants like Claude Desktop, LangChain agents, and other MCP-compatible clients.

## Features

- ✅ **Real MCP Protocol**: Uses official Python SDK (mcp v1.26.0)
- ✅ **11 Geospatial Tools**: Data loading, spatial analysis, search, image description
- ✅ **Context Optimized**: Returns summaries instead of full datasets
- ✅ **Multiple Transports**: Supports stdio, SSE, and Streamable HTTP
- ✅ **Type-Safe**: Automatic schema generation from Python type hints
- ✅ **Easy Integration**: Works with smolagents, LangChain, Claude Desktop

## Quick Start

### 1. Install Dependencies

```bash
pip install --user "mcp[cli]" smolagents
```

### 2. Start Server

```bash
cd MCP_server
python server.py
```

### 3. Use in Python (Local Adapter)

```python
from MCP_server.smolagents_adapter import get_smolagents_tools

tools = get_smolagents_tools()
# Now use tools with smolagents, LangChain, or directly
```

## Available Tools

### Data Loading
- `load_chicago_community_areas()` - Load Chicago's 77 community area boundaries
- `load_chicago_crime_data()` - Load crime incidents from last 365 days
- `get_crime_statistics(crime_type)` - Get summary statistics for crime data

### Spatial Analysis
- `count_crimes_per_community(crime_type)` - Spatial join to count crimes per area
- `generate_crime_map(title)` - Create choropleth map visualization

### Search & Discovery
- `search_geospatial_resources(topic, resource_type)` - Search for datasets/notebooks/publications
- `analyze_and_organize_results(results, topic)` - Organize search results into markdown table
- `search_publications(query, limit)` - Search academic publications

### Image Analysis
- `describe_image(file, prompt_text)` - Describe image contents using vision model
- `describe_map(file, prompt_text)` - Describe map with focus on area and problem

### Biomass Estimation
- `estimate_biomass(region, year)` - Estimate biomass for a region and year

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ MCP Server (server.py)                                  │
│ ├─ Tool Discovery (auto-scans tools/ directory)        │
│ ├─ Schema Generation (from Python type hints)          │
│ └─ Transport Layer (stdio/SSE/HTTP)                    │
└─────────────────────────────────────────────────────────┘
                         │
                         ├─→ tools/data_tools.py
                         ├─→ tools/spatial_analysis_tools.py
                         ├─→ tools/search_tools.py
                         ├─→ tools/image_tools.py
                         └─→ tools/biomass_tools.py
```

## Usage Examples

### With smolagents

```python
from MCP_server.smolagents_adapter import get_smolagents_tools
from smolagents import OpenAIServerModel, ToolCallingAgent

tools = get_smolagents_tools()
model = OpenAIServerModel(model_id="gpt-4o-mini")
agent = ToolCallingAgent(model=model, tools=tools)

result = agent.run("Load Chicago crime data and count thefts per community")
print(result)
```

### With MCP Client

```python
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

async with sse_client("http://localhost:8000/mcp") as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        
        # List available tools
        tools = await session.list_tools()
        print(f"Found {len(tools.tools)} tools")
        
        # Call a tool
        result = await session.call_tool(
            "load_chicago_community_areas",
            arguments={}
        )
        print(result)
```

### With Claude Desktop

Add to Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "iguide-tools": {
      "command": "python",
      "args": ["/path/to/MCP_server/server.py"],
      "env": {
        "PYTHONPATH": "/path/to/i-guide-platform-flask-servers"
      }
    }
  }
}
```

### With LangChain (Future)

```python
from langchain_mcp import MultiServerMCPClient

mcp_client = MultiServerMCPClient([
    {"url": "http://localhost:8000/mcp"}
])
tools = await mcp_client.get_tools()
```

## Development

### Adding New Tools

1. Create a function in `tools/*.py`
2. Decorate with `@mcp_tool`
3. Add type hints for automatic schema generation
4. Restart server (auto-discovers new tools)

Example:

```python
# tools/my_tools.py
from server import mcp_tool

@mcp_tool
def analyze_elevation(latitude: float, longitude: float) -> dict:
    """Analyze elevation at a geographic point.
    
    Args:
        latitude: Latitude in decimal degrees
        longitude: Longitude in decimal degrees
    
    Returns:
        Dictionary with elevation data
    """
    # Your implementation
    return {"elevation_meters": 123.45, "source": "SRTM"}
```

### Testing

```bash
# Test with MCP Inspector
npx -y @modelcontextprotocol/inspector
# Connect to: http://localhost:8000/mcp

# Test locally
python -c "
from MCP_server.smolagents_adapter import get_smolagents_tools
tools = get_smolagents_tools()
print(f'Loaded {len(tools)} tools')
"
```

## Configuration

### Environment Variables

Set in `.env` file at repository root:

```bash
# Vision API (for image description tools)
VISION_API_URL=http://your-vision-api:8000/v1/chat/completions
VISION_API_KEY=your-key
VISION_API_MODEL=Qwen/Qwen2.5-VL-7B-Instruct

# Feature truncation (for context management)
MCP_MAX_FEATURES=200
```

## Migration from FastAPI Version

If you have the old FastAPI-based server:

1. **Backup**: Old server saved as `server_fastapi_old.py`
2. **Tools unchanged**: All tool functions work as-is
3. **Notebook update**: Replace Cell 2 with local adapter import
4. **Benefits**: 
   - Real MCP protocol (not custom REST API)
   - Fixed signature mismatch bugs
   - Fixed context overflow issues
   - Compatible with MCP ecosystem

See `QUICKSTART.md` for detailed migration guide.

## Troubleshooting

### ModuleNotFoundError: No module named 'mcp'

```bash
pip install --user "mcp[cli]"
```

### Context overflow / Too many tokens

The tools now return summaries automatically. If you still see large responses:
- Make sure you're using the updated `data_tools.py`
- Use the local adapter instead of HTTP discovery
- Check that summaries are being returned (should see `"_cache_key"` in responses)

### Import errors when starting server

Make sure you're in the correct directory:
```bash
cd /path/to/i-guide-platform-flask-servers/MCP_server
python server.py
```

## Documentation

- **Full Guide**: See `QUICKSTART.md`
- **MCP Protocol**: https://modelcontextprotocol.io
- **Python SDK**: https://github.com/modelcontextprotocol/python-sdk
- **Analysis Report**: See `../mcp_smolagents_notebook_review.md`

## License

[Same as parent repository]

## Contributing

To add new tools:
1. Add function to appropriate file in `tools/`
2. Decorate with `@mcp_tool`
3. Include docstring and type hints
4. Test with local adapter
5. Submit PR

## Support

For issues related to:
- **MCP Protocol**: Check https://modelcontextprotocol.io/docs
- **Tool Implementation**: Open issue in this repo
- **Integration**: See examples in `QUICKSTART.md`
