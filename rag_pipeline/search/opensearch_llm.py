"""
opensearch_llm.py
-----------------
LLM-powered OpenSearch query generation for the I-GUIDE RAG pipeline.

Generates full OpenSearch DSL query bodies from natural language using an LLM
and the live index schema. Intended for complex spatial/temporal queries that
fixed query templates cannot handle.

NOTE: Not wired into the main pipeline. Reserved for future use.

Public API:
  get_opensearch_agent_results(query, limit) → List[hit-dicts]
  run_agent_search(state, ...)               → List[EvidenceEntry]
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from functools import lru_cache, wraps
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

import requests
from opensearchpy import OpenSearch

from ..state import EvidenceEntry, ensure_state_shapes, get_query_text, merge_retrieval
from .utils import getenv, get_logger, safe_score

log = get_logger("search.opensearch_llm")

_OS_SCHEMA_CACHE: Dict[str, Any] = {"ts": 0.0, "val": ""}
_OS_FORBIDDEN_KEYS = {"delete", "update", "script", "bulk", "reindex", "indices"}
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
# OpenSearch client
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _os_client() -> OpenSearch:
    node = getenv("OPENSEARCH_NODE")
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
    index = getenv("OPENSEARCH_INDEX")
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
    props = mapping.get(_os_index(), {}).get("mappings", {}).get("properties", {})
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
    """LLM-powered OpenSearch query. Falls back to keyword search on failure."""
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
        from .keyword import get_keyword_search_results
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
            "_score": safe_score(hit.get("_score", 1.0)),
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
    return merge_retrieval(state, source=source, hits=hits, limit=max_total, dedupe=dedupe)


__all__ = ["get_opensearch_agent_results", "get_opensearch_schema", "run_agent_search"]
