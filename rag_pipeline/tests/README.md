# RAG Pipeline Tests

This directory contains full pipeline end-to-end tests for validating the complete RAG (Retrieval-Augmented Generation) pipeline with real functionality.

## 🧪 Full Pipeline Test Files

### Core Pipeline Tests

#### `test_full_pipeline.py` ⭐
**Complete end-to-end pipeline test with detailed step-by-step output**

Shows the entire flow from memory → routing → search → generation with detailed logging.

```bash
PYTHONPATH=. python rag_pipeline/tests/test_full_pipeline.py
PYTHONPATH=. python rag_pipeline/tests/test_full_pipeline.py "custom query"
```

**Output includes:**
- Step 1: Memory module (chat history augmentation)
- Step 2: Routing & search decisions
- Step 3: Context construction
- Step 4: Full LLM prompt
- Step 5: AnvilGPT generation
- Step 6: Final answer with citations

---

#### `local_rag_test.py` ⭐
**Full pipeline test with connectivity checks**

Tests the complete RAG pipeline with pre-flight connectivity validation.

```bash
PYTHONPATH=. python rag_pipeline/tests/local_rag_test.py
PYTHONPATH=. python rag_pipeline/tests/local_rag_test.py "custom query"
PYTHONPATH=. python rag_pipeline/tests/local_rag_test.py --skip-checks  # Skip connectivity
```

**Features:**
- OpenSearch connectivity validation
- Flask embedding service health check
- AnvilGPT API validation
- Retrieval summary with source breakdown
- Generation output with citations
- Confidence scoring

---

#### `test_e2e_llm_router.py` ⭐
**Full pipeline test with LLM router**

Tests the complete RAG pipeline with LLM-based routing decisions and real search backends.

```bash
PYTHONPATH=. python rag_pipeline/tests/test_e2e_llm_router.py
PYTHONPATH=. python rag_pipeline/tests/test_e2e_llm_router.py "custom query"
```

**Tests:**
- LLM router decision making
- Real search backend execution (keyword, semantic, spatial, Neo4j)
- Complete pipeline flow with generation
- Multi-module result merging

---

#### `test_state_uniformity.py` ⭐
**Validates that all search modules maintain uniform state structure**

Ensures keyword, semantic, and spatial search all produce compatible `EvidenceEntry` structures for generation.

```bash
PYTHONPATH=. python rag_pipeline/tests/test_state_uniformity.py
```

**Verifies:**
- All evidence entries follow `EvidenceEntry` schema
- Document payloads are uniform across sources
- Generation module processes mixed sources seamlessly
- State structure is preserved throughout pipeline

---

#### `test_spatial_routing_e2e.py` ⭐
**Full pipeline test for spatial search path**

Tests the complete spatial routing → retrieval → generation flow with real APIs.

```bash
PYTHONPATH=. python rag_pipeline/tests/test_spatial_routing_e2e.py
pytest rag_pipeline/tests/test_spatial_routing_e2e.py -v
```

**Tests:**
- Spatial routing decisions
- Real Google Maps API geocoding
- Real OpenSearch spatial queries
- Real AnvilGPT generation
- Complete end-to-end spatial path

---

## 🚀 Quick Start

### Test the Complete Pipeline
```bash
# Full pipeline with all steps
PYTHONPATH=. python rag_pipeline/tests/test_full_pipeline.py

# Full pipeline with connectivity checks
PYTHONPATH=. python rag_pipeline/tests/local_rag_test.py

# Full pipeline with LLM router
PYTHONPATH=. python rag_pipeline/tests/test_e2e_llm_router.py

# Verify state uniformity across search modules
PYTHONPATH=. python rag_pipeline/tests/test_state_uniformity.py
```

---

## 📋 Environment Variables Required

Tests read from `rag_pipeline/.env.local`:

```bash
# OpenSearch
OPENSEARCH_NODE=http://149.165.159.254:9200
OPENSEARCH_INDEX=iguide-platform-embeddings-dev
OPENSEARCH_USERNAME=admin
OPENSEARCH_PASSWORD=***

# Flask Embedding Service
FLASK_EMBEDDING_URL=http://149.165.153.129:5000

# AnvilGPT LLM
ANVILGPT_URL=https://anvilgpt.rcac.purdue.edu/api/chat/completions
ANVILGPT_KEY=sk-***
ANVILGPT_MODEL=gpt-oss:120b

# Neo4j (optional)
NEO4J_URI=neo4j://149.165.155.135:7687
NEO4J_USER=***
NEO4J_PASSWORD=***

# Google Maps (optional, for spatial search)
GOOGLE_MAPS_API_KEY=***
```

---

## ✅ Test Coverage

| Component | Test Coverage |
|-----------|---------------|
| Memory Module | ✅ test_full_pipeline.py |
| Keyword Search | ✅ All pipeline tests |
| Semantic Search | ✅ All pipeline tests |
| Spatial Search | ✅ test_spatial_routing_e2e.py, test_state_uniformity.py |
| Neo4j Search | ✅ test_e2e_llm_router.py |
| LLM Router | ✅ test_e2e_llm_router.py |
| LLM Generation | ✅ All pipeline tests |
| State Uniformity | ✅ test_state_uniformity.py |
| Citations | ✅ All pipeline tests |

---

## 🐛 Troubleshooting

### LLM Generation Timeouts
If generation times out:
1. Verify `ANVILGPT_URL` and `ANVILGPT_KEY` are correct
2. Check `ANVILGPT_MODEL` matches available models
3. Test connectivity separately with curl

### Spatial Search No Results
If spatial search returns no results:
1. Check `GOOGLE_MAPS_API_KEY` is set
2. Verify OpenSearch documents have `spatial-bounding-box-geojson` field

---

## 📝 Test Results Example

```
================================================================================
  COMPLETE RAG PIPELINE TEST
================================================================================

✅ STEP 1: Memory Module → Query initialized
✅ STEP 2: Routing & Search → 5 documents retrieved (keyword: 5)
✅ STEP 3: Context Construction → 4,097 chars
✅ STEP 4: Prompt Construction → 4,381 chars
✅ STEP 5: LLM Generation → gpt-oss:120b (1,935 chars)
✅ STEP 6: Final Answer → 5 citations, confidence: 0.7

🎉 RAG PIPELINE TEST COMPLETED
```

