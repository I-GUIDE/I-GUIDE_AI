from __future__ import annotations

import logging
from typing import Any, List, MutableMapping

from .opengeodata_search import retrieve_opengeodata
from .search_keyword import retrieve_keyword
from .search_neo4j import retrieve_neo4j
from .semantic_search import retrieve_semantic
from .spatial_search import retrieve_spatial
from .state import (
    AgentState,
    RoutingDecision,
    ensure_state_shapes,
    get_query_text,
    merge_retrieval,
    summarize_evidence,
)
from .search_utils import get_logger

logger = get_logger("search_core")

def _record_decision(decisions: List[RoutingDecision], source: str, reason: str) -> None:
    decisions.append(RoutingDecision(source=source, reason=reason))


def _limit_for(state: MutableMapping[str, Any]) -> int:
    params = state.get("params") or {}
    try:
        return max(1, int(params.get("top_k", 8)))
    except (TypeError, ValueError):
        return 8


def run_retrieval(state: MutableMapping[str, Any]) -> AgentState:
    state = ensure_state_shapes(state)
    limit = _limit_for(state)
    query = get_query_text(state)
    decisions: List[RoutingDecision] = []
    
    logger.info(f"=== Starting retrieval for query: '{query}' (top_k={limit}) ===")

    keyword_hits = retrieve_keyword(state)
    keyword_titles = [hit.get("_source", {}).get("title", "No title") for hit in keyword_hits]
    logger.info(f"📚 KEYWORD search returned {len(keyword_hits)} documents:")
    for i, title in enumerate(keyword_titles[:10], 1):
        logger.info(f"  {i}. {title}")
    if len(keyword_titles) > 10:
        logger.info(f"  ... and {len(keyword_titles) - 10} more")
    
    appended = merge_retrieval(
        state,
        source="keyword",
        hits=keyword_hits,
        limit=limit,
    )
    _record_decision(decisions, "keyword", f"hits:{len(keyword_hits)} appended:{len(appended)}")

    semantic_hits = retrieve_semantic(state)
    semantic_titles = [hit.get("_source", {}).get("title", "No title") for hit in semantic_hits]
    logger.info(f"🔍 SEMANTIC search returned {len(semantic_hits)} documents:")
    for i, title in enumerate(semantic_titles[:10], 1):
        logger.info(f"  {i}. {title}")
    if len(semantic_titles) > 10:
        logger.info(f"  ... and {len(semantic_titles) - 10} more")
    
    appended = merge_retrieval(
        state,
        source="semantic",
        hits=semantic_hits,
        limit=limit,
    )
    _record_decision(decisions, "semantic", f"hits:{len(semantic_hits)} appended:{len(appended)}")

    session_ctx = state.get("session_context") or {}
    if "graph" in query.lower() or session_ctx.get("use_neo4j"):
        neo_hits = retrieve_neo4j(state)
        neo_titles = [hit.get("_source", {}).get("title", "No title") for hit in neo_hits]
        logger.info(f"🕸️ NEO4J search returned {len(neo_hits)} documents:")
        for i, title in enumerate(neo_titles[:10], 1):
            logger.info(f"  {i}. {title}")
        if len(neo_titles) > 10:
            logger.info(f"  ... and {len(neo_titles) - 10} more")
        
        appended = merge_retrieval(
            state,
            source="neo4j",
            hits=neo_hits,
            limit=limit,
        )
        _record_decision(decisions, "neo4j", f"hits:{len(neo_hits)} appended:{len(appended)}")

    if session_ctx.get("use_spatial"):
        spatial_hits = retrieve_spatial(state)
        spatial_titles = [hit.get("_source", {}).get("title", "No title") for hit in spatial_hits]
        logger.info(f"🗺️ SPATIAL search returned {len(spatial_hits)} documents:")
        for i, title in enumerate(spatial_titles[:10], 1):
            logger.info(f"  {i}. {title}")
        if len(spatial_titles) > 10:
            logger.info(f"  ... and {len(spatial_titles) - 10} more")
        
        appended = merge_retrieval(
            state,
            source="spatial",
            hits=spatial_hits,
            limit=limit,
        )
        _record_decision(decisions, "spatial", f"hits:{len(spatial_hits)} appended:{len(appended)}")

    opengeo_hits = retrieve_opengeodata(state)
    opengeo_titles = [hit.get("_source", {}).get("title", "No title") for hit in opengeo_hits]
    logger.info(f"🌍 OPENGEODATA search returned {len(opengeo_hits)} documents:")
    for i, title in enumerate(opengeo_titles[:10], 1):
        logger.info(f"  {i}. {title}")
    if len(opengeo_titles) > 10:
        logger.info(f"  ... and {len(opengeo_titles) - 10} more")
    
    # Don't apply limit to opengeodata - always include results to ensure they appear in frontend
    # The limit will be applied later during reranking/final selection
    appended = merge_retrieval(
        state,
        source="opengeodata",
        hits=opengeo_hits,
        limit=None,  # No limit - always include opengeodata results
    )
    logger.info(f"OpenGeoData: {len(appended)} documents appended to evidence")
    _record_decision(
        decisions,
        "opengeodata",
        f"hits:{len(opengeo_hits)} appended:{len(appended)}",
    )

    # Summary log
    total_evidence = len(state.get("evidence", {}).get("retrieved_documents", []))
    logger.info(f"=== Retrieval complete: {total_evidence} total documents in evidence ===")

    trace = state.setdefault("trace_observability", {})
    trace["retrieval_summary"] = summarize_evidence(state["evidence"])
    trace["retrieval_routing_decisions"] = [
        {"source": decision.source, "reason": decision.reason} for decision in decisions
    ]
    return state  # type: ignore[return-value]


__all__ = ["run_retrieval"]
