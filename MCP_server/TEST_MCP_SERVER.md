# MCP Server Testing Guide

Choose your testing approach based on what you need:

---

## ✅ Approach 1: Simple HTTP API (Recommended for curl testing)

**Best for:** Quick testing with curl, simple REST API access

### Terminal 1: Start Simple HTTP Server

```bash
cd /Users/shritan/Desktop/IGUIDE/i-guide-platform-flask-servers/MCP_server
python simple_http_server.py
```

**Expected output:**
```
🔍 Loading tools...
✅ Loaded 7 tools
============================================================
🚀 Starting Simple HTTP API for I-GUIDE Tools
============================================================

📍 URL: http://localhost:8001
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
```

**Leave this terminal running.**

### Terminal 2: Test with curl

```bash
# Test 1: List all tools
curl http://localhost:8001/tools | python -m json.tool

# Test 2: Call estimate_biomass
curl -X POST http://localhost:8001/call/estimate_biomass \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"region":"Iowa","year":2023}}' | python -m json.tool

# Test 3: Get tool info
curl http://localhost:8001/tools/estimate_biomass | python -m json.tool

# Test 4: Call search_publications
curl -X POST http://localhost:8001/call/search_publications \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"query":"climate change","limit":3}}' | python -m json.tool
```

### Quick All-Tests Script

```bash
echo "=== Testing Simple HTTP API ==="
echo ""
echo "Test 1: Health check"
curl -s http://localhost:8001/ | python -m json.tool
echo ""
echo "Test 2: List tools"
curl -s http://localhost:8001/tools | python -c "import sys,json; d=json.load(sys.stdin); print(f'Found {len(d[\"tools\"])} tools'); [print(f'  - {t[\"name\"]}') for t in d['tools']]"
echo ""
echo "Test 3: Call estimate_biomass"
curl -s -X POST http://localhost:8001/call/estimate_biomass \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"region":"Iowa","year":2023}}' | python -m json.tool
echo ""
echo "✅ Tests complete!"
```

---

## 🔧 Approach 2: Full MCP Server (For MCP Inspector)

**Best for:** Testing with official MCP Inspector tool, full MCP protocol

### Terminal 1: Start MCP Server

```bash
cd /Users/shritan/Desktop/IGUIDE/i-guide-platform-flask-servers/MCP_server
python server.py
```

**Expected output:**
```
✅ Registered 7 MCP tools
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

**Leave this terminal running.**

### Terminal 2: Test with MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector
```

**Steps:**
1. Browser opens at `http://localhost:5173`
2. In connection URL field, enter: `http://localhost:8000/sse`
3. Click "Connect"
4. You should see 7 tools listed
5. Click on a tool (e.g., `estimate_biomass`)
6. Fill in parameters: `region: "Iowa"`, `year: 2023`
7. Click "Execute"
8. See the result

**Note:** The MCP server uses the streamable HTTP protocol which requires proper session management. This is why curl testing is complex. Use the Simple HTTP API (Approach 1) for curl testing, or use MCP Inspector for visual testing.

---

## 📊 Comparison

| Feature | Simple HTTP API | Full MCP Server |
|---------|----------------|-----------------|
| Port | 8001 | 8000 |
| Protocol | REST (simple JSON) | MCP (streamable HTTP) |
| curl testing | ✅ Easy | ❌ Complex (needs sessions) |
| MCP Inspector | ❌ Not compatible | ✅ Compatible |
| Notebook usage | ✅ Via local adapter | ✅ Via local adapter |
| Production use | ✅ Good for REST APIs | ✅ Good for MCP clients |

---

## 🎯 Which Should You Use?

- **For quick testing and debugging:** Use **Simple HTTP API** (Approach 1)
- **For MCP Inspector visual testing:** Use **Full MCP Server** (Approach 2)
- **For notebook (smolagents):** Use **local adapter** (no server needed!)

---

## ✅ Verification Checklist

After testing, verify:

### Approach 1 (Simple HTTP API):
- [ ] Server shows "Uvicorn running on http://0.0.0.0:8001"
- [ ] `/tools` endpoint returns 7 tools
- [ ] `/call/estimate_biomass` returns valid result
- [ ] `lsof -i :8001` shows process listening

### Approach 2 (MCP Server):
- [ ] Server shows "Uvicorn running on http://0.0.0.0:8000"
- [ ] MCP Inspector connects successfully
- [ ] Can see 7 tools in Inspector UI
- [ ] Can execute tools in Inspector
- [ ] `lsof -i :8000` shows process listening

---

## 🐛 Troubleshooting

### Port already in use
```bash
# Kill process on port 8001
lsof -ti:8001 | xargs kill -9

# Or use different port
python simple_http_server.py --port 8002
```

### Module not found
```bash
# Make sure you're NOT in a virtualenv
deactivate

# Verify Python finds the modules
python -c "import mcp; import smolagents; print('OK')"
```

### Tools not loading
```bash
# Check tool files
ls -la /Users/shritan/Desktop/IGUIDE/i-guide-platform-flask-servers/MCP_server/tools/

# Run test suite
python test_implementation.py
```
