"""
test_spatial.py
---------------
Live test for the spatial search tool. Exercises three independent stages:

  TEST 0: Environment check
  TEST 1: spaCy NER — extract GPE/LOC entities from a natural-language query
  TEST 2: Google Maps geocoding — convert a location string to a bounding box
  TEST 3: OpenSearch geo_shape — run a bounded spatial query against the index
  TEST 4: End-to-end — get_spatial_search_results() stitches all three together
  TEST 5: LangChain tool wrapper — spatial_search_tool() returns JSON payload

Run from repo root:
    python tests/live/test_spatial.py

Env vars read (from repo-root .env):
    GOOGLE_MAPS_API_KEY   — required
    OPENSEARCH_NODE       — required
    OPENSEARCH_INDEX      — required
    OPENSEARCH_USERNAME   — optional
    OPENSEARCH_PASSWORD   — optional
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
logging.basicConfig(level=logging.INFO)

from rag_pipeline.search.spatial import (
    extract_locations_from_query,
    get_bounding_box,
    get_spatial_search_results,
    spatial_search,
)
from rag_pipeline.langchain_granular_tools import spatial_search_tool


def _banner(title: str) -> None:
    print("\n" + "=" * 65)
    print(title)
    print("=" * 65)


def test_env_check() -> bool:
    _banner("TEST 0: Environment check")
    required = ["GOOGLE_MAPS_API_KEY", "OPENSEARCH_NODE", "OPENSEARCH_INDEX"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"❌  Missing required env vars: {missing}")
        print("    Set them in .env and rerun.")
        return False
    print("✅  All required env vars present")
    return True


def test_ner() -> None:
    _banner("TEST 1: spaCy NER — extract locations from queries")
    cases = [
        ("flood risk in Chicago", ["Chicago"]),
        ("wildfires in California and Oregon", ["California", "Oregon"]),
        ("climate data for the Great Lakes region", []),  # fuzzy — spaCy may or may not catch
        ("what is a dataset",                  []),       # no location
    ]
    for query, expected_hint in cases:
        locations = extract_locations_from_query(query)
        print(f"  {query!r:50s} → {locations}")
    print("(Note: spaCy NER is heuristic — empty result for Test 3 is acceptable)")


def test_geocoding() -> None:
    _banner("TEST 2: Google Maps geocoding — location → bounding box")
    cases = ["Chicago", "California", "United States", "Denver, Colorado"]
    for location in cases:
        bbox = get_bounding_box(location)
        if bbox is None:
            print(f"❌  {location:30s} → no bbox returned")
            continue
        coords = bbox["coordinates"][0]
        lngs = [pt[0] for pt in coords]
        lats = [pt[1] for pt in coords]
        print(
            f"✅  {location:30s} → W={min(lngs):.3f} S={min(lats):.3f} "
            f"E={max(lngs):.3f} N={max(lats):.3f}"
        )


def test_opensearch_direct() -> None:
    _banner("TEST 3: OpenSearch geo_shape — direct coord query (bypass NLP)")
    # Chicago bounding box, approximately
    chicago_coords = [
        [-87.9401, 41.6445],
        [-87.5241, 41.6445],
        [-87.5241, 42.0230],
        [-87.9401, 42.0230],
        [-87.9401, 41.6445],
    ]
    hits = spatial_search(coords=chicago_coords, limit=5)
    if isinstance(hits, dict) and "error" in hits:
        print(f"❌  OpenSearch error: {hits['error']}")
        return
    print(f"✅  Returned {len(hits)} hits for Chicago bbox")
    for hit in hits[:3]:
        title = (hit.get("_source") or {}).get("title") or "<untitled>"
        score = hit.get("_score", 0)
        print(f"   - [{score:.2f}] {title[:70]}")


def test_end_to_end() -> None:
    _banner("TEST 4: End-to-end — get_spatial_search_results()")
    queries = [
        "datasets about Chicago",
        "flood research in Texas",
        "environmental data for California",
    ]
    for query in queries:
        hits = get_spatial_search_results(query, size=5)
        print(f"\n{query!r}")
        if not hits:
            print("   ⚠️   0 results (may be legitimate — no spatial matches or NLP miss)")
            continue
        print(f"   ✅  {len(hits)} hits")
        for hit in hits[:3]:
            title = (hit.get("_source") or {}).get("title") or "<untitled>"
            print(f"      - {title[:70]}")


def test_langchain_wrapper() -> None:
    _banner("TEST 5: LangChain tool wrapper — spatial_search_tool()")
    payload_str = spatial_search_tool("flood data for Chicago", limit=5)
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as exc:
        print(f"❌  Wrapper returned non-JSON payload: {exc}")
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
    test_ner()
    test_geocoding()
    test_opensearch_direct()
    test_end_to_end()
    test_langchain_wrapper()
    _banner("SUMMARY")
    print("All tests executed. Check ✅/❌ above for any failures.")


if __name__ == "__main__":
    main()
