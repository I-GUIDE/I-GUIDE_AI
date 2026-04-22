# Neo4j Agent Search Upgrade

**Date:** 2026-03-11  
**Status:** ✅ **IMPLEMENTED - LLM Agent Search Active**

---

## Summary

Upgraded Neo4j search from basic keyword matching to **LLM-powered intelligent graph traversal** while keeping the old implementation available for reference and rollback.

---

## What Changed

### 1. **Search Method Upgrade**

| Aspect | Old (Basic) | New (LLM Agent) |
|--------|-------------|-----------------|
| **Query Type** | Simple text matching | Intelligent graph traversal |
| **Relationships** | Not traversed (only counted) | Actively traversed for results |
| **Author Queries** | ❌ "by John Smith" → text match only | ✅ Follows AUTHORED_BY relationships |
| **Org Queries** | ❌ "from NASA" → text match only | ✅ Follows AFFILIATED_WITH relationships |
| **Tag Queries** | ⚠️ Array contains check | ✅ Follows TAGGED_WITH relationships |
| **Future-Proof** | ❌ Hardcoded, breaks on schema changes | ✅ Auto-discovers schema, adapts to changes |
| **Fallback** | N/A | ✅ Falls back to basic search on error |

### 2. **Files Modified**

#### `rag_pipeline/search_core.py`
- **Added**: Import for `get_neo4j_agent_results` from `search_agents.py`
- **Added**: Environment variable `USE_NEO4J_AGENT_SEARCH` (default: true)
- **Modified**: Graph search execution block (lines 124-144)
- **Kept**: Old `retrieve_neo4j` import for reference and fallback

**Key Changes:**
```python
# NEW: Intelligent routing between agent and basic search
if use_agent_search and NEO4J_AGENT_AVAILABLE:
    try:
        neo_hits = get_neo4j_agent_results(query, limit=limit)  # LLM agent
    except Exception:
        neo_hits = retrieve_neo4j(state)  # Fallback to basic
else:
    neo_hits = retrieve_neo4j(state)  # Basic search
```

#### `.env.example`
- **Added**: `USE_NEO4J_AGENT_SEARCH=true` flag with documentation

### 3. **Files Preserved (Not Deleted)**

✅ **`rag_pipeline/search_neo4j.py`** - Original basic search implementation
- Still imported and available
- Used as fallback when agent search fails
- Can be re-enabled by setting `USE_NEO4J_AGENT_SEARCH=false`

✅ **`rag_pipeline/search_agents.py`** - Already existed, now actively used
- Contains `get_neo4j_agent_results()` function
- Handles LLM-based Cypher generation
- Includes schema discovery and query optimization

---

## How It Works

### **Execution Flow**

```
User Query: "publications by John Smith"
    ↓
LLM Router: Decides to use graph search
    ↓
┌─────────────────────────────────────────────┐
│  Neo4j Agent Search (NEW)                   │
│  1. Discover schema (cached 5 min)          │
│  2. LLM generates Cypher:                   │
│     MATCH (person:Person)-[:AUTHORED_BY]->  │
│           (resource:Resource)                │
│     WHERE person.name CONTAINS 'john smith' │
│     RETURN resource, score                  │
│  3. Execute query                           │
│  4. Return results                          │
└─────────────────────────────────────────────┘
    ↓ (if error)
┌─────────────────────────────────────────────┐
│  Fallback to Basic Search (OLD)             │
│  MATCH (r)                                  │
│  WHERE r.title CONTAINS 'john smith'        │
│  RETURN r, score                            │
└─────────────────────────────────────────────┘
    ↓
Results merged into evidence
```

### **Schema Discovery (Automatic)**

Every 5 minutes, the agent queries Neo4j to discover:

```cypher
-- Discover all labels
CALL db.labels() YIELD label RETURN label

-- Discover all relationships
CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType

-- Discover properties for each label
MATCH (n:Resource) WITH n LIMIT 3 RETURN keys(n)
```

This schema is passed to the LLM to generate intelligent queries.

---

## Configuration

### **Environment Variables**

