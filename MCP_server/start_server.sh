#!/bin/bash
# Start the I-GUIDE MCP Server

echo "🚀 Starting I-GUIDE MCP Server..."
echo ""

# Check if MCP is installed
if ! python -c "import mcp" 2>/dev/null; then
    echo "❌ Error: MCP SDK not installed"
    echo "   Please run: pip install --user 'mcp[cli]'"
    exit 1
fi

# Check if in correct directory
if [ ! -f "server.py" ]; then
    echo "❌ Error: server.py not found"
    echo "   Please run this script from the MCP_server directory"
    exit 1
fi

# Start the server
python server.py
