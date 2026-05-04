"""
search_agents.py
----------------
LLM-powered search agents for the I-GUIDE RAG pipeline.

Neo4j search hierarchy (most reliable → most flexible):
  1. Prewritten tools  (neo4j_graph_tools)  — deterministic, no LLM, fast
  2. Text2Cypher       (_text2cypher)        — LLM-generated, flexible
  3. Basic keyword     (search_neo4j)        — fallback, always works

OpenSearch agent search is a separate path used for spatial/temporal queries.

Public API (mirrors previous version, drop-in replacement):
  get_neo4j_agent_results(query, limit)   → List[hit-dicts]
  get_opensearch_agent_results(query, limit) → List[hit-dicts]
  run_agent_search(state, ...)            → List[EvidenceEntry]
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from functools import lru_cache, wraps
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

import requests
from neo4j import Driver, GraphDatabase
from neo4j.graph import Node as _Neo4jNode
from opensearchpy import OpenSearch

from .neo4j_graph_tools import (
    build_tool_query,
    detect_pattern,
    run_user_author_fallback,
    _get_internal_labels,
)
from ..state import EvidenceEntry, ensure_state_shapes, get_query_text, merge_retrieval

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("search_agents")


# ---------------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------------

def _getenv(name: str, required: bool = True, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Missing required environment variable: {name}")
    if value and len(value) >= 2 and value[0] == value[-1] in ('"', "'"):
        value = value[1:-1]
    return value or ""


def _retry(times: int = 3, base_delay: float = 0.25, exc: Tuple = (Exception,)):
    def decorator(fn):
        @wraps(fn)
        def _inner(*args, **kwargs):
            last_exc = None
            for attempt in range(times):
                try:
                    return fn(*args, **kwargs)
                except exc as err:
                    last_exc = err
                    if attempt < times - 1:
                        time.sleep(base_delay * (2 ** attempt))
            raise last_exc
        return _inner
    return decorator


def _safe_score(val: Any, default: float = 1.0) -> float:
    try:
        score = float(val)
        return score if math.isfinite(score) else default
    except Exception:
        return default


def _normalize_source_fields(src: Dict[str, Any], hit_id: str) -> Dict[str, Any]:
    if not isinstance(src, dict):
        src = {}
    src = dict(src)
    if "element_type" not in src and "resource-type" in src:
        src["element_type"] = src["resource-type"]
    src.setdefault("doc_id", hit_id)
    src.setdefault("title", src.get("name") or "No Title")
    src.setdefault("contents", src.get("abstract") or src.get("description") or "No Content")
    return src


def _transform_thumbnail(value: Any) -> Any:
    return value  # placeholder for utils.generateMultipleResolutionImagesFor


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

def _openai_endpoint(path: str) -> str:
    base = os.getenv("ANVILGPT_URL", "https://api.openai.com/v1").rstrip("/")
    # If URL already ends with the full path (e.g. /api/chat/completions), return as-is
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/{path.lstrip('/')}"


def _llm_chat(
    messages: List[Dict[str, str]],
    max_tokens: int = 512,
    temperature: float = 0.0,
) -> str:
    url = _openai_endpoint("chat/completions")
    api_key = _getenv("ANVILGPT_KEY")
    model = (os.getenv("ANVILGPT_MODEL") or "gpt-4o-mini").strip()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=payload, timeout=45)
    response.raise_for_status()
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as err:
        raise ValueError(f"Unexpected LLM response payload: {data}") from err


# ---------------------------------------------------------------------------
# Neo4j connectivity
# ---------------------------------------------------------------------------

_SCHEMA_CACHE: Dict[str, Any] = {"ts": 0.0, "val": ""}
_SCHEMA_TTL_SEC = float(os.getenv("SCHEMA_CACHE_TTL_SEC", "300"))


@lru_cache(maxsize=1)
def _neo4j_driver() -> Driver:
    uri = (
        os.getenv("NEO4J_CONNECTION_STRING")
        or os.getenv("NEO4J_URI")
        or _getenv("NEO4J_CONNECTION_STRING")
    )
    user = (
        os.getenv("NEO4J_USER")
        or os.getenv("NEO4J_USERNAME")
        or _getenv("NEO4J_USER")
    )
    password = _getenv("NEO4J_PASSWORD")
    return GraphDatabase.driver(uri, auth=(user, password), max_connection_lifetime=300)


def _neo4j_db() -> Optional[str]:
    value = os.getenv("NEO4J_DB", "").strip()
    return value or None


def _neo4j_run(cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    database = _neo4j_db()
    driver = _neo4j_driver()
    with (driver.session(database=database) if database else driver.session()) as session:
        return list(session.run(cypher, **params))


def _extract_node_from_record(rec: Dict[str, Any]) -> Optional[_Neo4jNode]:
    node = rec.get("node")
    if isinstance(node, _Neo4jNode):
        return node
    for value in rec.values():
        if isinstance(value, _Neo4jNode):
            return value
    return None


def _graph_context_prefix(record: Dict[str, Any]) -> str:
    """
    Build a human-readable prefix from relationship columns returned by pattern Cypher queries.
    These fields are not node properties — they come from the graph traversal itself
    (e.g. matched_author from CONTRIBUTED, collection_name from BELONGS_TO).
    Injecting them into contents lets the reranker use graph context it wouldn't otherwise see.
    """
    parts: List[str] = []
    if record.get("matched_author"):
        parts.append(f"Author: {record['matched_author']}")
    if record.get("matched_org"):
        parts.append(f"Organization: {record['matched_org']}")
    if record.get("seed_title"):
        parts.append(f"Related to: {record['seed_title']}")
    if record.get("collection_name"):
        parts.append(f"Collection: {record['collection_name']}")
    return "; ".join(parts)


def _rows_to_hits(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert raw Neo4j records to the standard hit format."""
    hits: List[Dict[str, Any]] = []
    for idx, record in enumerate(rows):
        node = _extract_node_from_record(record)
        score = _safe_score(record.get("score", 1.0))

        if node is not None:
            props = dict(node)
            ref_id = props.get("_id", getattr(node, "element_id", f"node:{idx}"))
            node_labels = set(getattr(node, "labels", set())) - _get_internal_labels()
        else:
            props = {k: v for k, v in record.items() if isinstance(v, (str, int, float, list, dict))}
            ref_id = props.get("doc_id", f"row:{idx}")
            node_labels = set()

        source = _normalize_source_fields(props, str(ref_id))
        doc_id = str(source.get("doc_id", ref_id))

        resource_type = (
            source.get("resource-type")
            or source.get("element_type")
            or (next(iter(node_labels)).lower() if node_labels else None)
        )

        prefix = _graph_context_prefix(record)
        base_contents = source.get("contents") or ""
        contents = f"[{prefix}] {base_contents}".strip() if prefix else base_contents or None

        hits.append({
            "_id": doc_id,
            "_score": score,
            "_source": {
                "contributor":     source.get("contributor"),
                "contents":        contents,
                "resource-type":   resource_type,
                "title":           source.get("title"),
                "authors":         _as_list(source.get("authors")),
                "tags":            _as_list(source.get("tags")),
                "thumbnail-image": _transform_thumbnail(source.get("thumbnail-image", source.get("thumbnail_image"))),
                "click_count":     source.get("click_count", 0),
            },
        })
    return hits


