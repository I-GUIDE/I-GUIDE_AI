FROM python:3.11-slim

# System deps: build tools + GDAL for geopandas/fiona/rasterio
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Headless matplotlib and package root on PYTHONPATH so MCP tools can do
# `from rag_pipeline...` / `from agent_runtime...` without sys.path hacks
ENV MPLBACKEND=Agg \
    PYTHONPATH=/app

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy packages the tool modules import from
COPY MCP_server/ ./MCP_server/
COPY rag_pipeline/ ./rag_pipeline/
COPY agent_runtime/ ./agent_runtime/
COPY services/ ./services/

# Create non-root user with writable upload dir
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/agent_chat_files/uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=5)" || exit 1

CMD ["uvicorn", "MCP_server.server:app", "--host", "0.0.0.0", "--port", "8000"]
