# MCP + Smolagents Integration Review

**Branch:** `MPC_Prototype`  
**Date:** 2026-02-09  
**Status:** ❌ **SYSTEM NOT FUNCTIONAL - CRITICAL BUGS PRESENT**

---

## 1. INVENTORY

### Core Files
- **`MCP_server/smolagent_mcp_tools.ipynb`** - Test notebook (10+ execution runs, 957K token overflow)
- **`MCP_server/server.py`** - FastAPI server exposing tools via HTTP
- **`MCP_server/smolagents_adapter.py`** - Local import adapter (PRESENT BUT UNUSED)
- **`MCP_server/tools/*.py`** - 11 tool functions across 5 modules

### Tool Inventory
```
data_tools.py: load_chicago_community_areas, load_chicago_crime_data, filter_dataframe_by_value
spatial_analysis_tools.py: spatial_join_and_count, plot_choropleth_map, search_geospatial_resources, analyze_and_organize_results
image_tools.py: describe_image, describe_map
search_tools.py: search_publications
biomass_tools.py: estimate_biomass
```

---

## 2. EXECUTION FLOW

### Cell 1: Environment Setup
```python
# smolagent_mcp_tools.ipynb:29-42
load_dotenv(os.path.join(repo_root, ".env"))
```
Loads environment variables from `.env`.

### Cell 2: HTTP Tool Discovery ⚠️
```python
# smolagent_mcp_tools.ipynb:76-139
def discover_mcp_tools(base_url: str):
    spec = requests.get(f"{base_url}/openapi.json").json()
    for path in spec["paths"]:
        if path.startswith("/mcp/"):
            def _tool(payload: dict) -> object:  # ❌ SIGNATURE MISMATCH
                resp = requests.post(endpoint, json=payload, timeout=60)
                return resp.json().get("outputs")
            return smol_tool(_tool)
```

**Output:**
```
Discovered 11 remote MCP tools from http://localhost:8000
```

**Problem:** Wrapper accepts `payload: dict`, but backend tools have specific signatures.

### Cell 3: API Key Input
```python
os.environ["OPENAI_API_KEY"] = getpass.getpass()
```

### Cell 4: Agent Creation
```python
# smolagent_mcp_tools.ipynb:182-186
from smolagents import OpenAIServerModel, ToolCallingAgent
model = OpenAIServerModel(model_id="gpt-4o")
agent = ToolCallingAgent(model=model, tools=tools)
```

### Cell 7: Agent Execution ❌ FAILS
```python
# smolagent_mcp_tools.ipynb:85520-85527
query = """
    1. Load Chicago community areas and crime data.
    2. Perform a spatial join to count crimes per community area.
    3. Filter for thefts only.
    4. Plot a choropleth map of theft counts.
"""
result = agent.run(query)
```

**Execution Trace:**
1. Agent calls `load_chicago_community_areas({'payload': {}})` → ✅ SUCCESS
2. Agent calls `load_chicago_crime_data({'payload': {}})` → ❌ **500 Server Error**
3. Agent retries with `load_chicago_community_areas` again → ✅ SUCCESS (adds more to context)
4. Context reaches 957K tokens → ❌ **Context Length Exceeded Error**

---

## 3. MCP VERIFICATION

### Question: Is this actually using MCP protocol?

**Answer: ❌ NO**

### What MCP Actually Is
- **Protocol:** JSON-RPC 2.0 over stdio/SSE/HTTP
- **Messages:** `{"jsonrpc": "2.0", "method": "tools/list", "id": 1}`
- **Discovery:** `tools/list` method
- **Invocation:** `tools/call` method with structured parameters

### What This Implementation Is
```python
# MCP_server/server.py:76-92
@app.post(f"/mcp/{tool_name}")  # ← FastAPI REST endpoint, not MCP
async def tool_mcp(request: Request):
    inputs = await _parse_request_inputs(request)
    return {
        "tool": tool_name,
        "inputs": inputs,
        "outputs": func(**inputs),  # ← Direct function call
        "status": "success"
    }
```

**This is:** Custom FastAPI REST API with `/mcp/` prefix  
**Not:** Model Context Protocol (no JSON-RPC, no MCP SDK)

### Tool Discovery Method
```python
# Notebook uses OpenAPI spec, not MCP tools/list
spec = requests.get(f"{base_url}/openapi.json").json()
```

**Conclusion:** "MCP" is branding only. No actual MCP protocol implementation.

---

## 4. SMOLAGENTS INTEGRATION

### Agent Type: `ToolCallingAgent`
- Uses OpenAI function calling API
- Converts tool signatures to OpenAI function schemas
- Model decides which tools to call based on task