def _as_list(val: Any) -> List[Any]:
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


# ---------------------------------------------------------------------------
# Neo4j schema discovery
# ---------------------------------------------------------------------------

def get_comprehensive_schema() -> str:
    """
    Return a compact schema string (labels, relationships, sample properties).
    Cached for SCHEMA_TTL_SEC seconds.
    """
    now = time.time()
    cached = _SCHEMA_CACHE.get("val")
    if cached and (now - _SCHEMA_CACHE.get("ts", 0.0)) < _SCHEMA_TTL_SEC:
        return cached

    parts: List[str] = []

    rows = _neo4j_run("CALL db.labels() YIELD label RETURN label", {})
    labels = [r["label"] for r in rows]
    parts.append(f"Labels: {', '.join(labels) if labels else '(none)'}")

    rows = _neo4j_run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType", {})
    rels = [r["relationshipType"] for r in rows]
    parts.append(f"Relationships: {', '.join(rels) if rels else '(none)'}")

    for label in labels[:8]:
        keys_rows = _neo4j_run(
            f"MATCH (n:`{label}`) WITH n LIMIT 5 RETURN keys(n) AS k", {}
        )
        keys = sorted({key for row in keys_rows for key in row["k"]})
        parts.append(f"Properties[{label}]: {', '.join(keys) if keys else '(none)'}")

        # Sample values for the most identity-like properties — helps LLM ground queries
        for prop in ("name", "display_first_name", "display_last_name", "organization", "affiliation"):
            if prop in keys:
                sample_rows = _neo4j_run(
                    f"MATCH (n:`{label}`) WHERE n.`{prop}` IS NOT NULL "
                    f"RETURN DISTINCT n.`{prop}` AS v LIMIT 3",
                    {},
                )
                samples = [str(r["v"]) for r in sample_rows if r["v"]]
                if samples:
                    parts.append(f"  Sample {label}.{prop}: {', '.join(samples)}")

    snapshot = "\n".join(parts)
    _SCHEMA_CACHE.update({"ts": now, "val": snapshot})
    return snapshot


