# Unified Server Architecture

## High-Level Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    I-GUIDE Unified Server                       │
│                     (FastAPI + FastMCP)                         │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │              Tool Registry (_tool_registry)               │ │
│  │  { "describe_image": func, "estimate_biomass": func, ...}│ │
│  └───────────────────────────────────────────────────────────┘ │
│                              │                                  │
│                              │ @mcp_tool decorator              │
│                              │ auto-registers to both           │
│                              │                                  │
│              ┌───────────────┴───────────────┐                 │
│              │                               │                 │
│    ┌─────────▼─────────┐         ┌─────────▼──────────┐       │
│    │   MCP Protocol    │         │    REST API        │       │
│    │   (FastMCP)       │         │    (FastAPI)       │       │
│    │   /mcp            │         │    /api            │       │
│    │                   │         │                    │       │
│    │ • JSON-RPC        │         │ • Swagger UI       │       │
│    │ • Base64 files    │         │ • Multipart upload │       │
│    │ • tools/list      │         │ • OpenAPI schema   │       │
│    │ • tools/call      │         │ • /tool/{name}     │       │
│    └───────────────────┘         └────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
         │                                    │
         │                                    │
    ┌────▼────────┐                    ┌─────▼──────────┐
    │ AI Clients  │                    │   Developers   │
    │             │                    │                │
    │ • Claude    │                    │ • curl         │
    │ • LangChain │                    │ • Postman      │
    │ • Custom    │                    │ • Web apps     │
    └─────────────┘                    │ • Swagger UI   │
                                       └────────────────┘
```

## Component Breakdown

### 1. Tool Definition Layer

```python
# tools/my_tool.py
from server import mcp_tool

@mcp_tool  # ← Marks function for dual transport
def my_tool(param: str) -> dict:
    """Business logic here"""
    return {"result": param}
```

**Responsibilities:**
- Pure business logic
- No transport-specific code
- Type hints for validation
- Documentation via docstrings

### 2. Registration Layer

```python
# server.py
def register_tool(func):
    # 1. Store in registry
    _tool_registry[func.__name__] = func
    
    # 2. Register with MCP
    mcp.tool()(mcp_wrapper)
    
    # 3. Register with REST
    _register_rest_endpoint(func, ...)
```

**Responsibilities:**
- Scan tools/ directory
- Extract metadata (name, signature, docs)
- Create MCP tool wrapper
- Create REST endpoint wrapper
- Handle file upload detection

### 3. MCP Transport Layer

```python
mcp = FastMCP(name="I-GUIDE Tools")
mcp_app = mcp.streamable_http_app()
```

**Responsibilities:**
- JSON-RPC protocol handling
- Base64 file encoding/decoding
- MCP session management
- Tool schema generation

**Endpoints:**
- `POST /mcp` - JSON-RPC endpoint
  - `initialize` - Start session
  - `tools/list` - List available tools
  - `tools/call` - Execute tool

### 4. REST Transport Layer

```python
rest_app = FastAPI(title="I-GUIDE Tools REST API")

@rest_app.post("/tool/{tool_name}")
async def tool_endpoint(...):
    # Call registered function
    result = func(**kwargs)
    return {"success": True, "result": result}
```

**Responsibilities:**
- HTTP request/response handling
- Multipart file upload support
- OpenAPI schema generation
- Swagger UI hosting
- CORS configuration

**Endpoints:**
- `GET /api/` - API info
- `GET /api/tools` - List tools
- `GET /api/docs` - Swagger UI
- `POST /api/tool/{name}` - Execute tool
- `GET /api/health` - Health check

### 5. Parent App (Unified Entry Point)

```python
parent_app = FastAPI(title="I-GUIDE Unified Server")
parent_app.mount("/mcp", mcp_app)
parent_app.mount("/api", rest_app)
```

**Responsibilities:**
- Mount both transports
- Root endpoint with info
- Global middleware (future)
- Unified server lifecycle

## Request Flow

### MCP Protocol Request

```
1. Client sends JSON-RPC request to /mcp
   POST /mcp
   {
     "jsonrpc": "2.0",
     "method": "tools/call",
     "params": {
       "name": "describe_image",
       "arguments": {
         "file": {"content": "base64...", "name": "img.jpg"},
         "prompt_text": "What is this?"
       }
     }
   }

2. FastMCP parses JSON-RPC
   ↓
3. Looks up tool in registry
   ↓
4. Calls mcp_wrapper(**arguments)
   ↓
5. mcp_wrapper calls original function
   ↓
6. Returns result in JSON-RPC format
   {
     "jsonrpc": "2.0",
     "result": "This is an image of...",
     "id": 1
   }
```

### REST API Request

```
1. Client sends HTTP request to /api/tool/{name}
   POST /api/tool/describe_image
   Content-Type: multipart/form-data
   
   file: <binary data>
   prompt_text: "What is this?"

2. FastAPI parses multipart data
   ↓
3. Validates against function signature
   ↓