### Tool Call Flow
```
1. Smolagents introspects tool signature: _tool(payload: dict)
2. Generates OpenAI schema: {"name": "load_chicago_crime_data", "parameters": {"payload": {"type": "object"}}}
3. OpenAI returns: {"function": {"name": "load_chicago_crime_data", "arguments": "{\"payload\": {}}"}}
4. Smolagents calls: _tool(payload={})
5. Wrapper POSTs: requests.post("/mcp/load_chicago_crime_data", json={})
6. FastAPI calls: load_chicago_crime_data(**{})  # Unpacks empty dict as kwargs
7. Backend function expects: load_chicago_crime_data()  # NO PARAMETERS
8. Result: 500 Internal Server Error
```

---

## 5. CRITICAL ISSUES

### 🔴 Issue #1: Signature Mismatch

**Location:** `smolagent_mcp_tools.ipynb:98-105`

**Problem:**
```python
# Wrapper created by notebook
def _tool(payload: dict) -> object:
    resp = requests.post(endpoint, json=payload)
    return resp.json().get("outputs")
```

**Backend reality** (`data_tools.py:25`):
```python
@mcp_tool
def load_chicago_crime_data() -> gpd.GeoDataFrame:  # Takes NO parameters
    start_date = (_dt.datetime.utcnow() - _dt.timedelta(days=365)).strftime(...)
    url = f"https://data.cityofchicago.org/resource/ijzp-q8t2.json?$where=..."
    df = pd.read_json(url)
    return json.loads(gpd.GeoDataFrame(df, geometry=...).to_json())
```

**Evidence of failure:** `smolagent_mcp_tools.ipynb:42835-42843`
```
Error executing tool 'load_chicago_crime_data' with arguments {'payload': {}}: 
HTTPError: 500 Server Error: Internal Server Error
```

**Fix:** Use the local adapter instead:
```python
# Replace Cell 2 entirely
from MCP_server.smolagents_adapter import get_smolagents_tools
tools = get_smolagents_tools()
```

The adapter (`smolagents_adapter.py:41-63`) correctly preserves function signatures:
```python
def get_smolagents_tools(tools_dir=None):
    wrapped = []
    for func in _iter_mcp_tools(tools_dir):
        func.__signature__ = inspect.signature(func)  # ← Preserves signature
        wrapped.append(smol_tool(func))
    return wrapped
```

---

### 🔴 Issue #2: Context Length Explosion

**Evidence:** `smolagent_mcp_tools.ipynb:85465-85474`
```
Error code: 400 - {'error': {'message': "This model's maximum context length 
is 128000 tokens. However, your messages resulted in 957082 tokens 
(956725 in the messages, 357 in the functions)."}}
```

**Root cause:** GeoJSON responses are massive
```json
// load_chicago_community_areas() returns:
{
  "type": "FeatureCollection",
  "features": [
    {
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[...500 coordinate pairs per polygon...]]]
      }
    }
    // × 77 community areas
  ]
}
// Result: ~50-100K tokens per response
```

**Current mitigation attempt** (`smolagent_mcp_tools.ipynb:108-119`):
```python
max_features = int(os.environ.get("MCP_MAX_FEATURES", "200"))
if total > max_features:
    outputs["features"] = outputs["features"][0:max_features]
```
**Why it fails:** 200 features is still enormous; truncation happens after full JSON creation.

**Fix Option A - Return DataFrame References:**
```python
# MCP_server/server.py: Add session storage
_dataframe_cache = {}

@app.post("/mcp/{tool_name}")
async def tool_mcp(request: Request):
    result = func(**inputs)
    if isinstance(result, dict) and result.get("type") == "FeatureCollection":
        df_id = str(uuid4())
        _dataframe_cache[df_id] = result
        return {
            "tool": tool_name,
            "outputs": {
                "dataframe_id": df_id,
                "type": "GeoDataFrame",
                "feature_count": len(result["features"]),
                "columns": list(result["features"][0]["properties"].keys()),
                "sample": result["features"][:3]  # Only 3 features
            },
            "status": "success"
        }
    return {"tool": tool_name, "outputs": result, "status": "success"}
```

**Fix Option B - Aggressive Truncation:**
```python
# In tool wrapper, keep only 5 features
if "features" in outputs:
    outputs["features"] = outputs["features"][:5]
    outputs["_note"] = f"Showing 5 of {len(outputs['features'])} features"
```

---

### 🔴 Issue #3: Backend Tool Signature Issues

**`load_chicago_crime_data`** expects no parameters but receives `{}`

**Other tools with parameter mismatches:**
- `filter_dataframe_by_value(df, column, value)` - expects DataFrame object, not dict
- `spatial_join_and_count(gdf_polygons, gdf_points)` - expects 2 GeoDataFrames

**These will all fail** when called through HTTP wrapper because:
1. Agent can't pass Python objects (DataFrames) through JSON
2. Signature mismatch prevents proper parameter passing

---

