"""
text2cypher.py
--------------
LLM-powered Neo4j search for the I-GUIDE RAG pipeline.

Neo4j search hierarchy (most reliable → most flexible):
  1. Pattern tools  (patterns.py)         — deterministic, no LLM, fast
  2. Text2Cypher    (_text2cypher)         — LLM-generated Cypher, flexible
  3. Keyword search (keyword_fallback.py) — always works, no LLM

Public API:
  get_neo4j_agent_results(query, limit)        → List[hit-dicts]
  get_neo4j_element_by_id_results(element_id)  → List[hit-dicts]
  get_neo4j_related_node_results(element_id)   → List[hit-dicts]
  explore_neo4j_related_nodes(element_id)      → Dict (graph payload)
  get_comprehensive_schema()                   → str
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from functools import lru_cache, wraps
from typing import Any, Dict, List, Mapping, Optional, Tuple

import requests
from neo4j.graph import Node as _Neo4jNode

from ._client import run_query
from .patterns import (
    _get_internal_labels,
    build_element_by_id_query,
    build_explore_related_nodes_query,
    build_tool_query,
    detect_pattern,
    run_user_author_fallback,
)
from ..utils import get_logger, getenv, normalize_source_fields, safe_score

log = get_logger("neo4j.text2cypher")

_SCHEMA_CACHE: Dict[str, Any] = {"ts": 0.0, "val": ""}
_SCHEMA_TTL_SEC = float(os.getenv("SCHEMA_CACHE_TTL_SEC", "300"))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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


def _transform_thumbnail(value: Any) -> Any:
    return value


def _as_list(val: Any) -> List[Any]:
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

def _openai_endpoint(path: str) -> str:
    base = os.getenv("ANVILGPT_URL", "https://api.openai.com/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/{path.lstrip('/')}"


def _llm_chat(messages: List[Dict[str, str]], max_tokens: int = 512, temperature: float = 0.0) -> str:
    url = _openai_endpoint("chat/completions")
    api_key = getenv("ANVILGPT_KEY")
    model = (os.getenv("ANVILGPT_MODEL") or "gpt-4o-mini").strip()
    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=payload, timeout=45)
    response.raise_for_status()
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as err:
        raise ValueError(f"Unexpected LLM response payload: {data}") from err


# ---------------------------------------------------------------------------
# Node processing helpers
# ---------------------------------------------------------------------------

def _extract_node_from_record(rec: Dict[str, Any]) -> Optional[Any]:
    node = rec.get("node")
    if isinstance(node, _Neo4jNode):
        return node
    if isinstance(node, Mapping):
        return node
    for value in rec.values():
        if isinstance(value, _Neo4jNode):
            return value
        if isinstance(value, Mapping) and any(key in value for key in ("id", "_id", "doc_id", "title")):
            return value
    return None


def _node_props_labels_ref(node: Any, fallback_id: str) -> Tuple[Dict[str, Any], set, str]:
    if isinstance(node, _Neo4jNode):
        props = dict(node)
        labels = set(getattr(node, "labels", set())) - _get_internal_labels()
        ref_id = (
            props.get("_id") or props.get("doc_id") or props.get("id")
            or getattr(node, "element_id", fallback_id)
        )
        return props, labels, str(ref_id)
    if isinstance(node, Mapping):
        props = dict(node)
        raw_labels = props.pop("_labels", [])
        labels = set(raw_labels if isinstance(raw_labels, list) else []) - _get_internal_labels()
        ref_id = props.get("_id") or props.get("doc_id") or props.get("id") or fallback_id
        return props, labels, str(ref_id)
    return {}, set(), str(fallback_id)


def _graph_context_prefix(record: Dict[str, Any]) -> str:
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


def _source_to_hit(
    source: Dict[str, Any],
    *,
    doc_id: str,
    score: float,
    node_labels: Optional[set] = None,
    prefix: str = "",
) -> Dict[str, Any]:
    labels = node_labels or set()
    resource_type = (
        source.get("resource-type")
        or source.get("element_type")
        or (next(iter(labels)).lower() if labels else None)
    )
    base_contents = source.get("contents") or ""
    contents = f"[{prefix}] {base_contents}".strip() if prefix else base_contents or None
    return {
        "_id": doc_id,
        "_score": score,
        "_source": {
            "doc_id":          doc_id,
            "id":              source.get("id") or doc_id,
            "contributor":     source.get("contributor"),
            "contents":        contents,
            "resource-type":   resource_type,
            "title":           source.get("title"),
            "authors":         _as_list(source.get("authors")),
            "tags":            _as_list(source.get("tags")),
            "thumbnail-image": _transform_thumbnail(source.get("thumbnail-image", source.get("thumbnail_image"))),
            "click_count":     source.get("click_count", 0),
        },
    }


def _node_to_hit(node: Any, *, score: float = 1.0, record: Optional[Dict[str, Any]] = None, fallback_id: str = "node") -> Optional[Dict[str, Any]]:
    props, node_labels, ref_id = _node_props_labels_ref(node, fallback_id)
    if not props:
        return None
    source = normalize_source_fields(props, ref_id)
    doc_id = str(source.get("doc_id") or source.get("id") or ref_id)
    prefix = _graph_context_prefix(record or {})
    return _source_to_hit(source, doc_id=doc_id, score=score, node_labels=node_labels, prefix=prefix)


def _rows_to_hits(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for idx, record in enumerate(rows):
        score = safe_score(record.get("score", 1.0))
        node = _extract_node_from_record(record)
        if node is not None:
            hit = _node_to_hit(node, score=score, record=record, fallback_id=f"node:{idx}")
            if hit is not None:
                hits.append(hit)
            continue
        props = {k: v for k, v in record.items() if isinstance(v, (str, int, float, list, dict))}
        ref_id = props.get("_id") or props.get("doc_id") or props.get("id") or f"row:{idx}"
        source = normalize_source_fields(props, str(ref_id))
        doc_id = str(source.get("doc_id") or source.get("id") or ref_id)
        hits.append(_source_to_hit(source, doc_id=doc_id, score=score, prefix=_graph_context_prefix(record)))
    return hits


def _hit_to_document(hit: Dict[str, Any], source_name: str = "neo4j") -> Dict[str, Any]:
    doc = hit.get("_source") or {}
    doc_id = str(hit.get("_id") or doc.get("doc_id") or doc.get("id") or "")
    return {
        "doc_id": doc_id,
        "source": source_name,
        "score": hit.get("_score", 0.0),
        "title": doc.get("title") or "Untitled",
        "element_type": doc.get("element_type") or doc.get("resource-type") or "resource",
        "contents": (doc.get("contents") or "")[:800],
        "authors": _as_list(doc.get("authors")),
        "tags": _as_list(doc.get("tags")),
    }


def _empty_related_payload() -> Dict[str, Any]:
    return {"source": "neo4j", "count": 0, "seed": None, "documents": [], "edges": [], "citation_ids": []}


def _normalize_edges(raw_edges: Any) -> List[Dict[str, str]]:
    edges: List[Dict[str, str]] = []
    seen = set()
    for edge in raw_edges or []:
        if not isinstance(edge, Mapping):
            continue
        src = str(edge.get("src") or "").strip()
        dst = str(edge.get("dst") or "").strip()
        rel_type = str(edge.get("type") or "").strip()
        if not src or not dst:
            continue
        key = (src, dst, rel_type)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"src": src, "dst": dst, "type": rel_type})
    return edges


def _related_rows_to_hits(rows: List[Dict[str, Any]], *, include_seed: bool = True) -> List[Dict[str, Any]]:
    if not rows:
        return []
    row = rows[0]
    hits: List[Dict[str, Any]] = []
    seen: set = set()

    def add_node(node: Any, score: float, fallback_id: str) -> None:
        hit = _node_to_hit(node, score=score, fallback_id=fallback_id)
        if hit is None:
            return
        doc_id = hit.get("_id")
        if not doc_id or doc_id in seen:
            return
        seen.add(doc_id)
        hits.append(hit)

    if include_seed:
        add_node(row.get("seed"), 1.0, "seed")
    for idx, node in enumerate(row.get("nodes") or []):
        add_node(node, 0.8, f"related:{idx}")
    return hits


def _related_rows_to_payload(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return _empty_related_payload()
    row = rows[0]
    seed_hit = _node_to_hit(row.get("seed"), score=1.0, fallback_id="seed")
    if seed_hit is None:
        return _empty_related_payload()
    seed_doc = _hit_to_document(seed_hit)
    related_hits = _related_rows_to_hits(rows, include_seed=False)
    seed_doc_id = seed_doc.get("doc_id")
    documents = [_hit_to_document(hit) for hit in related_hits if hit.get("_id") != seed_doc_id]
    citation_ids: List[str] = []
    for doc_id in [seed_doc.get("doc_id"), *[doc.get("doc_id") for doc in documents]]:
        if doc_id and doc_id not in citation_ids:
            citation_ids.append(str(doc_id))
    return {
        "source": "neo4j",
        "count": len(documents),
        "seed": seed_doc,
        "documents": documents,
        "edges": _normalize_edges(row.get("edges")),
        "citation_ids": citation_ids,
    }


# ---------------------------------------------------------------------------
# Public element lookup (Tier 1 execution)
# ---------------------------------------------------------------------------

def get_neo4j_element_by_id_results(element_id: str) -> List[Dict[str, Any]]:
    """Deterministically fetch one public knowledge element by canonical Neo4j id."""
    try:
        cypher, params = build_element_by_id_query(element_id)
        return _rows_to_hits(run_query(cypher, params))
    except ValueError:
        return []
    except Exception as exc:
        log.warning("Neo4j element ID lookup failed (%s).", exc)
        return []


def get_neo4j_related_node_results(element_id: str, *, depth: int = 2, limit: int = 50, include_seed: bool = True) -> List[Dict[str, Any]]:
    """Deterministically fetch public related-node hits for a seed element."""
    try:
        cypher, params = build_explore_related_nodes_query(element_id, depth=depth, limit=limit)
        return _related_rows_to_hits(run_query(cypher, params), include_seed=include_seed)
    except ValueError:
        return []
    except Exception as exc:
        log.warning("Neo4j related-node lookup failed (%s).", exc)
        return []


def explore_neo4j_related_nodes(element_id: str, *, depth: int = 2, limit: int = 50) -> Dict[str, Any]:
    """Deterministically fetch a graph-shaped related-node neighborhood."""
    try:
        cypher, params = build_explore_related_nodes_query(element_id, depth=depth, limit=limit)
        return _related_rows_to_payload(run_query(cypher, params))
    except ValueError:
        return _empty_related_payload()
    except Exception as exc:
        log.warning("Neo4j related-node exploration failed (%s).", exc)
        return _empty_related_payload()


# ---------------------------------------------------------------------------
# Schema discovery
# ---------------------------------------------------------------------------

def get_comprehensive_schema() -> str:
    """Compact schema string (labels, relationships, sample properties). TTL-cached."""
    now = time.time()
    cached = _SCHEMA_CACHE.get("val")
    if cached and (now - _SCHEMA_CACHE.get("ts", 0.0)) < _SCHEMA_TTL_SEC:
        return cached

    parts: List[str] = []
    rows = run_query("CALL db.labels() YIELD label RETURN label", {})
    labels = [r["label"] for r in rows]
    parts.append(f"Labels: {', '.join(labels) if labels else '(none)'}")

    rows = run_query("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType", {})
    rels = [r["relationshipType"] for r in rows]
    parts.append(f"Relationships: {', '.join(rels) if rels else '(none)'}")

    for label in labels[:8]:
        keys_rows = run_query(f"MATCH (n:`{label}`) WITH n LIMIT 5 RETURN keys(n) AS k", {})
        keys = sorted({key for row in keys_rows for key in row["k"]})
        parts.append(f"Properties[{label}]: {', '.join(keys) if keys else '(none)'}")
        for prop in ("name", "display_first_name", "display_last_name", "organization", "affiliation"):
            if prop in keys:
                sample_rows = run_query(
                    f"MATCH (n:`{label}`) WHERE n.`{prop}` IS NOT NULL RETURN DISTINCT n.`{prop}` AS v LIMIT 3", {}
                )
                samples = [str(r["v"]) for r in sample_rows if r["v"]]
                if samples:
                    parts.append(f"  Sample {label}.{prop}: {', '.join(samples)}")

    snapshot = "\n".join(parts)
    _SCHEMA_CACHE.update({"ts": now, "val": snapshot})
    return snapshot


# ---------------------------------------------------------------------------
# Text2Cypher (Tier 2)
# ---------------------------------------------------------------------------

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
    if "limit" not in cypher.lower():
        cypher = cypher.rstrip().rstrip(";") + " LIMIT $limit"
        log.debug("Injected missing LIMIT into generated Cypher.")
    return cypher


def _text2cypher(user_query: str, schema: str, limit: int) -> Tuple[str, Dict[str, Any]]:
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
        [{"role": "system", "content": _CYPHER_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
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
    return run_query(cypher, params)


# ---------------------------------------------------------------------------
# Main 3-tier dispatcher
# ---------------------------------------------------------------------------

def get_neo4j_agent_results(user_query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """
    3-tier Neo4j search:
      1. Pattern tools  — deterministic, no LLM
      2. Text2Cypher    — LLM-generated Cypher
      3. Keyword search — always-on fallback
    """
    query = (user_query or "").strip()
    if not query:
        return []

    # Tier 1: pattern tools
    pattern_result = detect_pattern(query)
    if pattern_result is not None:
        pattern_name, captured = pattern_result
        if pattern_name == "element_by_id":
            hits = get_neo4j_element_by_id_results(captured.get("element_id", ""))
            log.info("Element ID tool returned %d results for query: %s", len(hits), query)
            return hits
        if pattern_name == "explore_related_by_id":
            hits = get_neo4j_related_node_results(captured.get("element_id", ""), depth=2, limit=limit, include_seed=True)
            log.info("Related-node ID tool returned %d results for query: %s", len(hits), query)
            return hits
        tool_query = build_tool_query(pattern_name, captured, limit)
        if tool_query is not None:
            cypher, params = tool_query
            try:
                rows = run_query(cypher, params)
                hits = _rows_to_hits(rows)
                if not hits and pattern_name == "by_author":
                    log.debug("Contributor author lookup empty, trying User fallback.")
                    rows = run_user_author_fallback(captured, limit, run_query)
                    hits = _rows_to_hits(rows)
                if hits:
                    log.info("Pattern tool '%s' returned %d results for query: %s", pattern_name, len(hits), query)
                    return hits
                log.debug("Pattern tool '%s' returned 0 results; escalating.", pattern_name)
            except Exception as exc:
                log.warning("Pattern tool '%s' failed (%s); escalating to Text2Cypher.", pattern_name, exc)

    # Tier 2: Text2Cypher
    if os.getenv("USE_TEXT2CYPHER", "true").lower() in ("true", "1", "yes"):
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

    # Tier 3: keyword fallback
    log.info("Using basic Neo4j keyword fallback for query: %s", query)
    from .keyword_fallback import get_neo4j_search_results
    return get_neo4j_search_results(query, limit=limit)


__all__ = [
    "get_neo4j_agent_results",
    "get_neo4j_element_by_id_results",
    "get_neo4j_related_node_results",
    "explore_neo4j_related_nodes",
    "get_comprehensive_schema",
]
