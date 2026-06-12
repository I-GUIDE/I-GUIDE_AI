FROM python:3.11-slim

# Install system dependencies (GDAL for geopandas/fiona used by the in-process
# MCP spatial tools the analysis agent loads)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    ca-certificates \
    gnupg \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI (client only) so sandboxed code execution can shell out to the HOST
# Docker daemon (Docker-out-of-Docker). The host socket must be mounted at runtime;
# no daemon/containerd is installed here.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Headless matplotlib + package root on PYTHONPATH so the agent can import the
# in-process MCP tool modules (MCP_server/tools/*) and rag_pipeline/agent_runtime.
# GDAL include paths so fiona/rasterio build against the system GDAL.
ENV MPLBACKEND=Agg \
    PYTHONPATH=/app \
    CPLUS_INCLUDE_PATH=/usr/include/gdal \
    C_INCLUDE_PATH=/usr/include/gdal

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install spaCy model for NLP processing
RUN python -m spacy download en_core_web_sm

# Copy application packages. MCP_server is required because the agent loads MCP
# tools in-process (agent_runtime.langchain_mcp_tools imports MCP_server/tools/*).
COPY rag_pipeline/ ./rag_pipeline/
COPY agent_runtime/ ./agent_runtime/
COPY api/ ./api/
COPY MCP_server/ ./MCP_server/

# Pre-download SentenceTransformer model so the container doesn't need
# outbound network access to HuggingFace on first request
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Create writable upload directory and non-root user
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/agent_chat_files/uploads \
    && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 5002

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5002/health', timeout=5)" || exit 1

# Run with gunicorn (more workers for LLM processing)
CMD ["gunicorn", "--bind", "0.0.0.0:5002", "--workers", "2", "--threads", "4", "--timeout", "600", "--access-logfile", "-", "--error-logfile", "-", "api.server:app"]