```bash
# Enable LLM agent search (default, recommended)
USE_NEO4J_AGENT_SEARCH=true

# Disable LLM agent search (revert to basic keyword search)
USE_NEO4J_AGENT_SEARCH=false

# Required for LLM agent search
OPENAI_KEY=your-api-key
OPENAI_BASE_URL=https://your-anvilgpt-url/api/chat/completions
OPENAI_CHAT_MODEL=gpt-4o-mini  # or your preferred model

# Neo4j connection (required for both methods)
NEO4J_CONNECTION_STRING=bolt://your-neo4j-host:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

### **Feature Flags**

| Flag | Default | Purpose |
|------|---------|---------|
| `USE_LLM_ROUTING` | true | Enable LLM-based routing for graph/spatial |
| `USE_NEO4J_AGENT_SEARCH` | true | Enable LLM agent for Neo4j graph traversal |

---

## Benefits

### **1. Intelligent Query Understanding**

**Example Query:** "publications by John Smith"

**Old Approach:**
```cypher
MATCH (r)
WHERE r.title CONTAINS 'john smith' OR r.contents CONTAINS 'john smith'
RETURN r
```
❌ Returns documents mentioning "John Smith" in text, not authored by him

**New Approach:**
```cypher
MATCH (person:Person)-[:AUTHORED_BY]->(resource:Resource)
WHERE toLower(person.name) CONTAINS 'john smith'
RETURN resource AS node, score
ORDER BY score DESC
```
✅ Returns documents actually authored by John Smith

---

### **2. Future-Proof Schema Adaptation**

**Scenario:** You add new node types and relationships

```cypher
// Today
(Resource)-[:AUTHORED_BY]->(Person)

// 6 months later
(Resource)-[:AUTHORED_BY]->(Person)-[:WORKS_AT]->(Institution)
(Resource)-[:FUNDED_BY]->(Grant)-[:FROM]->(Agency)
(Resource)-[:USES_DATA_FROM]->(Dataset)
```

**Old Approach:** ❌ Requires code changes to support new patterns

**New Approach:** ✅ Automatically discovers and uses new patterns

---

### **3. Multi-Hop Traversal**

**Example Query:** "datasets used by NASA publications"

**Old Approach:** ❌ Cannot traverse multi-hop relationships

**New Approach:** ✅ LLM generates:
```cypher
MATCH (org:Organization {name: 'NASA'})<-[:AFFILIATED_WITH]-(resource:Resource)-[:USES_DATA_FROM]->(dataset:Dataset)
RETURN dataset AS node, count(resource) AS score
ORDER BY score DESC
```

---

### **4. Graceful Degradation**

If anything fails, the system automatically falls back:

```
LLM Agent Search
    ↓ (fails)
Basic Keyword Search
    ↓ (fails)
Continue without Neo4j
```

System **never breaks**, always returns results.

---

## Testing

### **Test Queries**

#### **Author Queries** (Requires `AUTHORED_BY` relationships)
```
"publications by John Smith"
"datasets created by Jane Doe"
"resources uploaded by Alice"
```

#### **Organization Queries** (Requires `AFFILIATED_WITH` relationships)
```
"datasets from NASA"
"publications by USGS"
"resources from University of Illinois"
```

#### **Tag Queries** (Requires `TAGGED_WITH` relationships)
```
"resources tagged climate"
"datasets categorized as hydrology"
"publications labeled water quality"
```

#### **Relationship Queries** (Requires `RELATED_TO`/`CITES` relationships)
```
"papers related to climate change"
"datasets similar to water quality study"
"resources connected to urban planning"
```

### **Verify Agent Search is Active**

Check logs for:
```
🤖 Using LLM agent search for Neo4j (intelligent graph traversal)
🕸️ NEO4J search returned X documents:
```

If you see:
```
Using basic Neo4j search (set USE_NEO4J_AGENT_SEARCH=true for LLM agent)
```
Then agent search is disabled.

---

## Rollback Instructions

If you need to revert to the old basic search:

### **Option 1: Environment Variable (Recommended)**
```bash
# In your .env file
USE_NEO4J_AGENT_SEARCH=false
```

### **Option 2: Code Revert**
```bash
# Revert search_core.py to use only retrieve_neo4j
git diff HEAD~1 rag_pipeline/search_core.py
git checkout HEAD~1 -- rag_pipeline/search_core.py
```

---

## Monitoring

### **Metrics to Track**

1. **Search Quality**
   - Are author/org/tag queries returning correct results?
   - Compare result relevance before/after upgrade

2. **Performance**
   - LLM agent adds ~200-500ms latency per Neo4j search
   - Schema discovery cached (5 min TTL) adds ~100ms on cache miss

3. **Error Rate**
   - Monitor fallback frequency: `search_method:neo4j_basic_fallback`
   - Check logs for LLM generation failures

4. **Cost**
   - Each Neo4j search = 1 LLM call (~200-400 tokens)
   - Estimate: $0.0001-0.0003 per search (GPT-4o-mini pricing)

### **Log Messages**

```bash
# Success
🤖 Using LLM agent search for Neo4j (intelligent graph traversal)
🕸️ NEO4J search returned 12 documents

# Fallback
⚠️ Neo4j agent search failed, falling back to basic search: [error]
🕸️ NEO4J search returned 8 documents

