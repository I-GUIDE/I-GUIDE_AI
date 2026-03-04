from __future__ import annotations

import logging
from typing import Any, MutableMapping

from .generation import run_generation
from .reranker_llm import rerank_evidence_with_llm
from .search_core import run_retrieval
from .state import AgentState, ensure_state_shapes

logger = logging.getLogger(__name__)


def rag_pipeline(state: MutableMapping[str, Any]) -> AgentState:
    """
    Run the retrieval and generation stages of the pipeline using shared AgentState.
    """
    state = ensure_state_shapes(state)
    state = run_retrieval(state)
    params = state.get("params") or {}
    if params.get("enable_llm_reranker", True):
        try:
            state = rerank_evidence_with_llm(state, top_k=params.get("top_k"))
        except Exception as exc:
            logger.warning("LLM reranker failed; continuing without reranking: %s", exc)
    state = run_generation(state)
    return state  # type: ignore[return-value]


__all__ = ["rag_pipeline"]
