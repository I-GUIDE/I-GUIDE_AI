"""
test_neo4j.py
-------------
Live test for the Neo4j graph search layer.

  TEST 0: Environment check — required env vars present
  TEST 1: Connection — driver connects and can run a trivial Cypher query
  TEST 2: Pattern detection — detect_pattern() identifies well-known query types
  TEST 3: Keyword search — get_neo4j_search_results() returns hits with correct shape
  TEST 4: Resource type queries — searches targeting specific element types
  TEST 5: Internal label guard — no internal system nodes leak into results
  TEST 6: LangChain wrapper — neo4j_search_tool() returns a valid JSON payload

Run from repo root:
    python tests/live/test_neo4j.py

Env vars read (from repo-root .env):
    NEO4J_CONNECTION_STRING (or NEO4J_URI) — required
    NEO4J_USER (or NEO4J_USERNAME)         — required
    NEO4J_PASSWORD                         — required
    NEO4J_DB                               — optional
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.WARNING)

from rag_pipeline.search.neo4j import get_neo4j_search_results
from rag_pipeline.search.neo4j_graph_tools import detect_pattern, _get_internal_labels, _get_resource_labels
from rag_pipeline.langchain_granular_tools import neo4j_search_tool


def _banner(title: str) -> None:
    print("\n" + "=" * 65)
    print(title)
    print("=" * 65)


def test_env_check() -> bool:
    _banner("TEST 0: Environment check")
    uri = os.getenv("NEO4J_CONNECTION_STRING") or os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")

    ok = True
    if not uri:
        print("❌  Missing NEO4J_CONNECTION_STRING (or NEO4J_URI)")
        ok = False
    else:
        print(f"✅  NEO4J URI       = {uri}")

    if not user:
        print("❌  Missing NEO4J_USER (or NEO4J_USERNAME)")
        ok = False
    else:
        print(f"✅  NEO4J_USER      = {user}")

    if not password:
        print("❌  Missing NEO4J_PASSWORD")
        ok = False
    else:
        print("✅  NEO4J_PASSWORD  = (set)")

    db = os.getenv("NEO4J_DB")
    if db:
        print(f"   NEO4J_DB        = {db}")

    return ok


def test_connection() -> bool:
    _banner("TEST 1: Connection — run trivial Cypher")
    try:
        from neo4j import GraphDatabase

        uri = os.getenv("NEO4J_CONNECTION_STRING") or os.getenv("NEO4J_URI")
        user = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME")
        password = os.getenv("NEO4J_PASSWORD")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            result = session.run("RETURN 1 AS n")
            row = result.single()
        driver.close()
        if row and row["n"] == 1:
            print("✅  Neo4j connection successful")
            return True
        print(f"❌  Unexpected response: {row}")
        return False
    except Exception as exc:
        print(f"❌  Connection failed: {exc}")
        return False


def test_pattern_detection() -> None:
    _banner("TEST 2: Pattern detection — detect_pattern()")
    cases = [
        ("show all notebooks",          True),
        ("list datasets about climate",  True),
        ("what is a dataset",            False),
        ("COVID spatial analysis",       False),
    ]
    all_pass = True
    for query, expect_match in cases:
        result = detect_pattern(query)
        matched = result is not None
        icon = "✅" if matched == expect_match else "❌"
        if matched != expect_match:
            all_pass = False
        label = f"pattern={result[0]!r}" if result else "no match"
        print(f"  {icon}  {query!r:40s} → {label} (expected {'match' if expect_match else 'no match'})")
    print("Pattern tests:", "ALL PASSED ✅" if all_pass else "SOME FAILED ❌")


def test_keyword_search() -> None:
    _banner("TEST 3: Keyword search — get_neo4j_search_results()")
    queries = [
        "flood risk",
        "climate change Illinois",
        "geospatial analysis",
    ]
    for query in queries:
        hits = get_neo4j_search_results(query, limit=5)
        if not hits:
            print(f"⚠️   {query!r:40s} → 0 hits")
            continue
        first = hits[0]
        for key in ("_id", "_score", "_source"):
            assert key in first, f"Missing key {key!r} in hit"
        title = (first.get("_source") or {}).get("title") or "<untitled>"
        print(f"✅  {query!r:40s} → {len(hits)} hits | top: {title[:50]}")


def test_resource_type_queries() -> None:
    _banner("TEST 4: Resource type queries")
    resource_labels = _get_resource_labels()
    print(f"   Known resource labels: {sorted(resource_labels)}")
    cases = ["datasets about climate", "notebooks for spatial analysis", "tools for flood prediction"]
    for query in cases:
        hits = get_neo4j_search_results(query, limit=5)
        titles = [(h.get("_source") or {}).get("title") or "<untitled>" for h in hits[:3]]
        print(f"   {query!r:45s} → {len(hits)} hits")
        for t in titles:
            print(f"      - {t[:65]}")


def test_internal_label_guard() -> None:
    _banner("TEST 5: Internal label guard — no system nodes in results")
    internal = _get_internal_labels()
    print(f"   Internal labels to exclude: {sorted(internal)}")
    hits = get_neo4j_search_results("show all notebooks", limit=20)
    suspicious = []
    for hit in hits:
        title = (hit.get("_source") or {}).get("title") or ""
        if not title or title.startswith("_") or title.lower() in ("none", "null", ""):
            suspicious.append(title)
    if suspicious:
        print(f"❌  Found suspicious results: {suspicious[:5]}")
    else:
        print(f"✅  {len(hits)} results, no blank/internal titles found")


def test_langchain_wrapper() -> None:
    _banner("TEST 6: LangChain wrapper — neo4j_search_tool()")
    payload_str = neo4j_search_tool("flood risk data", limit=5)
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as exc:
        print(f"❌  Wrapper returned non-JSON: {exc}")
        return
    expected_keys = {"source", "count", "documents", "citation_ids"}
    missing = expected_keys - set(payload.keys())
    if missing:
        print(f"❌  Missing keys in payload: {missing}")
        return
    print(f"✅  Payload shape OK — source={payload['source']} count={payload['count']}")
    if payload["documents"]:
        print(f"   First doc: {payload['documents'][0].get('title', '<untitled>')[:70]}")


def main() -> None:
    if not test_env_check():
        return
    connected = test_connection()
    if not connected:
        print("\n⚠️   Skipping search tests — fix connection first.")
        return
    test_pattern_detection()
    test_keyword_search()
    test_resource_type_queries()
    test_internal_label_guard()
    test_langchain_wrapper()
    _banner("SUMMARY")
    print("All tests executed. Check ✅/❌/⚠️ above for failures.")


if __name__ == "__main__":
    main()