# Disabled
ℹ️ Using basic Neo4j search (set USE_NEO4J_AGENT_SEARCH=true for LLM agent)
```

---

## Architecture

### **Component Diagram**

```
┌─────────────────────────────────────────────────────────┐
│  search_core.py (Orchestrator)                          │
│  - Runs keyword + semantic (always)                     │
│  - LLM Router decides graph/spatial                     │
│  - Calls Neo4j search if graph=true                     │
└─────────────────────────────────────────────────────────┘
                        ↓
        ┌───────────────────────────────┐
        │  USE_NEO4J_AGENT_SEARCH?      │
        └───────────────────────────────┘
                ↙                    ↘
        YES (default)              NO
            ↓                          ↓
┌──────────────────────────┐  ┌──────────────────────────┐
│  search_agents.py        │  │  search_neo4j.py         │
│  get_neo4j_agent_results │  │  retrieve_neo4j          │
│                          │  │                          │
│  1. get_comprehensive_   │  │  1. _build_neo4j_        │
│     schema()             │  │     keyword_cypher()     │
│  2. _agent_generate_     │  │  2. _neo4j_run()         │
│     cypher()             │  │  3. _records_to_hits()   │
│  3. _neo4j_run()         │  │                          │
│  4. _rows_to_docs()      │  │  Simple text matching    │
│                          │  │  No relationship         │
│  Intelligent traversal   │  │  traversal               │
└──────────────────────────┘  └──────────────────────────┘
            ↓                          ↓
            └──────────┬───────────────┘
                       ↓
            ┌──────────────────────┐
            │  Neo4j Database      │
            │  - Nodes             │
            │  - Relationships     │
            │  - Properties        │
            └──────────────────────┘
```

---

## Next Steps

### **Immediate (Recommended)**

1. **Verify your Neo4j schema**
   ```cypher
   CALL db.labels() YIELD label RETURN label
   CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType
   ```

2. **Test with sample queries**
   - Try author queries: "publications by [author name]"
   - Try org queries: "datasets from [organization]"
   - Check logs to confirm agent search is active

3. **Monitor performance**
   - Check latency impact (~200-500ms per Neo4j search)
   - Verify fallback rate is low (<5%)

### **Short-term (Optional)**

4. **Add relationships to Neo4j** (if missing)
   ```cypher
   // Connect resources to authors
   MATCH (r:Resource), (p:Person)
   WHERE r.contributor = p.name
   CREATE (p)-[:AUTHORED_BY]->(r)
   
   // Connect resources to organizations
   MATCH (r:Resource), (o:Organization)
   WHERE r.affiliation = o.name
   CREATE (r)-[:AFFILIATED_WITH]->(o)
   ```

5. **Tune LLM prompt** (if needed)
   - Edit `search_agents.py` lines 440-460
   - Add domain-specific examples
   - Adjust scoring weights

### **Long-term (Future)**

6. **Cache common queries**
   - Store LLM-generated Cypher for frequent patterns
   - Reduce LLM calls and latency

7. **Add query analytics**
   - Track which relationship patterns are most used
   - Optimize schema based on usage patterns

---

## Troubleshooting

### **Agent Search Not Working**

**Symptom:** Logs show "Using basic Neo4j search"

**Solutions:**
1. Check `USE_NEO4J_AGENT_SEARCH=true` in .env
2. Verify `OPENAI_KEY` and `OPENAI_BASE_URL` are set
3. Check `search_agents.py` imports successfully

---

### **LLM Generation Failures**

**Symptom:** Logs show "Neo4j agent search failed, falling back"

**Solutions:**
1. Check LLM API is accessible
2. Verify model supports function calling
3. Check Neo4j schema is valid (run `get_comprehensive_schema()`)
4. Review LLM prompt in `search_agents.py` lines 440-460

---

### **Poor Result Quality**

**Symptom:** Agent search returns irrelevant results

**Solutions:**
1. Verify Neo4j has proper relationships (not just nodes)
2. Check relationship directions are correct
3. Add few-shot examples to LLM prompt
4. Tune scoring weights in generated Cypher

---

### **High Latency**

**Symptom:** Neo4j searches take >1 second

**Solutions:**
1. Reduce schema cache TTL (default 5 min)
2. Use faster LLM model (e.g., gpt-3.5-turbo)
3. Cache common query patterns
4. Optimize Neo4j indexes

---

## Summary

✅ **Implemented**: LLM agent search for intelligent Neo4j graph traversal  
✅ **Preserved**: Old basic search for reference and fallback  
✅ **Configurable**: Easy toggle via environment variable  
✅ **Future-Proof**: Automatically adapts to schema changes  
✅ **Reliable**: Graceful fallback on any failure  

**No breaking changes** - system continues to work exactly as before if agent search is disabled or fails.