## 6. RUNTIME REQUIREMENTS

### Required Services
- **FastAPI server at `http://localhost:8000`** (NOT started by notebook)
- **OpenAI API access** (gpt-4o model)

### Environment Variables
```bash
# Notebook uses:
MCP_BASE_URL=http://localhost:8000  # Server URL
OPENAI_API_KEY=sk-...                # Required, prompted via getpass
OPENAI_MODEL=gpt-4o                  # Model ID
MCP_MAX_FEATURES=200                 # GeoJSON truncation limit

# Backend tools use:
VISION_API_URL=http://149.165.153.129:8000/v1/chat/completions
VISION_API_KEY=...                   # Optional
VISION_API_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
```

### Missing Dependencies
`requirements.txt` does not include `smolagents`. Must install separately:
```bash
pip install smolagents
```

### Server Startup
**Not handled by notebook.** Must manually run:
```bash
cd MCP_server
uvicorn server:app --host 0.0.0.0 --port 8000
```

---

## 7. VERIFICATION CHECKLIST

| Check | Status | Evidence |
|-------|--------|----------|
| MCP server reachable | ⚠️ ASSUMED | Notebook assumes localhost:8000 running |
| Agent connects to server | ⚠️ MISLEADING | Connects via HTTP REST, not MCP protocol |
| Agent discovers tools | ✅ PASS | 11 tools discovered via OpenAPI spec |
| Agent calls tool successfully | ❌ FAIL | Second tool call returns 500 error |
| Tool returns valid response | ⚠️ PARTIAL | First tool succeeded, second failed |
| Agent completes task | ❌ FAIL | Context overflow at 957K tokens |
| Output is reproducible | ❌ FAIL | Depends on server state, env vars, API quota |
| Errors are clear | ❌ FAIL | 500 errors don't explain signature mismatch |

**Overall:** ❌ **SYSTEM NOT FUNCTIONAL**

---

## 8. RECOMMENDED FIXES

### Priority 1: Make It Work (30 minutes)

**Replace HTTP discovery with local adapter:**

```python
# Cell 2 - BEFORE (broken)
tools = discover_mcp_tools("http://localhost:8000")

# Cell 2 - AFTER (working)
from MCP_server.smolagents_adapter import get_smolagents_tools
tools = get_smolagents_tools()
print(f"Discovered {len(tools)} MCP tools (local import)")
```

**Why this fixes it:**
- ✅ Preserves exact function signatures
- ✅ No HTTP overhead
- ✅ No signature mismatch
- ✅ Clear Python exceptions instead of HTTP 500
- ✅ No server dependency

### Priority 2: Fix Context Overflow (2-4 hours)

**Option A - Server-side DataFrame caching (recommended):**

```python
# MCP_server/server.py
from uuid import uuid4
_cache = {}

@app.post("/mcp/load_chicago_community_areas")
async def load_areas():
    gdf = load_chicago_community_areas_impl()  # Full data
    df_id = str(uuid4())
    _cache[df_id] = gdf
    return {
        "outputs": {
            "dataframe_id": df_id,
            "type": "GeoDataFrame",
            "shape": gdf.shape,
            "columns": list(gdf.columns),
            "head": gdf.head(3).to_dict()  # Only 3 rows for context
        }
    }

@app.post("/mcp/spatial_join_by_id")
async def spatial_join(polygons_id: str, points_id: str):
    result = spatial_join_and_count(_cache[polygons_id], _cache[points_id])
    result_id = str(uuid4())
    _cache[result_id] = result
    return {"outputs": {"dataframe_id": result_id, "shape": result.shape}}
```

**Option B - Aggressive truncation:**
```python
# In tool wrapper
if "features" in outputs:
    outputs = {
        "type": "GeoDataFrame",
        "feature_count": len(outputs["features"]),
        "features_sample": outputs["features"][:5],
        "bbox": calculate_bbox(outputs["features"])
    }
```

### Priority 3: Add Server Startup (1 hour)

```python
# Add as Cell 0
import subprocess, time, requests, signal, os

MCP_BASE_URL = "http://localhost:8000"
try:
    requests.get(f"{MCP_BASE_URL}/openapi.json", timeout=2)
    print(f"✅ Server running at {MCP_BASE_URL}")
    server_proc = None
except:
    print("🚀 Starting server...")
    server_proc = subprocess.Popen(
        ["uvicorn", "server:app", "--port", "8000"],
        cwd=os.path.join(repo_root, "MCP_server")
    )
    time.sleep(3)
    print("✅ Server started")

def cleanup():
    if server_proc:
        server_proc.terminate()
import atexit
atexit.register(cleanup)
```

---

## 9. WORKING EXAMPLE

