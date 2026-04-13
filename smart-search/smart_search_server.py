#!/usr/bin/env python3
"""
Smart Search Server Entry Point

This is the entry point for the production Docker container.
It imports and runs the Flask app from api.server.
"""

import sys
import os

# Add parent directory to path so we can import api / rag_pipeline
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.server import app

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5002))
    debug = os.getenv('DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)