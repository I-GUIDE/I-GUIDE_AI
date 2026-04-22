# Neo4j Agent Search - Quick Start Guide

**TL;DR:** Your Neo4j search is now intelligent and future-proof. No action needed unless you want to customize.

---

## What Just Changed

✅ Neo4j now uses **LLM-powered graph traversal** instead of basic text matching  
✅ Automatically discovers your schema and adapts to changes  
✅ Old implementation kept for reference/rollback  
✅ Enabled by default with automatic fallback  

---

## Quick Test

### 1. Check if it's working

Look for this in your logs when a Neo4j search runs:
```
🤖 Using LLM agent search for Neo4j (intelligent graph traversal)
```

### 2. Try these queries

```bash
# Author queries (if you have AUTHORED_BY relationships)
"publications by John Smith"

# Organization queries (if you have AFFILIATED_WITH relationships)
"datasets from NASA"

# Tag queries (if you have TAGGED_WITH relationships)
"resources tagged climate"
```

---

## Configuration

### Enable/Disable Agent Search

**In your `.env` file:**

```bash
# Enable (default, recommended)
USE_NEO4J_AGENT_SEARCH=true

# Disable (revert to old basic search)
USE_NEO4J_AGENT_SEARCH=false
```

### Required Environment Variables

```bash
# For LLM agent search
OPENAI_KEY=your-api-key
OPENAI_BASE_URL=https://your-anvilgpt-url/api/chat/completions

# For Neo4j (required for both methods)
NEO4J_CONNECTION_STRING=bolt://your-neo4j-host:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

---

## Verify Your Schema

Run this in Neo4j Browser to see what the agent will discover:

```cypher
// Check labels
CALL db.labels() YIELD label RETURN label

// Check relationships
CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType

// Check sample data
MATCH (n) RETURN n LIMIT 5
```

---

## Rollback (If Needed)

### Quick rollback via environment variable:
```bash
USE_NEO4J_AGENT_SEARCH=false
```

### Full rollback via git:
```bash
git diff HEAD~1 rag_pipeline/search_core.py
git checkout HEAD~1 -- rag_pipeline/search_core.py
```

---

## Files Changed

| File | Status | Purpose |
|------|--------|---------|
| `rag_pipeline/search_core.py` | ✏️ Modified | Added agent search with fallback |
| `rag_pipeline/search_neo4j.py` | ✅ Preserved | Old implementation (still available) |
| `rag_pipeline/search_agents.py` | ✅ Used | Agent search implementation (already existed) |
| `.env.example` | ✏️ Modified | Added `USE_NEO4J_AGENT_SEARCH` flag |
| `NEO4J_AGENT_SEARCH_UPGRADE.md` | ✨ New | Full documentation |
| `NEO4J_QUICK_START.md` | ✨ New | This file |

---

## Benefits

| Query Type | Old | New |
|------------|-----|-----|
| "publications by John Smith" | ❌ Text match only | ✅ Follows AUTHORED_BY |
| "datasets from NASA" | ❌ Text match only | ✅ Follows AFFILIATED_WITH |
| "resources tagged climate" | ⚠️ Array contains | ✅ Follows TAGGED_WITH |
| Future schema changes | ❌ Breaks | ✅ Auto-adapts |

---

## Troubleshooting

### Not seeing agent search logs?

1. Check `USE_NEO4J_AGENT_SEARCH=true` in .env
2. Verify `OPENAI_KEY` is set
3. Restart your server

### Getting fallback messages?

```
⚠️ Neo4j agent search failed, falling back to basic search
```

This is **normal** - the system continues working with basic search. Check:
1. LLM API is accessible
2. Neo4j connection is working
3. Schema discovery succeeds

### Want to disable it?

```bash
USE_NEO4J_AGENT_SEARCH=false
```

---

## Next Steps

1. ✅ **Done** - Agent search is active
2. **Optional**: Test with your queries
3. **Optional**: Verify your Neo4j schema has relationships
4. **Optional**: Monitor performance and result quality

---

## Questions?

- **Full docs**: See `NEO4J_AGENT_SEARCH_UPGRADE.md`
- **Implementation**: See `rag_pipeline/search_core.py` lines 124-162
- **Agent logic**: See `rag_pipeline/search_agents.py` lines 405-609

---

**Everything is backward compatible. Your system works exactly as before if agent search is disabled or fails.**