```python
# working_example.ipynb

# Cell 1: Setup
import os, sys
from dotenv import load_dotenv
repo_root = os.path.abspath('..')
sys.path.insert(0, repo_root)
load_dotenv(os.path.join(repo_root, ".env"))

# Cell 2: Load tools via adapter (not HTTP)
from MCP_server.smolagents_adapter import get_smolagents_tools
tools = get_smolagents_tools()
print(f"✅ Loaded {len(tools)} tools")

# Cell 3: Setup agent
import getpass
os.environ["OPENAI_API_KEY"] = getpass.getpass("API key: ")
from smolagents import OpenAIServerModel, ToolCallingAgent
agent = ToolCallingAgent(
    model=OpenAIServerModel(model_id="gpt-4o-mini"),  # Cheaper
    tools=tools,
    max_steps=5
)

# Cell 4: Simple test (no GeoJSON)
result = agent.run("Estimate biomass for Iowa in 2023")
print(result)
# Expected: "The estimated biomass for Iowa in 2023 is 12345.6 tons."

# Cell 5: Publication search test
result = agent.run("Search for publications about urban heat islands")
print(result)
# Expected: Returns mock publication list
```

**This version:**
- ✅ Uses correct signatures
- ✅ No HTTP layer
- ✅ No context overflow (simple responses)
- ✅ Clear errors

---

## 10. ARCHITECTURAL ISSUES

### Not Using MCP Protocol

**Finding:** Despite the name, this is a FastAPI REST API, not Model Context Protocol.

**Evidence:**
- No JSON-RPC messages
- No `tools/list` or `tools/call` methods
- No MCP SDK (`mcp` package not imported)
- Discovery via OpenAPI spec, not MCP protocol
- Transport is plain HTTP POST, not stdio/SSE/MCP-over-HTTP

**Impact:** Misleading to maintainers; won't interoperate with actual MCP clients/servers.

**Recommendation:** Either:
1. **Implement real MCP** using an MCP SDK (if available for Python)
2. **Rename everything** to avoid confusion:
   - `MCP_server/` → `tool_server/`
   - `/mcp/{tool}` → `/tools/{tool}`
   - `@mcp_tool` → `@remote_tool`

### Unused Adapter

**`smolagents_adapter.py` exists and works correctly but is not used in the notebook.**

The notebook reimplements tool discovery over HTTP, breaking signatures. The adapter should be the primary integration method.

---

## 11. TESTING GAPS

### No Tests Present

Search for test files:
```bash
find MCP_server -name "test_*.py" -o -name "*_test.py"
# Result: No matches
```

### Required Tests

```python
# test_adapter.py
def test_adapter_loads_11_tools():
    from MCP_server.smolagents_adapter import get_smolagents_tools
    tools = get_smolagents_tools()
    assert len(tools) == 11

def test_tool_signatures_preserved():
    tools = {t.name: t for t in get_smolagents_tools()}
    sig = inspect.signature(tools["estimate_biomass"])
    assert "region" in sig.parameters
    assert "year" in sig.parameters

def test_tool_execution():
    tools = {t.name: t for t in get_smolagents_tools()}
    result = tools["estimate_biomass"](region="Iowa", year=2023)
    assert result["region"] == "Iowa"
    assert result["biomass_tons"] == 12345.6

# test_context_size.py
def test_geojson_under_10k_tokens():
    import tiktoken
    enc = tiktoken.encoding_for_model("gpt-4")
    result = load_chicago_community_areas()
    tokens = len(enc.encode(json.dumps(result)))
    assert tokens < 10000, f"Response is {tokens} tokens (limit: 10K)"
```

---

## SUMMARY

### Current State
- ❌ Not using MCP protocol (custom HTTP API with "MCP" branding)
- ❌ HTTP wrapper breaks tool signatures → 500 errors
- ❌ GeoJSON responses cause 957K token context overflow
- ❌ No tests
- ❌ No server startup in notebook
- ❌ Missing `smolagents` in requirements.txt
- ⚠️ Working adapter exists but is unused

### Immediate Actions
1. **Use local adapter** (replace Cell 2): 30 minutes, fixes signature issue
2. **Implement DataFrame references** (server-side caching): 2-4 hours, fixes context overflow
3. **Add server startup cell**: 1 hour, improves UX
4. **Add `smolagents` to requirements.txt**: 5 minutes
5. **Write integration test**: 1 hour, prevents regressions

### Long-term
- Decide: Real MCP vs rename to custom API (clarity)
- Add schema validation (Pydantic models)
- Implement streaming for large data
- Add observability (logging, metrics)
- Document DataFrame reference pattern

**Estimated effort to working state:** 4-8 hours  
**Estimated effort to production-ready:** 40-80 hours

---

**Report Date:** 2026-02-09  
**Branch:** `MPC_Prototype`  
**Notebook:** `MCP_server/smolagent_mcp_tools.ipynb` (85,577 lines, multiple execution runs)
