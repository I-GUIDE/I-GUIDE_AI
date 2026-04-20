# RAG Pipeline Tests

This directory contains **pytest-style** tests for the RAG pipeline.  Runnable
verification scripts (interactive CLIs, end-to-end demos) live under the
top-level [`scripts/`](../../scripts/) directory instead.

Run everything that doesn't need live services:

```bash
pytest rag_pipeline/tests/ -v -m "not integration"
```

Run the full suite (requires `OPENSEARCH_NODE`, `VLLM_API_KEY`, etc. in
the repo-root `.env`):

```bash
pytest rag_pipeline/tests/ -v
```

## Pytest files in this directory

| File | What it covers | Needs live services |
|------|---------------|---------------------|
| `test_mcp_cache.py` | TTL cache for `make_langchain_mcp_tools()` — hit/miss, per-module isolation, expiry, clear hook | No — fully mocked |
| `test_opengeodata_search.py` | `get_opengeodata_results()` normalization, state merging, error handling | No — mocks `run_opengeodata` |
| `test_e2e_llm_router.py` | End-to-end LLM router deciding which search backends to invoke | **Yes** — OpenSearch + LLM |
| `test_spatial_routing_e2e.py` | Spatial routing → retrieval → generation path | **Yes** — OpenSearch + Google Maps + LLM |
| `test_state_uniformity.py` | `EvidenceEntry` structure is uniform across search backends | **Yes** — OpenSearch |

## Runnable verification scripts (no longer in this directory)

The scripts previously living here (`test_full_pipeline.py`, `local_rag_test.py`,
`test_reranker.py`, `test_hallucination_check.py`, `demo_pipeline.py`) were
really runnable verification tools, not pytest tests.  They moved to
[`scripts/`](../../scripts/) and lost the `test_` prefix so pytest no longer
tries to collect them:

| Old path | New path |
|----------|----------|
| `rag_pipeline/tests/test_full_pipeline.py` | `scripts/run_full_pipeline.py` |
| `rag_pipeline/tests/local_rag_test.py` | `scripts/run_local_rag_test.py` |
| `rag_pipeline/tests/test_reranker.py` | `scripts/demo_reranker.py` |
| `rag_pipeline/tests/test_hallucination_check.py` | `scripts/check_hallucination.py` |
| `rag_pipeline/tests/demo_pipeline.py` | `scripts/demo_pipeline.py` |

See [`scripts/`](../../scripts/) for usage.

## Environment variables

Tests read configuration from the repo-root `.env` file (loaded automatically).
See [`.env.example`](../../.env.example) for the full list.  The most common:

| Variable | Used by |
|----------|---------|
| `OPENSEARCH_NODE` / `OPENSEARCH_INDEX` | keyword, semantic, spatial search |
| `VLLM_API_KEY` / `VLLM_PROXY` / `VLLM_MODEL` | LLM generation + LLM router |
| `FLASK_EMBEDDING_URL` | semantic search |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | graph search |
| `GOOGLE_MAPS_API_KEY` | NLP-based spatial search |
