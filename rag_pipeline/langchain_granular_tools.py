from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from .opengeodata_search import get_opengeodata_results
from .search_keyword import get_keyword_search_results
from .search_neo4j import get_neo4j_search_results
from .semantic_search import semantic_search as run_semantic_search
from .spatial_search import get_spatial_search_results


def _safe_int(value: Any, default: int = 8, minimum: int = 1, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _normalize_hits(hits: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for hit in hits:
        doc = hit.get("_source") or {}
        doc_id = str(hit.get("_id") or doc.get("doc_id") or "")
        if not doc_id:
            continue
        normalized.append(
            {
                "doc_id": doc_id,
                "source": source,
                "score": hit.get("_score", 0.0),
                "title": doc.get("title") or "Untitled",
                "element_type": doc.get("element_type") or doc.get("resource-type") or "resource",
                "contents": (doc.get("contents") or "")[:800],
            }
        )
    return normalized


def _build_payload(hits: List[Dict[str, Any]], source: str) -> str:
    docs = _normalize_hits(hits, source=source)
    payload = {
        "source": source,
        "count": len(docs),
        "documents": docs,
        "citation_ids": [doc["doc_id"] for doc in docs],
    }
    return json.dumps(payload, ensure_ascii=True, default=str)


def keyword_search_tool(query: str, limit: int = 8) -> str:
    hits = get_keyword_search_results(query, size=_safe_int(limit))
    return _build_payload(hits, source="keyword")


def semantic_search_tool(query: str, limit: int = 8) -> str:
    hits = run_semantic_search(query, size=_safe_int(limit))
    return _build_payload(hits, source="semantic")


def neo4j_search_tool(query: str, limit: int = 8) -> str:
    hits = get_neo4j_search_results(query, limit=_safe_int(limit))
    return _build_payload(hits, source="neo4j")


def spatial_search_tool(query: str, limit: int = 8) -> str:
    hits = get_spatial_search_results(query, size=_safe_int(limit))
    return _build_payload(hits, source="spatial")


def opengeodata_search_tool(query: str, limit: int = 8, session_context_json: Optional[str] = None) -> str:
    session_ctx: Optional[Mapping[str, Any]] = None
    if session_context_json:
        try:
            parsed = json.loads(session_context_json)
            if isinstance(parsed, dict):
                session_ctx = parsed
        except json.JSONDecodeError:
            session_ctx = None
    hits = get_opengeodata_results(query, limit=_safe_int(limit), session_ctx=session_ctx)
    return _build_payload(hits, source="opengeodata")


def make_langchain_granular_tools() -> List[Any]:
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    return [
        StructuredTool.from_function(
            func=keyword_search_tool,
            name="keyword_search",
            description="Keyword/BM25 search over indexed contents. Returns JSON with doc_ids and snippets.",
        ),
        StructuredTool.from_function(
            func=semantic_search_tool,
            name="semantic_search",
            description="Vector semantic search over indexed contents. Returns JSON with doc_ids and snippets.",
        ),
        StructuredTool.from_function(
            func=neo4j_search_tool,
            name="neo4j_search",
            description="Graph-aware Neo4j keyword search. Returns JSON with doc_ids and snippets.",
        ),
        StructuredTool.from_function(
            func=spatial_search_tool,
            name="spatial_search",
            description="Spatially-biased search inferred from location mentions. Returns JSON with doc_ids and snippets.",
        ),
        StructuredTool.from_function(
            func=opengeodata_search_tool,
            name="opengeodata_search",
            description=(
                "OpenGeoData federated search. Optional session_context_json for bbox/time/provider hints. "
                "Returns JSON with doc_ids and snippets."
            ),
        ),
    ]


__all__ = [
    "keyword_search_tool",
    "semantic_search_tool",
    "neo4j_search_tool",
    "spatial_search_tool",
    "opengeodata_search_tool",
    "make_langchain_granular_tools",
]
