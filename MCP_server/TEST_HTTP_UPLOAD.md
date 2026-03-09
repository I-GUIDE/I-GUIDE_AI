# Testing MCP HTTP Upload

This guide shows how to test image uploads through your MCP server using the HTTP transport.

---

## 🚀 Quick Start

### Terminal 1: Start MCP Server (HTTP Mode)

```bash
cd /Users/shritan/Desktop/IGUIDE/i-guide-platform-flask-servers/MCP_server

python server.py --http
```

**Expected output:**
```
🚀 Starting I-GUIDE MCP Server (HTTP)
   Server name: I-GUIDE Tools
   Transport: HTTP
   Server: http://localhost:8000
   Endpoint: http://localhost:8000/mcp

🛑 Starting HTTP server...

✅ MCP Server ready with 7 tools
```

### Terminal 2: Run Test

```bash
cd /Users/shritan/Desktop/IGUIDE/i-guide-platform-flask-servers

python MCP_server/test_mcp_http_upload.py
```

**The test will:**
1. Check if MCP server is running
2. Discover tools via MCP protocol
3. Test simple tool (biomass)
4. Ask you for an image path
5. Upload and describe the image via MCP

---

## 📸 Preparing Test Image

### Option 1: Use a Screenshot

```bash
# Take a screenshot (saves to Desktop)
# Press: Cmd + Shift + 4
# Select area
# File appears on Desktop as: Screen Shot 2026-02-18 at...
```

Then when the test asks for path, enter:
```
/Users/shritan/Desktop/Screen Shot 2026-02-18 at 11.20.45 AM.png
```

### Option 2: Use Any Existing Image

Find any `.jpg`, `.png`, or `.gif` on your computer:
```bash
# Examples:
/Users/shritan/Downloads/photo.jpg
/Users/shritan/Documents/map.png
/Users/shritan/Desktop/screenshot.png
```

### Option 3: Download Test Image

```bash
# Download a test image
curl -o ~/Desktop/test_image.jpg https://picsum.photos/800/600
```

---

## ✅ Expected Test Output

```
╔══════════════════════════════════════════════════════════╗
║               MCP HTTP UPLOAD TEST SUITE                 ║
╚══════════════════════════════════════════════════════════╝

🔍 Checking MCP server...
✅ MCP server is running at http://localhost:8000

============================================================
TEST 1: Tool Discovery
============================================================
✅ Found 7 tools:
   • estimate_biomass
   • get_crime_statistics
   • load_chicago_community_areas
   • load_chicago_crime_data
   • describe_image
   • describe_map
   • search_publications

============================================================
TEST 2: Simple Tool Call (No Upload)
============================================================
📡 Calling: estimate_biomass(region='Iowa', year=2023)
✅ Result: {...biomass data...}

============================================================
📸 Image Upload Test
============================================================

Enter path to test image (or press Enter for default):
Path: /Users/shritan/Desktop/test.jpg

📸 Image: /Users/shritan/Desktop/test.jpg
✅ Read 245,632 bytes
✅ Encoded to base64: 327,510 characters

📡 Sending MCP request...
   Method: tools/call
   Tool: describe_image
   Request size: ~330,000 bytes

============================================================
✅ SUCCESS - Image Description:
============================================================
The image shows a map of Chicago displaying crime statistics...
============================================================

============================================================
TEST RESULTS SUMMARY
============================================================
✅ PASS - Tool Discovery
✅ PASS - Simple Tool Call
✅ PASS - Image Upload

🎉 All tests passed!

✅ Your MCP server is working correctly with:
   • HTTP transport
   • Tool discovery
   • Simple tools
   • Image upload tools

🚀 Ready for production deployment!
```

---

## 🐛 Troubleshooting

### Error: "Connection refused"

**Problem:** MCP server not running

**Solution:**
```bash
# Start server in Terminal 1
cd MCP_server
python server.py --http
```

### Error: "Address already in use"

**Problem:** Port 8000 already taken

**Solution:**
```bash
# Check what's using port 8000
lsof -i :8000

# Kill it or change server port
# Edit server.py: mcp.run(transport="http", port=8001)
```

### Error: "File not found"

**Problem:** Image path incorrect

**Solution:**
```bash
# Verify file exists
ls -la /Users/shritan/Desktop/test_image.jpg

# Or copy/paste path from Finder:
# Right-click image → Hold Option → "Copy as Pathname"
```

### Error: "Failed to describe image"

**Problem:** Vision API issue

**Solution:**
```bash
# Check environment variables
cat ../.env | grep VISION

# Required:
# VISION_API_URL=http://149.165.153.129:8000/v1/chat/completions
```

---

## 📊 What This Validates

| Component | Status |
|-----------|--------|
| **MCP Protocol** | ✅ JSON-RPC 2.0 |
| **HTTP Transport** | ✅ Works |
| **Tool Discovery** | ✅ Lists 7 tools |
| **Simple Tools** | ✅ Biomass works |
| **Image Upload** | ✅ Base64 encoding works |
| **Vision API** | ✅ Image description works |
| **Production Ready** | ✅ Remote deployment ready |

---

## 🎯 After Tests Pass

You'll have proven:
1. ✅ MCP HTTP transport works
2. ✅ Image uploads work via MCP
3. ✅ Ready for remote deployment
4. ✅ Ready for LangChain integration

Next steps:
- Deploy MCP server remotely
- Build chatbot with LangChain
- Connect chatbot to MCP server

---

## 🔗 Related Files

- `server.py` - MCP server (now supports HTTP!)
- `test_mcp_http_upload.py` - This test script
- `tools/image_tools.py` - Image description tools
- `test_implementation.py` - Basic smoke tests

---

**Ready to test?** Follow the Quick Start above! 🚀