# ---------------------------------------------------------------------------
# Text2Cypher (LLM fallback)
# ---------------------------------------------------------------------------

# Few-shot examples covering the main relationship patterns in the I-GUIDE graph.
# These are injected into the generation prompt to steer the LLM toward
# correct traversal patterns without overfitting to specific values.
_FEW_SHOT_EXAMPLES = """
Q: publications by Jane Doe
A: {"cypher": "MATCH (u:User)-[:CONTRIBUTED]->(r) WHERE toLower(coalesce(u.display_first_name,'') + ' ' + coalesce(u.display_last_name,'')) CONTAINS toLower($q) RETURN r AS node, coalesce(log10(toFloat(coalesce(r.click_count,0))+1),0) AS score ORDER BY score DESC LIMIT $limit", "params": {"q": "Jane Doe", "limit": 12}}

Q: datasets contributed by Smit Vasani
A: {"cypher": "MATCH (c:Contributor)-[:CONTRIBUTED]->(r) WHERE toLower(coalesce(c.display_first_name,'') + ' ' + coalesce(c.display_last_name,'')) CONTAINS toLower($q) RETURN r AS node, coalesce(log10(toFloat(coalesce(r.click_count,0))+1),0) AS score ORDER BY score DESC LIMIT $limit", "params": {"q": "Smit Vasani", "limit": 12}}

Q: resources tagged flooding
A: {"cypher": "MATCH (r) WHERE any(tag IN coalesce(r.tags,[]) WHERE toLower(tag) CONTAINS toLower($q)) RETURN r AS node, 1.5 + coalesce(log10(toFloat(coalesce(r.click_count,0))+1),0) AS score ORDER BY score DESC LIMIT $limit", "params": {"q": "flooding", "limit": 12}}

Q: notebooks related to wildfire risk
A: {"cypher": "MATCH (seed)-[:RELATED]-(r) WHERE toLower(coalesce(seed.title,'')) CONTAINS toLower($q) RETURN r AS node, coalesce(log10(toFloat(coalesce(r.click_count,0))+1),0) AS score ORDER BY score DESC LIMIT $limit", "params": {"q": "wildfire risk", "limit": 12}}

Q: resources in the Climate collection
A: {"cypher": "MATCH (r)-[:BELONGS_TO]->(col:Collection) WHERE toLower(coalesce(col.title, col.name,'')) CONTAINS toLower($q) RETURN r AS node, coalesce(log10(toFloat(coalesce(r.click_count,0))+1),0) AS score ORDER BY score DESC LIMIT $limit", "params": {"q": "Climate", "limit": 12}}
"""

_CYPHER_SYSTEM_PROMPT = (
    "You translate natural language into READ-ONLY Neo4j Cypher. "
    "Return ONLY a JSON object with keys 'cypher' (string) and 'params' (object). "
    "Never use MERGE, CREATE, DELETE, SET, REMOVE, DETACH, or CALL dbms. "
    "Always LIMIT results with $limit. Use $q as the main search parameter."
)

_FORBIDDEN = re.compile(
    r"\b(merge|create|delete|detach\s+delete|set|remove|"
    r"load\s+csv|call\s+dbms|apoc\.periodic\.|apoc\.load)\b",
    re.I,
)


