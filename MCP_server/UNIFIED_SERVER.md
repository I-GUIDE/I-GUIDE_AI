# I-GUIDE Unified Server - Dual Transport Architecture

## Overview

The I-GUIDE Unified Server exposes geospatial analysis tools through **two transports**:

1. **MCP Protocol** (`/mcp`) - For AI assistants (Claude Desktop, LangChain, etc.)
2. **REST API** (`/api`) - For developers, web apps, with Swagger UI

**Key Benefit**: Write a tool function once with `@mcp_tool`, and it's automatically accessible via both transports.

## Architecture

```
┌─────────────────────────────────────────┐
│  Tool Function (Business Logic)         │
│  @mcp_tool                              │
│  def my_tool(param1, param2): ...      │
└─────────────────────────────────────────┘
                  │
                  │ Auto-registers to both transports
                  │
    ┌─────────────┴─────────────┐
    │                           │
┌───▼────────────┐    ┌────────▼──────────┐
│  MCP Protocol  │    │  REST API         │
│  /mcp          │    │  /api             │
│                │    │                   │
│  • JSON-RPC    │    │  • Swagger UI     │
│  • Base64 files│    │  • Multipart      │
│  • AI clients  │    │  • Interactive    │
└────────────────┘    └───────────────────┘
```

## Quick Start

### 1. Start the Server

```bash
cd MCP_server
python server.py
```

You'll see:

```
🚀 Starting I-GUIDE Unified Server
======================================================================

📊 Loaded 8 tools:
   • describe_image
   • describe_map
   • estimate_biomass
   • ...

🌐 Server URLs:
   Root:       http://localhost:8000
   MCP:        http://localhost:8000/mcp
   REST API:   http://localhost:8000/api
   Swagger UI: http://localhost:8000/api/docs
```

### 2. Access Swagger UI

Open in browser: **http://localhost:8000/api/docs**

You'll see interactive documentation for all tools with "Try it out" functionality.

### 3. Test a Tool via REST API

#### For tools with file uploads (e.g., `describe_image`):

```bash
curl -X POST http://localhost:8000/api/tool/describe_image \
  -F "file=@image.jpg" \
  -F "prompt_text=What is in this image?"
```

#### For JSON-based tools (e.g., `estimate_biomass`):

```bash
curl -X POST http://localhost:8000/api/tool/estimate_biomass \
  -H "Content-Type: application/json" \
  -d '{
    "region": "Iowa",
    "year": 2023
  }'
```

### 4. Test via MCP Protocol

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "describe_image",
      "arguments": {
        "file": {"content": "base64_encoded_image", "name": "img.jpg"},
        "prompt_text": "What is in this image?"
      }
    },
    "id": 1
  }'
```

## Creating New Tools

### Simple Tool (JSON-based)

```python
# In tools/my_tool.py
from server import mcp_tool

@mcp_tool
def calculate_area(width: float, height: float) -> float:
    """Calculate the area of a rectangle.
    
    Args:
        width: Width in meters
        height: Height in meters
    
    Returns:
        Area in square meters
    """
    return width * height
```

**Result**: Automatically creates:
- MCP tool: `calculate_area`
- REST endpoint: `POST /api/tool/calculate_area`
- Swagger documentation with type hints

### Tool with File Upload

```python
# In tools/my_image_tool.py
from server import mcp_tool
from fastapi import UploadFile
from typing import Union, Dict

@mcp_tool
def analyze_photo(file: Union[Dict, UploadFile], analysis_type: str = "basic") -> str:
    """Analyze a photo and return insights.
    
    Args:
        file: Image file (multipart upload or base64 dict)
        analysis_type: Type of analysis to perform
    
    Returns:
        Analysis results as text
    """
    # Handle both Dict (MCP with base64) and UploadFile (REST multipart)
    if isinstance(file, dict):
        image_bytes = base64.b64decode(file['content'])
    else:
        image_bytes = file.file.read()
    
    # Your analysis logic here
    return "Analysis results..."
