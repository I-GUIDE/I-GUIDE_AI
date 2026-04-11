# I-GUIDE Platform Flask Servers

A multi-agent RAG (Retrieval-Augmented Generation) system for geospatial research on the [I-GUIDE Platform](https://platform.i-guide.io). Searches across keyword, semantic, spatial, knowledge graph, and external data catalogs to answer research questions with cited, grounded answers.

## Architecture

The codebase has three main packages:

```
rag_pipeline/          Search, retrieval, generation (the RAG engine)
agent_runtime/         Multi-agent orchestration (decides how to answer)
MCP_server/            Geospatial analysis tools exposed via Model Context Protocol
```

Plus supporting services:

```
embedding-server/              Dense embedding generation (port 5000)
metadata-extraction-server/    Spatial metadata extraction (port 5001)
```

### How a query flows

```
User query
  -> agent_runtime: OrchestratorAgent decides which sub-agents to invoke
       -> SearchAgent: retrieves evidence using search tools
       -> AnalysisAgent: synthesizes answer from evidence
       -> CodeAgent: generates executable code when needed
       -> DirectAnswerAgent: answers from chat history alone
  -> rag_pipeline: search backends + generation
       -> search/keyword.py:          BM25 via OpenSearch
       -> search/semantic.py:         Vector kNN via embedding service
       -> search/neo4j.py:            Knowledge graph (3-tier: pattern tools -> Text2Cypher -> keyword)
       -> search/spatial.py:          Geographic filtering via NLP + Google Maps geocoding
       -> search/opengeodata.py:      Federated STAC/OGC/CKAN/NASA CMR catalog search
       -> generation.py:              LLM answer synthesis with citations
  -> Response with answer, citations, and evidence
```

## Project Structure

```
agent_runtime/                   Multi-agent orchestration
  graph_state.py                 Type definitions, tool-name constants
  intent_classifier.py           Query intent classification (analysis/code/discovery/hybrid)
  tool_policy.py                 Tool filtering by intent
  executor_factory.py            Agent prompts, LLM config, executor builders
  graph_nodes.py                 Tool factories for the orchestrator agent
  graph_runtime.py               Public API: run_agent_query(), stream_agent_query_events()
  runtime_utils.py               Response parsing, trace building

rag_pipeline/                    RAG engine
  api_server.py                  Flask REST API (port 5002)
  pipeline.py                    Pipeline orchestration (memory -> search -> rerank -> generate)
  routing.py                     Retrieval -> reranking -> generation glue
  state.py                       Shared AgentState schema
  generation.py                  LLM answer synthesis with citations
  reranker_llm.py                LLM-as-judge reranking
  reranker.py                    ColBERT reranking (optional)
  hallucination_check.py         Post-generation audit (optional)
  router_llm.py                  LLM-based search method selection
  memory_module.py               Chat history, follow-up detection, query augmentation
  llm_utils.py                   LLM client wrapper (OpenAI-compatible)
  agent_chat_service.py          Chat session management
  agent_file_store.py            File upload/download storage
  langchain_tool.py              RAG pipeline as a LangChain StructuredTool
  langchain_granular_tools.py    Individual search backends as LangChain tools
  langchain_mcp_tools.py         MCP server bridge into LangChain
  langchain_file_tools.py        File I/O tools for agents
  search/                        Search backends
    core.py                      Search orchestrator (runs all backends)
    keyword.py                   OpenSearch BM25 keyword search
    semantic.py                  Vector embedding kNN search
    neo4j.py                     Neo4j knowledge graph search
    neo4j_graph_tools.py         Prewritten Cypher templates + pattern detection
    agents.py                    3-tier Neo4j dispatcher + LLM-generated OpenSearch DSL
    spatial.py                   Geographic search (NLP + geocoding + geo_shape)
    opengeodata.py               Federated search (STAC, OGC, CKAN, NASA CMR)
    utils.py                     Logging, env parsing, normalization

MCP_server/                      Model Context Protocol tool server
  server.py                      FastMCP server with auto-discovery (port 8000)
  tools/
    data_tools.py                Chicago community areas + crime data loading
    spatial_analysis_tools.py    Spatial joins, choropleth maps, resource search
    search_tools.py              External resource search (DuckDuckGo)
    image_tools.py               Vision model image/map analysis
    notebook_workflow_tools.py   Jupyter notebook -> callable tool conversion
    generated_notebook_tools.py  Dynamically registered notebook-derived tools
  notebook_workflow_builder.py   Notebook AST parsing and artifact generation
  smolagents_adapter.py          Smolagents-compatible tool wrapper

embedding-server/                Dense embedding service
  dense_embedding_server.py      Flask server for all-MiniLM-L6-v2 embeddings (port 5000)
  dense_embedding.py             Embedding model wrapper
  create_embedding_for_existing.py  Batch embedding generation
  reindex_wkt_spatial.py         WKT to GeoJSON conversion

metadata-extraction-server/      Metadata extraction
  minio_webhook.py               MinIO webhook listener (port 5001)
  extract_metadata.py            Spatial metadata extraction
  extract_metadata_code_notebooks.py  Code/notebook metadata extraction
```

## Quick Start

### Prerequisites

- Python 3.11+
- OpenSearch instance
- LLM endpoint (OpenAI-compatible, e.g., AnvilGPT or vLLM)

### Setup

```sh
cp .env.example .env
# Edit .env with your OpenSearch, LLM, and Neo4j credentials

pip install -r requirements.txt
```

### Run the API server

```sh
python -m rag_pipeline.api_server
# Runs on port 5002
```

### Run the agent from CLI

```sh
python -m agent_runtime.graph_runtime \
  "Generate the code for a RAG grader" \
  --tool-strategy granular \
  --include-mcp-tools
```

```sh
python -m agent_runtime.graph_runtime \
  "What's the risk of aging dams" \
  --tool-strategy full_pipeline
```

```sh
python -m agent_runtime.graph_runtime \
  "Analyze: What's the community in Chicago with most theft" \
  --tool-strategy granular \
  --include-mcp-tools
```

### Run with Docker Compose

```sh
docker-compose up
```

Starts three services: embedding-server (5000), metadata-extraction-server (5001), rag-pipeline (5002).

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/query` | POST | RAG pipeline (retrieval + generation) |
| `/agent/chat` | POST | Agent-backed conversation |
| `/agent/chat/stream` | POST | Streaming agent responses (SSE) |
| `/agent/files/upload` | POST | Upload files for analysis |
| `/agent/files/<id>/download` | GET | Download files |
| `/query/batch` | POST | Batch query processing |

## Configuration

See [.env.example](.env.example) for all configuration options. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENSEARCH_NODE` | Yes | OpenSearch endpoint |
| `OPENSEARCH_INDEX` | Yes | Document index name |
| `VLLM_API_KEY` or `OPENAI_KEY` | Yes | LLM API key |
| `VLLM_PROXY` or `OPENAI_BASE_URL` | Yes | LLM endpoint URL |
| `FLASK_EMBEDDING_URL` | Yes | Embedding service URL |
| `NEO4J_URI` | No | Neo4j connection (enables graph search) |
| `GOOGLE_MAPS_API_KEY` | No | Enables NLP-based spatial search |
| `MCP_SERVER_URL` | No | MCP tool server URL (default: http://127.0.0.1:8000/mcp) |

## Testing

```sh
# Run unit tests (no live services needed)
python -m pytest MCP_server/ rag_pipeline/tests/test_opengeodata_search.py test_opengeodata.py -v

# Run integration tests (requires OpenSearch, LLM, etc.)
python -m pytest rag_pipeline/tests/ -v

# Run Neo4j search tests (requires Neo4j)
python test_neo4j.py
```

## LangSmith Tracing

```sh
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=your_langsmith_api_key
export LANGSMITH_PROJECT=i-guide-agent
```

Agent execution from `agent_runtime.graph_runtime` will appear in LangSmith traces.

## Inspect Agent Flow

```sh
python tools/inspect_agent_query_graph.py
python tools/inspect_agent_query_graph.py --format mermaid
python tools/inspect_agent_query_graph.py --format json
```
