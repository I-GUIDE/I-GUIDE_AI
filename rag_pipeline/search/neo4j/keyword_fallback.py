from __future__ import annotations

from typing import Any, Dict, List, MutableMapping, Optional

from ._client import get_node_class, run_query
from ..utils import get_logger, normalize_source_fields, safe_score
from ...state import EvidenceEntry, ensure_state_shapes, get_query_text, merge_retrieval

log = get_logger("neo4j.keyword_fallback")


def _build_keyword_cypher() -> str:
    return """
    MATCH (r)
    WHERE
      (r.title IS NOT NULL    AND toLower(r.title)    CONTAINS toLower($q)) OR
      (r.contents IS NOT NULL AND toLower(r.contents) CONTAINS toLower($q)) OR
      (r.tags IS NOT NULL     AND any(tag IN r.tags WHERE toLower(tag) CONTAINS toLower($q)))
    WITH r,
         CASE
           WHEN r.title IS NOT NULL    AND toLower(r.title)    CONTAINS toLower($q) THEN 2.0
           WHEN r.contents IS NOT NULL AND toLower(r.contents) CONTAINS toLower($q) THEN 1.5
           ELSE 1.0
         END AS relevance,
         coalesce(log10(toFloat(coalesce(r.click_count, 0)) + 1), 0) AS popularity,
         coalesce(toFloat(count { (r)--() }), 0) AS connectivity
    WITH r,
         relevance + (popularity * 0.2) + (connectivity * 0.05) AS score
    RETURN r AS node, score
    ORDER BY score DESC
    LIMIT $limit
    """


def _extract_node_from_record(record: Dict[str, Any], node_cls: Optional[type]) -> Optional[Any]:
    node = record.get("node")
    if node_cls and isinstance(node, node_cls):
        return node
    for value in record.values():
        if node_cls and isinstance(value, node_cls):
            return value
    return None


def _records_to_hits(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    node_cls = get_node_class()
    hits: List[Dict[str, Any]] = []
    for idx, record in enumerate(records):
        node = _extract_node_from_record(record, node_cls)
        score = safe_score(record.get("score", 1.0))
        if node is not None:
            properties = dict(node)
            doc_id = properties.get("_id", getattr(node, "element_id", f"node:{idx}"))
        else:
            properties = {k: v for k, v in record.items() if isinstance(v, (str, int, float, list, dict))}
            doc_id = properties.get("doc_id", f"row:{idx}")
        source = normalize_source_fields(properties, str(doc_id))
        hits.append({"_id": str(doc_id), "_score": score, "_source": source})
    return hits


def get_neo4j_search_results(
    user_query: str,
    limit: int = 12,
    *,
    driver: Optional[Any] = None,
    cypher: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Keyword-style Neo4j search with no LLM dependency. Tier 3 fallback."""
    query = (user_query or "").strip()
    if not query:
        return []
    params = {"q": query, "limit": max(1, min(int(limit or 0), 100))}
    cypher_stmt = cypher or _build_keyword_cypher()
    try:
        records = run_query(cypher_stmt, params, driver=driver)
    except Exception as exc:
        log.error("Neo4j keyword query failed: %s", exc)
        return []
    return _records_to_hits(records) if records else []


def retrieve_neo4j(state: MutableMapping[str, Any]) -> List[Dict[str, Any]]:
    ensure_state_shapes(state)
    query = get_query_text(state).strip()
    if not query:
        log.debug("Neo4j retriever skipped: empty query.")
        return []
    params = state.get("params") or {}
    try:
        limit = int(params.get("top_k", 8))
    except (TypeError, ValueError):
        limit = 8
    return get_neo4j_search_results(query, limit=limit)


def run_neo4j_search(
    state: MutableMapping[str, Any],
    *,
    query: Optional[str] = None,
    limit: int = 12,
    max_total: Optional[int] = None,
    dedupe: bool = True,
    source: str = "neo4j",
) -> List[EvidenceEntry]:
    ensure_state_shapes(state)
    actual_query = (query or get_query_text(state)).strip()
    if not actual_query:
        log.debug("Neo4j search skipped: empty query.")
        return []
    hits = get_neo4j_search_results(actual_query, limit=limit)
    if not hits:
        return []
    return merge_retrieval(state, source=source, hits=hits, limit=max_total, dedupe=dedupe)


__all__ = ["get_neo4j_search_results", "retrieve_neo4j", "run_neo4j_search"]