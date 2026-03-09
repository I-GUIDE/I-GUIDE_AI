# Quick Start - Unified Server

## 🚀 Start Server (One Command)

```bash
cd MCP_server
python server.py
```

## 🌐 Access Points

| What | URL | Use For |
|------|-----|---------|
| **Swagger UI** | http://localhost:8000/api/docs | Interactive testing |
| **REST API** | http://localhost:8000/api | Developer integration |
| **MCP Protocol** | http://localhost:8000/mcp | AI assistants |
| **Server Info** | http://localhost:8000 | Status & endpoints |

## 📝 Create a New Tool (3 Steps)

### 1. Create File
```bash
touch MCP_server/tools/my_tool.py
```

### 2. Write Function
```python
from server import mcp_tool

@mcp_tool
def my_tool(param1: str, param2: int = 10) -> dict:
    """Does something cool.
    
    Args:
        param1: Description of param1
        param2: Description of param2 (default: 10)
    
    Returns:
        Result dictionary
    """
    return {"result": f"{param1} processed {param2} times"}
```

### 3. Restart Server
```bash
# Ctrl+C to stop, then:
python server.py
```

**Done!** Your tool is now accessible via:
- MCP: `/mcp` endpoint
- REST: `POST /api/tool/my_tool`
- Swagger: http://localhost:8000/api/docs

## 🧪 Test Your Tool

### Option 1: Swagger UI (Easiest)
1. Open http://localhost:8000/api/docs
2. Find your tool
3. Click "Try it out"
4. Fill in parameters
5. Click "Execute"

### Option 2: curl
```bash
curl -X POST http://localhost:8000/api/tool/my_tool \
  -H "Content-Type: application/json" \
  -d '{"param1": "test", "param2": 5}'
```

### Option 3: Python
```python
import requests

response = requests.post(
    "http://localhost:8000/api/tool/my_tool",
    json={"param1": "test", "param2": 5}
)
print(response.json())
```

## 📤 Tool with File Upload

```python
from server import mcp_tool
from fastapi import UploadFile
from typing import Union, Dict
import base64

@mcp_tool
def process_image(file: Union[Dict, UploadFile], prompt: str = "") -> str:
    """Process an image file.
    
    Args:
        file: Image file (multipart or base64)
        prompt: Optional processing prompt
    
    Returns:
        Processing result
    """
    # Handle both formats
    if isinstance(file, dict):
        # MCP format (base64)
        image_bytes = base64.b64decode(file['content'])
    else:
        # REST format (multipart)
        image_bytes = file.file.read()
    
    # Process image_bytes...
    return "Processed!"
```

**Test with curl:**
```bash
curl -X POST http://localhost:8000/api/tool/process_image \
  -F "file=@image.jpg" \
  -F "prompt=Analyze this"
```

## 🔍 List All Tools

```bash
curl http://localhost:8000/api/tools
```

## ✅ Health Check

```bash
curl http://localhost:8000/api/health
```

## 📊 Example Tools Included

Try these built-in examples:

### 1. Calculate Rectangle Area
```bash
curl -X POST http://localhost:8000/api/tool/calculate_rectangle_area \
  -H "Content-Type: application/json" \
  -d '{"width": 5.0, "height": 3.0, "unit": "meters"}'
```

### 2. Convert Temperature
```bash
curl -X POST http://localhost:8000/api/tool/convert_temperature \
  -H "Content-Type: application/json" \
  -d '{"value": 100, "from_unit": "C", "to_unit": "F"}'
```

### 3. Describe Image (File Upload)
```bash
curl -X POST http://localhost:8000/api/tool/describe_image \
  -F "file=@your_image.jpg" \
  -F "prompt_text=What is in this image?"
```

## 🐛 Troubleshooting

### Port Already in Use
```bash
# Find process using port 8000
lsof -i :8000

# Kill it
kill -9 <PID>
```

### Tool Not Appearing
1. Check file is in `MCP_server/tools/`
2. Check function has `@mcp_tool` decorator
3. Restart server
4. Check server logs for import errors

### Import Errors
```bash
# Check syntax
python -m py_compile MCP_server/tools/my_tool.py

# Test import
python -c "from MCP_server.tools import my_tool"
```

## 📚 More Info

- **Full Documentation**: `UNIFIED_SERVER.md`
- **Changes Summary**: `CHANGES.md`
- **Test Suite**: `python test_unified_server.py`
- **Example Tools**: `tools/example_tool.py`

## 🎯 Common Patterns

### JSON Tool (No Files)
```python
@mcp_tool
def json_tool(param: str) -> dict:
    return {"result": param}
```
**Endpoint**: JSON body

### File Upload Tool
```python
@mcp_tool
def file_tool(file: Union[Dict, UploadFile]) -> str:
    # Handle both formats
    return "processed"
```
**Endpoint**: Multipart form-data

### Mixed Parameters
```python
@mcp_tool
def mixed_tool(file: Union[Dict, UploadFile], param: str) -> dict:
    return {"file": "processed", "param": param}
```
**Endpoint**: Multipart with file + form fields

## 💡 Pro Tips

1. **Type Hints**: Always add them for better Swagger docs
2. **Docstrings**: First line shows in tool list
3. **Defaults**: Optional params need defaults
4. **Return Types**: Use `dict` or `str` for JSON serialization
5. **File Handling**: Use `Union[Dict, UploadFile]` for dual transport

## 🚢 Deploy to Production

```bash
# Install gunicorn
pip install gunicorn

# Run with workers
gunicorn MCP_server.server:parent_app \
  -w 4 \
  -k uvicorn.workers.UvicornWorker \
  -b 0.0.0.0:8000
```

## 🎉 You're Ready!

1. Create tools in `tools/`
2. Add `@mcp_tool` decorator
3. Restart server
4. Test in Swagger UI

That's it! Your tools are now accessible via both MCP and REST.
