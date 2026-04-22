# LLM Routing Integration Complete

## Summary

Integrated LLM-based routing for graph (Neo4j) and spatial searches while keeping keyword and semantic searches always enabled.

## Changes Made

### 1. Enhanced Router Prompt (`rag_pipeline/router_llm.py`)

**Lines 135-151**: Improved the graph search guidelines with specific triggers:

- **Author/contributor queries**: "by [person]", "from [author]", "published by", "created by", "uploaded by", "authored by"
- **Organization queries**: "from [org]", "by [institution]", "affiliated with", "maintained by", "developed by"
- **Tag/category queries**: "tagged with", "categorized as", "labeled as", "type of", "classified as"
- **Relationship queries**: "related to", "connected to", "similar to", "associated with", "linked to", "references"
- **Entity-centric**: any query mentioning specific people, organizations, contributors, or tags

**Examples added**: "publications by John Smith", "datasets from NASA", "resources tagged climate", "papers affiliated with USGS"

### 2. Updated Search Core (`rag_pipeline/search_core.py`)

**Key Changes**:

1. **Always-on searches** (lines 44-76):
   - Keyword search: Always executed
   - Semantic search: Always executed

2. **LLM-routed searches** (lines 78-157):
   - Graph (Neo4j) search: Conditionally executed based on LLM router decision
   - Spatial search: Conditionally executed based on LLM router decision

3. **Routing Logic** (lines 84-114):
   - Environment variable control: `USE_LLM_ROUTING` (default: "true")
   - LLM router instantiation and query planning
   - Graceful fallback to heuristics on error
   - Detailed logging of routing decisions and rationale

4. **Enhanced Observability** (lines 100-104, 132-136, 154-157):
   - Logs LLM routing decisions with rationale
   - Records routing decisions in trace observability
   - Tracks whether searches were executed or skipped with reasons

## Architecture

```
Query Input
    ↓
┌─────────────────────────────────────┐
│  Always Execute (Parallel)          │
│  • Keyword Search                   │
│  • Semantic Search                  │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│  LLM Router Decision                │
│  (router_llm.LLMRouter)             │
│                                     │
│  Analyzes query for:                │
│  • Graph triggers (authors, orgs,   │
│    tags, relationships)             │
│  • Spatial triggers (locations,     │
│    coordinates, regions)            │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│  Conditional Execute                │
│  • Graph Search (if use_graph)      │
│  • Spatial Search (if use_spatial)  │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│  Always Execute                     │
│  • OpenGeoData Search               │
└─────────────────────────────────────┘
    ↓
Merged Evidence → Reranking → Generation
```

## Configuration

### Environment Variable

```bash
# Enable LLM routing (default)
USE_LLM_ROUTING=true

# Disable LLM routing (fallback to heuristics)
USE_LLM_ROUTING=false
```

### Fallback Behavior

If LLM routing fails or is disabled, the system falls back to simple heuristics:
- **Graph search**: Enabled if query contains "graph" (case-insensitive) or `session_context.use_neo4j` is True
- **Spatial search**: Enabled if `session_context.use_spatial` is True

## Benefits

1. **Intelligent Routing**: LLM analyzes query semantics to determine optimal search modalities
2. **Efficiency**: Avoids unnecessary graph/spatial searches for irrelevant queries
3. **Reliability**: Graceful fallback ensures system continues working even if LLM routing fails
4. **Observability**: Detailed logging and tracing of routing decisions
5. **Flexibility**: Environment variable control for easy testing and debugging
6. **Performance**: Keyword and semantic searches always run (core functionality), while expensive graph/spatial searches are intelligently gated

## Testing Recommendations

### Test Cases for Graph Search

1. **Author queries**: "publications by John Smith"
2. **Organization queries**: "datasets from NASA"
3. **Tag queries**: "resources tagged climate"
4. **Relationship queries**: "papers related to water quality"
5. **Negative case**: "what is climate change" (should NOT trigger graph)

### Test Cases for Spatial Search

1. **Location queries**: "data near Chicago"
2. **Coordinate queries**: "datasets at 41.8781° N, 87.6298° W"
3. **Region queries**: "resources within 10 miles of downtown"
4. **Negative case**: "climate change impacts" (should NOT trigger spatial)

### Monitoring

Check logs for:
- `🤖 LLM Router decision: graph=X, spatial=Y`
- `Rationale: {...}` (shows LLM's reasoning)
- `retrieval_routing_decisions` in trace observability

## Code Quality

- ✅ No linter errors
- ✅ Type hints preserved
- ✅ Comprehensive error handling
- ✅ Detailed logging
- ✅ Backward compatible (fallback to heuristics)
- ✅ Environment variable control
- ✅ Clear comments and structure

## Next Steps (Optional)

1. Monitor LLM routing accuracy in production
2. Tune routing prompt based on false positives/negatives
3. Add metrics collection for routing decisions
4. Consider caching routing decisions for identical queries
5. Experiment with different LLM models for routing
