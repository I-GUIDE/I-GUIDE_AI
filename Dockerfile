FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install spaCy model for NLP processing
RUN python -m spacy download en_core_web_sm

# Copy application packages
COPY rag_pipeline/ ./rag_pipeline/
COPY agent_runtime/ ./agent_runtime/
COPY services/ ./services/
COPY api/ ./api/

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