```

**Result**: Automatically creates:
- MCP tool: `analyze_photo` (accepts base64 in JSON)
- REST endpoint: `POST /api/tool/analyze_photo` (accepts multipart file upload)
- Swagger UI with file upload widget

## Endpoints

### Root Endpoint

**GET /** - Server information

```json
{
  "name": "I-GUIDE Unified Server",
  "tools_count": 8,
  "endpoints": {
    "mcp_protocol": "/mcp",
    "rest_api": "/api",
    "swagger_ui": "/api/docs"
  }
}
```

### REST API Endpoints

**GET /api/** - REST API information

**GET /api/tools** - List all tools with signatures

**GET /api/health** - Health check

**GET /api/docs** - Swagger UI (interactive documentation)

**GET /api/redoc** - ReDoc (alternative documentation)

**POST /api/tool/{tool_name}** - Call a specific tool

### MCP Protocol Endpoints

**POST /mcp** - MCP JSON-RPC endpoint

Standard MCP methods:
- `initialize` - Initialize MCP session
- `tools/list` - List available tools
- `tools/call` - Call a tool

## Testing

### Run the Test Suite

```bash
# Terminal 1: Start server
python MCP_server/server.py

# Terminal 2: Run tests
python MCP_server/test_unified_server.py
```

### Manual Testing

1. **Swagger UI**: http://localhost:8000/api/docs
   - Click on any endpoint
   - Click "Try it out"
   - Fill in parameters
   - Click "Execute"

2. **curl** (REST API):
   ```bash
   # List tools
   curl http://localhost:8000/api/tools
   
   # Call a tool
   curl -X POST http://localhost:8000/api/tool/describe_image \
     -F "file=@test.jpg" \
     -F "prompt_text=Describe this"
   ```

3. **MCP Client**: Use the existing test scripts
   - `test_mcp_http_upload.py` - Test file uploads via MCP
   - `test_mcp_client.py` - Test MCP protocol

## Configuration

### Environment Variables

Set in `.env` file in project root:

```bash
# Vision API (for image tools)
VISION_API_URL=http://149.165.153.129:8000/v1/chat/completions
VISION_API_KEY=your_api_key
VISION_API_MODEL=Qwen/Qwen2.5-VL-7B-Instruct

# Other tool configurations...
```

### Server Configuration

In `server.py`:

```python
# Change host/port
uvicorn.run(parent_app, host="0.0.0.0", port=8000)

# MCP security settings
transport_security=TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["your-domain.com:*"],
    allowed_origins=["http://your-domain.com:*"],
)
```

## Deployment

### Development

```bash
python MCP_server/server.py
```

### Production

```bash
# Using uvicorn directly
uvicorn MCP_server.server:parent_app --host 0.0.0.0 --port 8000 --workers 4

# Or with gunicorn
gunicorn MCP_server.server:parent_app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "MCP_server.server:parent_app", "--host", "0.0.0.0", "--port", "8000"]
```

## Troubleshooting

### Server won't start

1. Check if port 8000 is already in use:
   ```bash
   lsof -i :8000
   ```

2. Check for Python errors:
   ```bash
   python -m py_compile MCP_server/server.py
   ```

3. Verify dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Tools not appearing

1. Check that tools are decorated with `@mcp_tool`
2. Verify tools are in `MCP_server/tools/` directory
3. Check server startup logs for import errors

### File uploads not working

1. For REST API: Use `multipart/form-data` with `file` field
2. For MCP: Use base64-encoded `{"content": "...", "name": "..."}`
3. Check tool signature accepts `Union[Dict, UploadFile]`

## Future Enhancements

### Planned Features

- [ ] Authentication/API keys for REST API
- [ ] Rate limiting per transport
- [ ] GraphQL transport option
- [ ] WebSocket support for streaming
- [ ] Tool versioning (`/api/v1/tool/...`)
- [ ] Async tool execution with job queue
- [ ] Tool usage analytics

### Extensibility

The architecture is designed to be extensible:

1. **Add new transports**: Create a new adapter in `register_tool()`
2. **Add middleware**: Use FastAPI middleware on `parent_app` or `rest_app`
3. **Custom endpoints**: Add routes to `rest_app` or `parent_app`
4. **Tool metadata**: Add custom attributes to `@mcp_tool` decorator

## Support

For issues or questions:
- Check existing test scripts in `MCP_server/`
- Review tool implementations in `MCP_server/tools/`
- See MCP protocol docs: https://modelcontextprotocol.io

## License

[Your license here]