def _sanitize_cypher(cypher: str) -> str:
    if _FORBIDDEN.search(cypher):
        raise ValueError(f"Unsafe Cypher detected: {cypher[:120]}")
    if not re.search(r"\b(match|call\s+db\.)\b", cypher, re.I):
        raise ValueError("Cypher must contain MATCH or a db.* index call")
    # Inject LIMIT if the LLM forgot it
    if "limit" not in cypher.lower():
        cypher = cypher.rstrip().rstrip(";") + " LIMIT $limit"
        log.debug("Injected missing LIMIT into generated Cypher.")
    return cypher


def _text2cypher(user_query: str, schema: str, limit: int) -> Tuple[str, Dict[str, Any]]:
    """
    Generate a Cypher query from natural language using the LLM.
    Returns (cypher, params).
    """
    user_prompt = (
        f"Schema:\n{schema}\n\n"
        f"Few-shot examples:\n{_FEW_SHOT_EXAMPLES}\n\n"
        f"Task: Write a single read-only Cypher query to answer:\n\"{user_query}\"\n\n"
        f"Constraints:\n"
        f"- Use only labels and properties present in the schema above.\n"
        f"- Do NOT use label union syntax like (r:A|B|C) — use a plain MATCH (r) instead.\n"
        f"- Return resource nodes as 'node' and a numeric 'score'.\n"
        f"- Use $q for the search value and $limit for the result limit.\n"
        f"- $q must be the single core topic keyword only (e.g. 'covid', 'flood', 'spatial') "
        f"— NOT the full query string. Strip filler words like 'data', 'datasets', 'resources'.\n"
        f"- Use case-insensitive matching: toLower(n.prop) CONTAINS toLower($q).\n"
        f"- Search BOTH r.title and r.tags so partial matches like 'covid-19' are found.\n"
        f"- Combine text relevance with log10(coalesce(r.click_count,0)+1) popularity.\n"
        f"- Output JSON only: {{\"cypher\": \"...\", \"params\": {{\"q\": \"...\", \"limit\": {limit}}}}}"
    )

    content = _llm_chat(
        [
            {"role": "system", "content": _CYPHER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=512,
        temperature=0.0,
    ).strip()

    start, end = content.find("{"), content.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"LLM returned non-JSON: {content[:200]}")

    obj = json.loads(content[start:end])
    cypher = _sanitize_cypher(obj.get("cypher", ""))

    params: Dict[str, Any] = obj.get("params") or {}
    params["q"] = params.get("q") or user_query
    params["limit"] = max(1, min(int(params.get("limit", limit)), 100))

    return cypher, params


@_retry(times=2, base_delay=0.5)
def _run_text2cypher(user_query: str, schema: str, limit: int) -> List[Dict[str, Any]]:
    cypher, params = _text2cypher(user_query, schema, limit)
    log.debug("Text2Cypher generated:\n%s\nparams=%s", cypher, params)
    return _neo4j_run(cypher, params)


# ---------------------------------------------------------------------------
# Main Neo4j dispatcher
# ---------------------------------------------------------------------------

def get_neo4j_agent_results(user_query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """
    Intelligent Neo4j search using a 3-tier fallback hierarchy:

      1. Prewritten pattern tools  — deterministic, no LLM
      2. Text2Cypher               — LLM-generated Cypher
      3. Basic keyword search      — search_neo4j.get_neo4j_search_results

    Returns hits in the standard _source shape used throughout the pipeline.
    """
    query = (user_query or "").strip()
    if not query:
        return []

    # ── Tier 1: prewritten pattern tools ─────────────────────────────────
    pattern_result = detect_pattern(query)
    if pattern_result is not None:
        pattern_name, captured = pattern_result
        tool_query = build_tool_query(pattern_name, captured, limit)

        if tool_query is not None:
            cypher, params = tool_query
            try:
                rows = _neo4j_run(cypher, params)
                hits = _rows_to_hits(rows)

                # Contributor returned nothing — try User nodes as secondary
                if not hits and pattern_name == "by_author":
                    log.debug("Contributor author lookup empty, trying User fallback.")
                    rows = run_user_author_fallback(captured, limit, _neo4j_run)
                    hits = _rows_to_hits(rows)

                if hits:
                    log.info(
                        "Pattern tool '%s' returned %d results for query: %s",
                        pattern_name, len(hits), query,
                    )
                    return hits

                log.debug("Pattern tool '%s' returned 0 results; escalating.", pattern_name)

            except Exception as exc:
                log.warning("Pattern tool '%s' failed (%s); escalating to Text2Cypher.", pattern_name, exc)

    # ── Tier 2: Text2Cypher ───────────────────────────────────────────────
    use_text2cypher = os.getenv("USE_TEXT2CYPHER", "true").lower() in ("true", "1", "yes")

    if use_text2cypher:
        try:
            schema = get_comprehensive_schema()
            rows = _run_text2cypher(query, schema, limit)
            hits = _rows_to_hits(rows)
            if hits:
                log.info("Text2Cypher returned %d results for query: %s", len(hits), query)
                return hits
            log.debug("Text2Cypher returned 0 results; escalating to keyword fallback.")
        except Exception as exc:
            log.warning("Text2Cypher failed (%s); falling back to keyword search.", exc)

    # ── Tier 3: basic keyword fallback ────────────────────────────────────
    log.info("Using basic Neo4j keyword fallback for query: %s", query)
    from rag_pipeline.search.neo4j import get_neo4j_search_results
    return get_neo4j_search_results(query, limit=limit)


# ---------------------------------------------------------------------------
# OpenSearch agent (spatial/temporal LLM queries) — unchanged from original
# ---------------------------------------------------------------------------

_OS_SCHEMA_CACHE: Dict[str, Any] = {"ts": 0.0, "val": ""}
_OS_FORBIDDEN_KEYS = {"delete", "update", "script", "bulk", "reindex", "indices"}


@lru_cache(maxsize=1)
def _os_client() -> OpenSearch:
    node = _getenv("OPENSEARCH_NODE")
    user = os.getenv("OPENSEARCH_USERNAME", "")
    pwd = os.getenv("OPENSEARCH_PASSWORD", "")
    return OpenSearch(
        hosts=[node],
        http_auth=(user, pwd) if (user or pwd) else None,
        use_ssl=node.lower().startswith("https"),
        verify_certs=False,
        ssl_assert_hostname=False,
        ssl_show_warn=False,
        timeout=30,
        max_retries=2,
        retry_on_timeout=True,
    )


def _os_index() -> str:
    index = _getenv("OPENSEARCH_INDEX")
    if not index:
        raise RuntimeError("OPENSEARCH_INDEX must not be empty")
    return index


@_retry()
def _os_search(body: Dict[str, Any]) -> Dict[str, Any]:
    response = _os_client().search(index=_os_index(), body=body)
    return response if isinstance(response, dict) else response.body


def _describe_properties(props: Dict[str, Any], prefix: str = "") -> List[str]:
    lines: List[str] = []
    for name, spec in sorted(props.items()):
        full_name = f"{prefix}{name}"
        dtype = spec.get("type", "object")
        lines.append(f"{full_name}: {dtype}")
        nested = spec.get("properties")
        if isinstance(nested, dict):
            lines.extend(_describe_properties(nested, f"{full_name}."))
    return lines


def get_opensearch_schema() -> str:
    now = time.time()
    cached = _OS_SCHEMA_CACHE.get("val")
    if cached and (now - _OS_SCHEMA_CACHE.get("ts", 0.0)) < _SCHEMA_TTL_SEC:
        return cached
    try:
        mapping = _os_client().indices.get_mapping(index=_os_index())
    except Exception as exc:
        log.error("Failed to fetch OpenSearch mapping: %s", exc)
        return ""
    props = (
        mapping.get(_os_index(), {})
        .get("mappings", {})
        .get("properties", {})
    )
    if not isinstance(props, dict) or not props:
        return ""
    snapshot = "\n".join(_describe_properties(props)[:200])
    _OS_SCHEMA_CACHE.update({"ts": now, "val": snapshot})
    return snapshot


def _sanitize_opensearch_body(body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("Agent body must be a JSON object.")

    def _check(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key.lower() in _OS_FORBIDDEN_KEYS:
                    raise ValueError(f"Forbidden key in generated body: {key}")
                _check(value)
        elif isinstance(obj, list):
            for item in obj:
                _check(item)

    _check(body)
    size = body.get("size")
    body["size"] = 12 if size is None else max(1, min(int(size), 100))
    return body


def _agent_generate_opensearch_body(user_query: str, schema: str, limit: int) -> Dict[str, Any]:
    system = (
        "You translate natural language search questions into OpenSearch DSL JSON. "
        "Return ONLY JSON for a search body. Never perform destructive operations."
    )
    user_prompt = (
        f"OpenSearch field inventory:\n{schema or '(unknown)'}\n\n"
        f"Task: Create an OpenSearch search body (JSON) that answers:\n\"{user_query}\"\n\n"
        f"Constraints:\n"
        f"- Use only read-only APIs (query, sort, aggs).\n"
        f"- Limit results with \"size\": {limit}.\n"
        f"- Prefer geo filters for spatial hints.\n"
        f"- Prefer date range filters for temporal hints.\n"
        f"- Output JSON only."
    )
    content = _llm_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
        max_tokens=400,
        temperature=0.0,
    ).strip()
    start, end = content.find("{"), content.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"Agent returned non-JSON content: {content[:200]}")
    body = json.loads(content[start:end])
    body.setdefault("size", limit)
    return _sanitize_opensearch_body(body)


def get_opensearch_agent_results(user_query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """
    LLM-powered OpenSearch query for spatial/temporal queries.
    Falls back to keyword search on failure.
    """
    query = (user_query or "").strip()
    if not query:
        return []
    try:
        schema = get_opensearch_schema()
        body = _agent_generate_opensearch_body(query, schema, limit)
        response = _os_client().search(index=_os_index(), body=body)
        raw = response if isinstance(response, dict) else response.body
        hits_raw = raw.get("hits", {}).get("hits", []) or []
    except Exception as exc:
        log.error("OpenSearch agent query failed (%s); falling back to keyword search.", exc)
        from rag_pipeline.search.keyword import get_keyword_search_results
        return get_keyword_search_results(query, size=limit)

    hits: List[Dict[str, Any]] = []
    for hit in hits_raw:
        doc_id = str(hit.get("_id") or "")
        if not doc_id:
            continue
        source = hit.get("_source", {}) or {}
        source.pop("contents-embedding", None)
        source.pop("pdf_chunks", None)
        if "thumbnail-image" in source:
            source["thumbnail-image"] = _transform_thumbnail(source["thumbnail-image"])
        hits.append({
            "_id": doc_id,
            "_score": _safe_score(hit.get("_score", 1.0)),
            "_source": {
                "contributor":     source.get("contributor"),
                "contents":        source.get("contents"),
                "resource-type":   source.get("resource-type"),
                "title":           source.get("title"),
                "authors":         _as_list(source.get("authors")),
                "tags":            _as_list(source.get("tags")),
                "thumbnail-image": source.get("thumbnail-image"),
                "click_count":     source.get("click_count", 0),
            },
        })
    return hits


# ---------------------------------------------------------------------------
# State-based entry point (used by search_core.py)
# ---------------------------------------------------------------------------

def run_agent_search(
    state: MutableMapping[str, Any],
    *,
    query: Optional[str] = None,
    limit: int = 12,
    max_total: Optional[int] = None,
    dedupe: bool = True,
    source: str = "agent",
) -> List[EvidenceEntry]:
    ensure_state_shapes(state)
    actual_query = (query or get_query_text(state)).strip()
    if not actual_query:
        log.debug("Agent search skipped: empty query.")
        return []

    hits = get_opensearch_agent_results(actual_query, limit=limit)
    if not hits:
        return []

    return merge_retrieval(
        state,
        source=source,
        hits=hits,
        limit=max_total,
        dedupe=dedupe,
    )


# ---------------------------------------------------------------------------
# Convenience re-exports (keep backward compatibility with search_core.py)
# ---------------------------------------------------------------------------
from rag_pipeline.search.keyword import get_keyword_search_results
from rag_pipeline.search.neo4j import get_neo4j_search_results as get_basic_neo4j_search_results


__all__ = [
    "get_keyword_search_results",
    "get_basic_neo4j_search_results",
    "get_neo4j_agent_results",
    "get_opensearch_agent_results",
    "get_comprehensive_schema",
    "run_agent_search",
]