4. Calls rest_endpoint(file=UploadFile, prompt_text=str)
   ↓
5. rest_endpoint calls original function
   ↓
6. Returns JSON response
   {
     "success": true,
     "tool": "describe_image",
     "result": "This is an image of..."
   }
```

## File Upload Handling

### Dual Format Support

Tools accept `Union[Dict, UploadFile]` to handle both transports:

```python
@mcp_tool
def process_file(file: Union[Dict, UploadFile]) -> str:
    if isinstance(file, dict):
        # MCP format (base64)
        image_bytes = base64.b64decode(file['content'])
    else:
        # REST format (multipart)
        image_bytes = file.file.read()
    
    # Process image_bytes...
```

### Automatic Endpoint Detection

```python
def _register_rest_endpoint(func, ...):
    # Inspect function signature
    sig = inspect.signature(func)
    
    # Check for file parameters
    has_file_param = any(
        UploadFile in param.annotation
        for param in sig.parameters.values()
    )
    
    if has_file_param:
        # Create multipart endpoint
        @rest_app.post(...)
        async def endpoint(file: UploadFile, ...):
            ...
    else:
        # Create JSON endpoint
        @rest_app.post(...)
        async def endpoint(arguments: Dict):
            ...
```

## Tool Discovery

### MCP Protocol

```json
// Request
{"jsonrpc": "2.0", "method": "tools/list"}

// Response
{
  "tools": [
    {
      "name": "describe_image",
      "description": "Describe image contents",
      "inputSchema": {
        "type": "object",
        "properties": {
          "file": {"type": "object"},
          "prompt_text": {"type": "string"}
        }
      }
    }
  ]
}
```

### REST API

```bash
GET /api/tools

{
  "tools_count": 8,
  "tools": [
    {
      "name": "describe_image",
      "description": "Describe image contents",
      "endpoint": "/tool/describe_image",
      "parameters": {
        "file": {"type": "UploadFile", "required": true},
        "prompt_text": {"type": "str", "default": "..."}
      }
    }
  ]
}
```

## Error Handling

### MCP Protocol

```python
def mcp_wrapper(**kwargs):
    try:
        return func(**kwargs)
    except Exception as e:
        # MCP client sees the error
        raise
```

### REST API

```python
async def rest_endpoint(...):
    try:
        result = func(**kwargs)
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Tool execution failed: {str(e)}"
        )
```

## Extensibility Points

### 1. Add New Transport

```python
# In register_tool()
def register_tool(func):
    # Existing transports
    register_mcp_transport(func)
    register_rest_transport(func)
    
    # New transport
    register_graphql_transport(func)  # Future
    register_grpc_transport(func)     # Future
```

### 2. Add Middleware

```python
# Authentication
rest_app.add_middleware(AuthMiddleware)

# Rate limiting
rest_app.add_middleware(RateLimitMiddleware)

# Logging
parent_app.add_middleware(LoggingMiddleware)
```

### 3. Custom Endpoints

```python
# Add to rest_app
@rest_app.get("/custom/endpoint")
def custom_endpoint():
    return {"custom": "data"}
```

### 4. Tool Metadata

```python
@mcp_tool(
    summary="Short description",
    description="Long description",
    tags=["category1", "category2"],  # Future
    version="1.0.0",                  # Future
    deprecated=False                  # Future
)
def my_tool(...):
    ...
```

## Performance Considerations

### Concurrent Requests

- FastAPI handles requests asynchronously
- Each transport can serve multiple clients
- Tools run synchronously (can be made async)

### Caching

```python
# Future: Add caching layer
from functools import lru_cache

@mcp_tool
@lru_cache(maxsize=100)
def expensive_tool(param: str) -> str:
    # Cached results
    ...
```

### Load Balancing

```
┌─────────┐
│ Nginx   │
│ Load    │
│ Balancer│
└────┬────┘
     │
     ├─→ Server Instance 1 (port 8000)
     ├─→ Server Instance 2 (port 8001)
     └─→ Server Instance 3 (port 8002)
```

## Security Considerations

### Current

- CORS enabled for REST API
- DNS rebinding protection for MCP
- Input validation via Pydantic

### Future

- API key authentication
- Rate limiting per client
- Request signing
- Input sanitization
- File upload size limits
- Virus scanning for uploads

## Monitoring

### Health Check

```bash
GET /api/health

{
  "status": "healthy",
  "tools_loaded": 8,
  "transports": ["mcp", "rest"]
}
```

### Future Metrics

- Request count per tool
- Average response time
- Error rates
- Active connections
- Resource usage

## Summary

The unified server architecture provides:

✅ **Single Source of Truth**: One function, multiple transports
✅ **Automatic Registration**: Decorator handles everything
✅ **Type Safety**: Pydantic validation from type hints
✅ **Developer Experience**: Swagger UI for testing
✅ **AI Integration**: MCP protocol for assistants
✅ **Extensibility**: Easy to add new transports
✅ **Maintainability**: Clear separation of concerns
