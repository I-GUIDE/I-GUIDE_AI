"""Shared-state supervisor-over-peers orchestration.

Search / analysis / code are **peer** capability nodes (same level) that share one
typed ``SupervisorState``; an LLM **supervisor** decides the next action and the
graph **loops** back to it — so the agent can search-only, analyze-from-memory,
or multi-hop (analyze → "need more" → search → analyze). This is the agentic
alternative to nesting search under analysis.

Design rules (from the architecture discussion):
* **Peers, not pipeline stages** — the supervisor routes dynamically and loops.
* **Operators bundled into capabilities** — rerank lives inside the search node,
  grounding audit inside the analysis node (deterministic, applied whenever that
  capability runs).
* **Context hygiene** — the heavy ``evidence`` lives in shared state; the
  supervisor only ever sees a *distilled* view (counts/flags/summaries).

Everything is dependency-injected (``decide_fn`` / ``search_fn`` / ``analyze_fn`` /
``code_fn`` / ``llm``) so the graph is unit-testable without a live LLM/backends.
Default adapters wire to the existing agents and are best-effort (need live
validation). Gated by ``AGENT_SUPERVISOR``; the agents-as-tools path stays default.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agent_runtime.evidence_quality import audit_answer_grounding, rerank_documents
from agent_runtime.evidence_subgraph import (
    _doc_field,
    default_compose_fn,
    distill_evidence_state,
    extract_documents_from_search_evidence,
)
from agent_runtime.streaming_trace import emit_trace_event

ALLOWED_ACTIONS = ("search", "analyze", "code", "done")
DEFAULT_MAX_STEPS = 8

# Injected callables
DecideFn = Callable[["SupervisorState", Dict[str, Any]], str]   # (state, distilled) -> action
SearchFn = Callable[[str, "SupervisorState"], List[Any]]         # (query, state) -> documents
AnalyzeFn = Callable[[str, List[Any], Optional[List[Any]]], str] # (query, evidence, chat_history) -> answer
CodeFn = Callable[[str, List[Any]], Any]                         # (query, evidence) -> code_result


class SupervisorState(TypedDict, total=False):
    query: str
    chat_history: List[Any]
    thread_id: Optional[str]
    evidence: List[Any]        # accumulated, dedup'd documents (shared, heavy)
    answer: str
    code_result: Any
    audit: Dict[str, Any]
    actions: List[str]         # supervisor decision history
    next_action: str
    step: int
    max_steps: int
    final_answer: str
    distilled: Dict[str, Any]


def is_supervisor_enabled() -> bool:
    """Whether the orchestrate path should use the supervisor-over-peers graph."""
    return (os.getenv("AGENT_SUPERVISOR") or "").strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Shared-state helpers
# ---------------------------------------------------------------------------

def _merge_dedup(existing: List[Any], new: List[Any]) -> List[Any]:
    """Merge new documents into existing, dedup by doc id, preserving order."""
    merged = list(existing or [])
    seen = {_doc_field(d, "doc_id", "id", "_id", default=f"_{i}") for i, d in enumerate(merged)}
    for j, d in enumerate(new or []):
        key = _doc_field(d, "doc_id", "id", "_id", default=f"new-{j}")
        if key in seen:
            continue
        seen.add(key)
        merged.append(d)
    return merged


def _distill(state: SupervisorState) -> Dict[str, Any]:
    """Compact view of progress for the supervisor (no heavy documents)."""
    docs = state.get("evidence") or []
    audit = state.get("audit") or {}
    return {
        "has_evidence": bool(docs),
        "document_count": len(docs),
        "has_answer": bool((state.get("answer") or "").strip()),
        "has_code": state.get("code_result") is not None,
        "audit_severity": audit.get("severity"),
        "actions_taken": list(state.get("actions") or []),
    }


# ---------------------------------------------------------------------------
# Default decider (LLM with heuristic fallback)
# ---------------------------------------------------------------------------

def _heuristic_decision(distilled: Dict[str, Any]) -> str:
    if not distilled.get("has_evidence") and "search" not in distilled.get("actions_taken", []):
        return "search"
    if distilled.get("has_evidence") and not distilled.get("has_answer"):
        return "analyze"
    return "done"


def default_decide_fn(llm: Optional[Any] = None) -> DecideFn:
    """LLM-driven next-action chooser with a deterministic heuristic fallback."""

    def decide(state: SupervisorState, distilled: Dict[str, Any]) -> str:
        prompt = (
            "You are the orchestration supervisor for a geospatial research agent.\n"
            "Choose the SINGLE next action. Capabilities are peers you can use in any "
            "order and repeat as needed:\n"
            "- search: retrieve evidence (datasets, publications, notebooks)\n"
            "- analyze: compose an answer from the evidence gathered so far\n"
            "- code: produce runnable code / implementation\n"
            "- done: the user's request is satisfied\n\n"
            "Respond ONLY with JSON: {\"next\": \"search|analyze|code|done\", \"reason\": \"...\"}\n\n"
            f"User request:\n{state.get('query', '')}\n\n"
            f"Progress so far:\n{json.dumps(distilled, ensure_ascii=True)}\n"
        )
        try:
            active = llm
            if active is None:
                from agent_runtime.executor_factory import build_default_llm

                active = build_default_llm()
            raw = active.invoke(prompt) if hasattr(active, "invoke") else active(prompt)
            text = getattr(raw, "content", raw)
            if isinstance(text, list):
                text = "".join(str(getattr(p, "text", p)) for p in text)
            start, end = str(text).find("{"), str(text).rfind("}")
            parsed = json.loads(str(text)[start : end + 1]) if start != -1 else {}
            nxt = str(parsed.get("next") or "").strip().lower()
            if nxt in ALLOWED_ACTIONS:
                return nxt
        except Exception:
            pass
        return _heuristic_decision(distilled)

    return decide


# ---------------------------------------------------------------------------
# Default worker adapters (best-effort; wire to existing agents — need live validation)
# ---------------------------------------------------------------------------

def default_search_fn(*, llm: Optional[Any] = None, tool_strategy: str = "granular",
                      include_mcp_tools: bool = False, mcp_modules: Optional[List[str]] = None,
                      enabled_search_methods: Optional[List[str]] = None,
                      skill_roots: Optional[List[str]] = None) -> SearchFn:
    def fn(query: str, state: SupervisorState) -> List[Any]:
        from agent_runtime.executor_factory import (
            agent_config,
            build_search_agent_executor,
            child_thread_id,
            invoke_agent_with_payload_fallback,
        )
        from agent_runtime.runtime_utils import build_search_evidence_payload

        executor = build_search_agent_executor(
            llm=llm, tool_strategy=tool_strategy, include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules, skill_roots=skill_roots,
        )
        resp = invoke_agent_with_payload_fallback(
            executor, query=query, chat_history=None,
            config=agent_config(child_thread_id(state.get("thread_id"), "sup_search")),
        )
        return extract_documents_from_search_evidence(build_search_evidence_payload(query, resp, None))

    return fn


def default_code_fn(*, llm: Optional[Any] = None, skill_roots: Optional[List[str]] = None) -> CodeFn:
    def fn(query: str, evidence: List[Any]) -> Any:
        from agent_runtime.graph_runtime import run_code_agent_query

        result = run_code_agent_query(query, llm=llm, skill_roots=skill_roots)
        return {"answer": result.get("final_answer", ""), "code_result": result.get("code_result")}

    return fn


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_supervisor_graph(
    *,
    decide_fn: Optional[DecideFn] = None,
    search_fn: Optional[SearchFn] = None,
    analyze_fn: Optional[AnalyzeFn] = None,
    code_fn: Optional[CodeFn] = None,
    llm: Optional[Any] = None,
    top_k: int = 5,
    do_rerank: bool = True,
    do_audit: bool = True,
) -> Any:
    """Compile the supervisor-over-peers graph. Workers default to existing agents."""
    decide = decide_fn or default_decide_fn(llm=llm)
    do_search = search_fn or default_search_fn(llm=llm)
    do_analyze = analyze_fn or default_compose_fn(llm=llm)
    do_code = code_fn or default_code_fn(llm=llm)

    def supervisor_node(state: SupervisorState) -> Dict[str, Any]:
        step = state.get("step", 0)
        distilled = _distill(state)
        if step >= state.get("max_steps", DEFAULT_MAX_STEPS):
            nxt = "done"
        else:
            nxt = decide(state, distilled)
            if nxt not in ALLOWED_ACTIONS:
                nxt = "done"
        emit_trace_event(
            "node_completed",
            {"stage": "supervisor", "route": nxt, "message": f"supervisor → {nxt}"},
            node="supervisor",
        )
        return {"next_action": nxt, "actions": [*(state.get("actions") or []), nxt], "step": step + 1}

    def search_node(state: SupervisorState) -> Dict[str, Any]:
        q = state.get("query", "")
        emit_trace_event("node_started", {"stage": "search", "message": "Searching"}, node="search")
        docs = do_search(q, state) or []
        if do_rerank and len(docs) > 1:
            docs = rerank_documents(q, docs, top_k=top_k, llm=llm)  # operator bundled into search
        merged = _merge_dedup(state.get("evidence") or [], docs)
        emit_trace_event(
            "node_completed", {"stage": "search", "message": f"{len(merged)} docs in evidence"}, node="search"
        )
        return {"evidence": merged}

    def analysis_node(state: SupervisorState) -> Dict[str, Any]:
        q = state.get("query", "")
        evidence = state.get("evidence") or []
        emit_trace_event("node_started", {"stage": "analyze", "message": "Composing answer"}, node="analyze")
        answer = do_analyze(q, evidence, state.get("chat_history"))
        audit = audit_answer_grounding(q, answer, evidence, llm=llm) if do_audit else {}  # operator bundled in
        emit_trace_event(
            "node_completed",
            {"stage": "analyze", "message": audit.get("summary") or "Answer composed"},
            node="analyze",
        )
        return {"answer": answer, "audit": audit}

    def code_node(state: SupervisorState) -> Dict[str, Any]:
        q = state.get("query", "")
        emit_trace_event("node_started", {"stage": "code", "message": "Generating code"}, node="code")
        result = do_code(q, state.get("evidence") or [])
        emit_trace_event("node_completed", {"stage": "code", "message": "Code ready"}, node="code")
        update: Dict[str, Any] = {"code_result": result}
        # If code produced an answer and analysis hasn't, surface it.
        if isinstance(result, dict) and result.get("answer") and not (state.get("answer") or "").strip():
            update["answer"] = result["answer"]
        return update

    def finalize_node(state: SupervisorState) -> Dict[str, Any]:
        final = (state.get("answer") or "").strip()
        if not final and isinstance(state.get("code_result"), dict):
            final = str(state["code_result"].get("answer") or "")
        merged = {**state, "final_answer": final}
        return {"final_answer": final, "distilled": {**_distill(state), "answer": final}}

    builder = StateGraph(SupervisorState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("search", search_node)
    builder.add_node("analyze", analysis_node)
    builder.add_node("code", code_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda s: s.get("next_action", "done"),
        {"search": "search", "analyze": "analyze", "code": "code", "done": "finalize"},
    )
    # Peers loop back to the supervisor (this is what restores dynamic ordering / multi-hop).
    builder.add_edge("search", "supervisor")
    builder.add_edge("analyze", "supervisor")
    builder.add_edge("code", "supervisor")
    builder.add_edge("finalize", END)
    return builder.compile()


def run_supervisor(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    thread_id: Optional[str] = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    **graph_kwargs: Any,
) -> Dict[str, Any]:
    """Build + run the supervisor graph; return the full final state."""
    graph = build_supervisor_graph(llm=llm, **graph_kwargs)
    return graph.invoke(
        {
            "query": query,
            "chat_history": chat_history or [],
            "thread_id": thread_id,
            "evidence": [],
            "actions": [],
            "step": 0,
            "max_steps": max_steps,
        }
    )


__all__ = [
    "SupervisorState",
    "build_supervisor_graph",
    "run_supervisor",
    "is_supervisor_enabled",
    "default_decide_fn",
    "default_search_fn",
    "default_code_fn",
]
