# Live Integration Tests

These scripts exercise real external services. They are **not** part of the automated pytest suite — run them manually when you need to verify a live integration.

Run all scripts from the **repo root**:

```bash
python tests/live/test_keyword.py
python tests/live/test_semantic.py
python tests/live/test_neo4j.py
python tests/live/test_spatial.py
python tests/live/test_opengeodata_live.py
```

---

## Required environment variables (repo-root `.env`)

| Script | Required vars | Optional vars |
|---|---|---|
| `test_keyword.py` | `OPENSEARCH_NODE`, `OPENSEARCH_INDEX` | `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD` |
| `test_semantic.py` | `OPENSEARCH_NODE`, `OPENSEARCH_INDEX`, `FLASK_EMBEDDING_URL` | `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD` |
| `test_neo4j.py` | `NEO4J_CONNECTION_STRING` (or `NEO4J_URI`), `NEO4J_USER`, `NEO4J_PASSWORD` | `NEO4J_DB` |
| `test_spatial.py` | `GOOGLE_MAPS_API_KEY`, `OPENSEARCH_NODE`, `OPENSEARCH_INDEX` | `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD` |
| `test_opengeodata_live.py` | `VLLM_PROXY` (or `OPENAI_API_BASE`), `VLLM_API_KEY` (or `OPENAI_API_KEY`) | — |

---

## What each script tests

- **test_keyword.py** — BM25 `match` query over OpenSearch (`contents` field). Verifies connectivity, hit shape, and score ordering.
- **test_semantic.py** — Dense kNN query via FLASK_EMBEDDING_URL → OpenSearch `knn` on `contents-embedding`. Tests the embedding service + vector index.
- **test_neo4j.py** — Keyword search and pattern detection against the Neo4j graph. Tests connection, Cypher execution, and title/tag matching.
- **test_spatial.py** — Full spatial pipeline: spaCy NER → Google Maps geocoding → OpenSearch `geo_shape` query.
- **test_opengeodata_live.py** — Federated external catalog search (STAC, OGC Records, CKAN, NASA CMR) via `get_opengeodata_results`.
