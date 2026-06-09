FROM python:3.11-slim

# Install system dependencies for building Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (for OpenSearch client and WKT parser)
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies (minimal — this service does
# not need the geospatial/LLM stack, so it uses its own requirements file and
# does NOT need GDAL system libs)
COPY embedding-server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Node.js dependencies
COPY embedding-server/package.json embedding-server/package-lock.json* ./embedding-server/
WORKDIR /app/embedding-server
RUN npm install --production

# Copy application code
WORKDIR /app
COPY embedding-server/ ./embedding-server/

# Pre-download HuggingFace model so the container starts without network access
RUN python -c "from transformers import AutoTokenizer, AutoModel; AutoTokenizer.from_pretrained('sentence-transformers/all-MiniLM-L6-v2'); AutoModel.from_pretrained('sentence-transformers/all-MiniLM-L6-v2')"

# Create a non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/health', timeout=5)" || exit 1

# Run with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", "--timeout", "120", "embedding-server.dense_embedding_server:app"]
