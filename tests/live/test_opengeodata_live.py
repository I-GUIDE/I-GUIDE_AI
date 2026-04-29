"""
test_opengeodata_live.py
------------------------
Live test for the federated external-catalog search tool (OpenGeoData).

Covered backends:
  - STAC (SpatioTemporal Asset Catalog) — e.g. MS Planetary Computer
  - OGC API Records — standards-compliant record endpoints
  - CKAN — data.gov and similar open-data portals
  - NASA CMR (Common Metadata Repository)

  TEST 0: Environment check — LLM credentials present (needed for NL→bbox)
  TEST 1: NASA CMR direct — bypass NL layer, query CMR with a keyword
  TEST 2: End-to-end single query — get_opengeodata_results() full pipeline
  TEST 3: Multiple queries — verify results vary meaningfully by topic
  TEST 4: Result shape — every returned hit has expected fields
  TEST 5: LangChain wrapper — opengeodata_search_tool() returns valid JSON payload

Run from repo root:
    python tests/live/test_opengeodata_live.py

Env vars read (from repo-root .env):
    VLLM_PROXY (or OPENAI_API_BASE) — required for NL→bbox via LLM
    VLLM_API_KEY (or OPENAI_API_KEY) — required for LLM calls

Note: External catalog calls go to public APIs — no local service required.
      Results may be empty if a catalog is temporarily unavailable.
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

from rag_pipeline.search.opengeodata import get_opengeodata_results, search_cmr_collections
from rag_pipeline.langchain_granular_tools import opengeodata_search_tool


def _banner(title: str) -> None:
    print("\n" + "=" * 65)
    print(title)
    print("=" * 65)


def _llm_env_present() -> bool:
    for key in ("VLLM_PROXY", "OPENAI_API_BASE", "OPENAI_BASE_URL", "ANVILGPT_URL", "API_BASE"):
        if os.getenv(key):
            return True
    return False


def _llm_key_present() -> bool:
    for key in ("VLLM_API_KEY", "OPENAI_API_KEY", "OPENAI_KEY", "ANVILGPT_KEY", "API_KEY"):
        if os.getenv(key):
            return True
    return False


def test_env_check() -> bool:
    _banner("TEST 0: Environment check")
    base_ok = _llm_env_present()
    key_ok = _llm_key_present()
    if not base_ok:
        print("❌  Missing LLM base URL (VLLM_PROXY or OPENAI_API_BASE)")
    else:
        print("✅  LLM base URL present")
    if not key_ok:
        print("❌  Missing LLM API key (VLLM_API_KEY or OPENAI_API_KEY)")
    else:
        print("✅  LLM API key present")
    if not (base_ok and key_ok):
        print("    NL→bbox queries will fail without LLM credentials.")
        print("    NASA CMR test (TEST 1) may still work.")
    return base_ok and key_ok


def test_cmr_direct() -> None:
    _banner("TEST 1: NASA CMR direct keyword search")
    try:
        assets = search_cmr_collections(q="flood", limit=5)
    except Exception as exc:
        print(f"❌  CMR search raised: {exc}")
        return
    if not assets:
        print("⚠️   0 CMR assets returned — CMR may be unavailable or query too narrow")
        return
    print(f"✅  CMR returned {len(assets)} asset(s)")
    for asset in assets[:3]:
        title = asset.title if hasattr(asset, "title") else (asset.get("title") or "<untitled>")
        source = asset.source if hasattr(asset, "source") else (asset.get("source") or "?")
        print(f"   [{source}] {str(title)[:70]}")


def test_end_to_end() -> None:
    _banner("TEST 2: End-to-end — get_opengeodata_results()")
    query = "flood risk geospatial datasets"
    hits = get_opengeodata_results(query, limit=5)
    if not hits:
        print(f"⚠️   0 hits for {query!r}")
        print("    External catalogs may be unavailable or LLM bbox extraction failed.")
        return
    print(f"✅  {len(hits)} hits returned for {query!r}")
    first = hits[0]
    title = (first.get("_source") or {}).get("title") or "<untitled>"
    print(f"   Top hit: {title[:70]}")
    for hit in hits[:3]:
        t = (hit.get("_source") or {}).get("title") or "<untitled>"
        src = (hit.get("_source") or {}).get("source") or "?"
        print(f"   [{src}] {t[:65]}")


def test_multiple_queries() -> None:
    _banner("TEST 3: Multiple queries — result variation by topic")
    queries = [
        "satellite imagery wildfire California",
        "groundwater quality data",
        "urban heat island temperature",
    ]
    result_sets: list[set[str]] = []
    for query in queries:
        hits = get_opengeodata_results(query, limit=5)
        ids = {str((h.get("_source") or {}).get("doc_id") or h.get("_id") or "") for h in hits}
        result_sets.append(ids)
        print(f"   {query!r:50s} → {len(hits)} hits")

    if len(result_sets) >= 2:
        overlap = result_sets[0] & result_sets[1]
        if len(overlap) < min(len(result_sets[0]), len(result_sets[1])):
            print("✅  Different queries return different result sets (expected)")
        else:
            print("⚠️   All queries return identical results — catalog may be returning static data")


def test_result_shape() -> None:
    _banner("TEST 4: Result shape validation")
    hits = get_opengeodata_results("climate data", limit=8)
    if not hits:
        print("⚠️   0 hits — cannot validate shape")
        return

    required_keys = ("_id", "_score", "_source")
    source_keys = ("title",)
    problems: list[str] = []

    for i, hit in enumerate(hits):
        for key in required_keys:
            if key not in hit:
                problems.append(f"hit[{i}] missing {key!r}")
        src = hit.get("_source") or {}
        for key in source_keys:
            if key not in src:
                problems.append(f"hit[{i}]._source missing {key!r}")

    if problems:
        print(f"❌  Shape problems found:")
        for p in problems[:10]:
            print(f"      {p}")
    else:
        print(f"✅  All {len(hits)} hits have correct shape (_id, _score, _source.title)")


def test_langchain_wrapper() -> None:
    _banner("TEST 5: LangChain wrapper — opengeodata_search_tool()")
    payload_str = opengeodata_search_tool("flood geospatial data", limit=5)
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
    env_ok = test_env_check()
    test_cmr_direct()  # CMR doesn't need LLM credentials
    if not env_ok:
        print("\n⚠️   LLM credentials missing — skipping NL-dependent tests (TEST 2-5).")
        _banner("SUMMARY")
        print("Partial run. Fix LLM env vars for full coverage.")
        return
    test_end_to_end()
    test_multiple_queries()
    test_result_shape()
    test_langchain_wrapper()
    _banner("SUMMARY")
    print("All tests executed. Check ✅/❌/⚠️ above for failures.")


if __name__ == "__main__":
    main()
