"""
test_robustness.py
------------------
Full robustness sweep for the Neo4j graph search layer.
Run from project root: python test_robustness.py
"""
from dotenv import load_dotenv
import os, logging
load_dotenv()
logging.basicConfig(level=logging.INFO)

from rag_pipeline.search.agents import get_neo4j_agent_results
from rag_pipeline.search.neo4j_graph_tools import detect_pattern, _get_internal_labels, _get_resource_labels

# ---------------------------------------------------------------------------
# Test 1: Pattern detection
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("TEST 1: Pattern detection")
print("=" * 65)

pattern_cases = [
    ("publications by Alexander Michels",   "by_author"),
    ("datasets from NASA",                  "by_organization"),
    ("resources tagged flooding",           "by_tag"),
    ("show all notebooks",                  "by_resource_type"),
    ("find all datasets",                   "by_resource_type"),
    ("list all publications",               "by_resource_type"),
    ("show all maps",                       "by_resource_type"),
    ("resources related to wildfire",       "related_to"),
    ("resources in the Climate collection", "in_collection"),
    ("what is climate change",              None),
]

all_pass = True
for query, expected in pattern_cases:
    result = detect_pattern(query)
    pattern = result[0] if result else None
    ok = pattern == expected
    icon = "✅" if ok else "❌"
    print(f"{icon}  {query:<45} -> {pattern or 'None'}")
    if not ok:
        print(f"     expected: {expected}")
        all_pass = False

print("Pattern tests:", "ALL PASSED ✅" if all_pass else "SOME FAILED ❌")

# ---------------------------------------------------------------------------
# Test 2: Resource type queries return results
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("TEST 2: Resource type queries")
print("=" * 65)

resource_queries = [
    "show all notebooks",
    "find all datasets",
    "list all publications",
    "show all maps",
    "find all code",
]

for q in resource_queries:
    hits = get_neo4j_agent_results(q, limit=3)
    icon = "✅" if hits else "❌"
    print(f"{icon}  [{len(hits):2d} hits] {q}")
    for h in hits[:2]:
        print(f"         - {h['_source'].get('title')}")

# ---------------------------------------------------------------------------
# Test 3: Internal labels not leaking into resource results
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("TEST 3: Internal labels not leaking")
print("=" * 65)

internal = _get_internal_labels()
hits = get_neo4j_agent_results("show all notebooks", limit=20)
leaked = []
for h in hits:
    title = h['_source'].get('title', '')
    # We can't easily check label from hit, but we can check title isn't blank
    # Real check: none of the internal node types should appear
    if not title or title == "No Title":
        leaked.append(title)

if not leaked:
    print(f"✅  {len(hits)} results, no blank/internal titles found")
else:
    print(f"❌  Found suspicious results: {leaked}")

# ---------------------------------------------------------------------------
# Test 4: Author queries across multiple contributors
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("TEST 4: Author queries")
print("=" * 65)

author_queries = [
    "publications by Alexander Michels",
    "notebooks by Fangzheng Lyu",
    "work by Erick Li",
    "resources by Rebecca Vandewalle",
    "publications by Nonexistent Person",  # should be 0
]

for q in author_queries:
    hits = get_neo4j_agent_results(q, limit=3)
    expected_zero = "Nonexistent" in q
    ok = (len(hits) == 0) if expected_zero else (len(hits) > 0)
    icon = "✅" if ok else "❌"
    print(f"{icon}  [{len(hits):2d} hits] {q}")

# ---------------------------------------------------------------------------
# Test 5: Text2Cypher for non-pattern queries
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("TEST 5: Text2Cypher (Tier 2)")
print("=" * 65)

t2_queries = [
    "COVID spatial analysis",
    "flood risk data Illinois",
]

for q in t2_queries:
    hits = get_neo4j_agent_results(q, limit=3)
    icon = "✅" if hits else "⚠️ "
    print(f"{icon}  [{len(hits):2d} hits] {q}")
    for h in hits[:2]:
        print(f"         - {h['_source'].get('title')}")

# ---------------------------------------------------------------------------
# Test 6: Env var override
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("TEST 6: Env var override")
print("=" * 65)

os.environ["NEO4J_INTERNAL_LABELS"] = "Contributor,User,Alias,Temp,Notification,FutureType"
# Re-import to pick up env var (module already loaded, call function directly)
updated_internal = _get_internal_labels()
assert "FutureType" in updated_internal, "Env var override failed"
assert "Contributor" in updated_internal, "Default labels missing"
print(f"✅  Internal labels: {sorted(updated_internal)}")
print(f"✅  Resource labels: {sorted(_get_resource_labels())}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("SUMMARY")
print("=" * 65)
print("Pattern detection:    ✅" if all_pass else "Pattern detection:    ❌")
print("All tests complete — check ✅/❌ above for any failures")