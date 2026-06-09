"""Shared-state supervisor-over-peers orchestration.

Search / analyze / code are **peer** capability nodes (same level) that share one
typed ``SupervisorState``; an LLM **supervisor** decides the next action and the
graph **loops** back to it. When the supervisor is ``done``, a dedicated
**synthesize** node composes the final, grounded answer.

Single-responsibility split:
* **search**   — retrieve evidence (rerank bundled in).
* **analyze**  — *execute a GIS/data analysis workflow* (run spatial/stat tools),
  writing ``analysis_results`` to shared state. It does NOT compose prose.
* **code**     — produce runnable code, writing ``code_result``.
* **synthesize** — compose the final answer (original ``ANALYSIS_AGENT_PROMPT``
  format) from evidence + analysis_results + code_result, then audit grounding.

The supervisor only ever sees a *distilled* view (counts/flags), never the heavy
documents. Everything is dependency-injected so the graph is unit-testable with no
live LLM/backends. Default adapters wire to existing agents (best-effort; need
live validation). Default ON; per-request override ``use_supervisor``; env opt-out
``AGENT_SUPERVISOR=0``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agent_runtime.evidence_quality import audit_answer_grounding, rerank_documents
from agent_runtime.evidence_subgraph import (
    _content_to_text,
    _doc_field,
    _format_documents,
    extract_documents_from_search_evidence,
)
from agent_runtime.streaming_trace import emit_trace_event

ALLOWED_ACTIONS = ("search", "analyze", "code", "done")
DEFAULT_MAX_STEPS = 8

# Injected callables
DecideFn = Callable[["SupervisorState", Dict[str, Any]], str]    # (state, distilled) -> action
SearchFn = Callable[[str, "SupervisorState"], List[Any]]          # (query, state) -> documents
AnalyzeFn = Callable[[str, List[Any], "SupervisorState"], Any]    # (query, evidence, state) -> analysis_results
CodeFn = Callable[[str, List[Any], "SupervisorState"], Any]       # (query, evidence, state) -> code_result
# (query, evidence, analysis_results, code_result, chat_history) -> answer
SynthesizeFn = Callable[[str, List[Any], Any, Any, Optional[List[Any]]], str]

ANALYSIS_WORKFLOW_PROMPT = (
    "You are AnalysisAgent. Execute the geospatial / data ANALYSIS WORKFLOW the user "
    "needs using the available tools (QGIS/PyQGIS, spatial operations, statistics). "
    "Actually CALL the tools to compute results — do not merely describe them. Use the "
    "provided evidence for context. Report the concrete results/artifacts you produced; "
    "a separate step composes the final user-facing answer.\n"
    "If you need evidence from the knowledge base, prior results, or another capability "
    "before you can run the analysis, call request_capability(capability=..., reason=...) "
    "instead of guessing — the supervisor will fulfill the request and re-run you.\n"
    "If an execute_code tool is available, you may use it to run computational steps and "
    "verify results."
)

CODE_PEER_PROMPT = (
    "You are CodeAgent. Produce practical, runnable code with a short `Dependencies:` "
    "section. Ground domain facts only on the provided evidence; do not invent APIs or "
    "sources.\n"
    "If an execute_code tool is available, RUN and DEBUG your code with it: execute the "
    "code, read stdout/stderr, fix any errors, and re-run until it works — then report the "
    "final working code and its output. If your code needs third-party packages, pass them "
    "via execute_code's `dependencies` argument (e.g. dependencies=[\"numpy\",\"pandas\"]); "
    "they are installed before the code runs.\n"
    "If you need evidence from the knowledge base, prior analysis results, or another "
    "capability before you can write correct code, call request_capability(capability=..., "
    "reason=...) instead of guessing — the supervisor will fulfill it and re-run you. "
    "If evidence is insufficient, say what is missing."
)


class SupervisorState(TypedDict, total=False):
    query: str
    chat_history: List[Any]
    thread_id: Optional[str]
    evidence: List[Any]            # accumulated, dedup'd documents (shared, heavy)
    analysis_results: Any          # outputs of the analysis workflow
    code_result: Any
    answer: str
    audit: Dict[str, Any]
    needs: List[Dict[str, Any]]    # queue of capability requests from peers (FIFO)
    actions: List[str]             # supervisor decision history
    next_action: str
    step: int
    max_steps: int
    final_answer: str
    distilled: Dict[str, Any]


def is_supervisor_enabled() -> bool:
    """Whether the orchestrate path should use the supervisor-over-peers graph.

    Default **on**; set ``AGENT_SUPERVISOR`` to a falsy value (0/false/no/off) to
    fall back to the legacy agents-as-tools orchestrator.
    """
    return (os.getenv("AGENT_SUPERVISOR") or "").strip().lower() not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Shared-state helpers
# ---------------------------------------------------------------------------

def _merge_dedup(existing: List[Any], new: List[Any]) -> List[Any]:
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
    """Compact progress view for the supervisor (no heavy documents)."""
    docs = state.get("evidence") or []
    audit = state.get("audit") or {}
    return {
        "has_evidence": bool(docs),
        "document_count": len(docs),
        "has_analysis": state.get("analysis_results") is not None,
        "has_code": state.get("code_result") is not None,
        "has_answer": bool((state.get("answer") or "").strip()),
        "audit_severity": audit.get("severity"),
        "pending_needs": [n.get("capability") for n in (state.get("needs") or []) if isinstance(n, dict)],
        "actions_taken": list(state.get("actions") or []),
    }


_CAPABILITIES = ("search", "analyze", "code")


def _extract_needs(result: Any):
    """Split a worker result into ``(clean_result, [request, ...])``.

    A worker signals what it needs by returning a dict containing a ``needs`` key —
    a list of capability names (``"search"``/``"analyze"``/``"code"``) or
    ``{"capability", "reason"}`` dicts it wants fulfilled before its work completes.
    """
    if isinstance(result, dict) and result.get("needs"):
        raw = result.get("needs") or []
        clean = {k: v for k, v in result.items() if k != "needs"}
        norm: List[Dict[str, Any]] = []
        for n in raw:
            if isinstance(n, str) and n in _CAPABILITIES:
                norm.append({"capability": n, "reason": ""})
            elif isinstance(n, dict) and n.get("capability") in _CAPABILITIES:
                norm.append({"capability": n["capability"], "reason": str(n.get("reason") or "")})
        return clean, norm
    return result, []


def _enqueue_needs(existing: Optional[List[Dict[str, Any]]], raw_needs: List[Dict[str, Any]], requester: str):
    """Append the requested capabilities + a re-run of the requester to the queue."""
    if not raw_needs:
        return None
    queue = [{**n, "by": requester} for n in raw_needs]
    queue.append({"capability": requester, "reason": "re-run after needs met", "by": requester})
    return [*(existing or []), *queue]


def _make_request_tool():
    """A `request_capability` tool an agent can call to signal what it needs.

    Returns ``(tool, requests)`` where ``requests`` accumulates the agent's calls.
    A tool call is structured LLM output, so this makes the "needs" signal
    model-driven — the agent decides, mid-reasoning, that it needs another peer.
    """
    from langchain_core.tools import StructuredTool

    requests: List[Dict[str, str]] = []

    def request_capability(capability: str, reason: str = "") -> str:
        cap = (capability or "").strip().lower()
        if cap in _CAPABILITIES:
            requests.append({"capability": cap, "reason": reason or ""})
            return (
                f"Recorded request for '{cap}'. The supervisor will fulfill it and re-run "
                "you afterward; stop now and do not guess the missing information."
            )
        return f"Ignored: '{capability}' is not a known capability (search/analyze/code)."

    tool = StructuredTool.from_function(
        func=request_capability,
        name="request_capability",
        description=(
            "Request another capability (search/analyze/code) when you cannot complete your "
            "task without it — e.g. you need evidence from the knowledge base, prior analysis "
            "results, or generated code. The supervisor fulfills the request and re-runs you."
        ),
    )
    return tool, requests


def _heuristic_decision(distilled: Dict[str, Any]) -> str:
    """Fallback decider: search once if there's no evidence, then finish.

    (The LLM decider drives analyze/code; this only prevents runaway loops.)
    """
    if not distilled.get("has_evidence") and "search" not in distilled.get("actions_taken", []):
        return "search"
    return "done"


def _format_chat_history(chat_history: Optional[List[Any]], *, max_items: int = 8, max_chars: int = 4000) -> str:
    """Render recent chat history as compact 'role: content' lines for prompts."""
    if not chat_history:
        return ""
    lines: List[str] = []
    for item in list(chat_history)[-max_items:]:
        if isinstance(item, dict) and "role" in item and "content" in item:
            role, content = item.get("role"), item.get("content")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            role, content = item[0], item[1]
        else:
            role, content = "user", item
        lines.append(f"{role}: {content}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else "…" + text[-max_chars:]


def default_decide_fn(llm: Optional[Any] = None) -> DecideFn:
    """LLM-driven next-action chooser with a deterministic heuristic fallback."""

    def decide(state: SupervisorState, distilled: Dict[str, Any]) -> str:
        history = _format_chat_history(state.get("chat_history"))
        prompt = (
            "You are the orchestration supervisor for a geospatial research agent.\n"
            "Choose the SINGLE next action. Capabilities are peers you can use in any "
            "order and repeat as needed:\n"
            "- search: retrieve evidence (datasets, publications, notebooks)\n"
            "- analyze: run a GIS/data analysis workflow (spatial ops, statistics) over the evidence\n"
            "- code: produce runnable code / implementation\n"
            "- done: stop; a grounded final answer is composed automatically from the "
            "conversation + evidence + analysis results + code\n\n"
            "Use the conversation so far for context. If the request refers to something "
            "ALREADY produced earlier in the conversation (e.g. 'show me the code', 'explain "
            "that', 'what did you find'), do NOT search again — choose 'done' so the answer is "
            "composed from the conversation, unless genuinely new external information is needed.\n"
            "Peers may also REQUEST a capability they need (e.g. code needs evidence); such "
            "requests are fulfilled automatically before you are consulted again.\n\n"
            "Respond ONLY with JSON: {\"next\": \"search|analyze|code|done\", \"reason\": \"...\"}\n\n"
            + (f"Conversation so far:\n{history}\n\n" if history else "")
            + f"User request:\n{state.get('query', '')}\n\n"
            + f"Progress so far:\n{json.dumps(distilled, ensure_ascii=True)}\n"
        )
        try:
            active = llm
            if active is None:
                from agent_runtime.executor_factory import build_default_llm

                active = build_default_llm()
            raw = active.invoke(prompt) if hasattr(active, "invoke") else active(prompt)
            text = _content_to_text(raw)
            start, end = text.find("{"), text.rfind("}")
            parsed = json.loads(text[start : end + 1]) if start != -1 else {}
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


def default_analyze_fn(*, llm: Optional[Any] = None, include_mcp_tools: bool = True,
                       mcp_modules: Optional[List[str]] = None,
                       skill_roots: Optional[List[str]] = None,
                       code_exec: Optional[bool] = None) -> AnalyzeFn:
    """Run the GIS/data analysis workflow (QGIS + spatial-analysis MCP tools)."""

    def fn(query: str, evidence: List[Any], state: SupervisorState) -> Any:
        from agent_runtime.executor_factory import (
            agent_config,
            build_agent_executor,
            child_thread_id,
            invoke_agent_with_payload_fallback,
        )
        from agent_runtime.langchain_granular_tools import make_langchain_qgis_tools
        from agent_runtime.runtime_utils import extract_final_answer, extract_search_artifacts

        thread_id = state.get("thread_id")
        request_tool, requests = _make_request_tool()
        tools = list(make_langchain_qgis_tools(session_id=child_thread_id(thread_id, "analysis_qgis")))
        if include_mcp_tools:
            from agent_runtime.langchain_mcp_tools import make_langchain_mcp_tools

            tools.extend(make_langchain_mcp_tools(include_modules=mcp_modules or ["spatial_analysis_tools"]))
        tools.append(request_tool)
        from agent_runtime.code_execution import is_code_exec_enabled

        if code_exec if code_exec is not None else is_code_exec_enabled():
            from agent_runtime.langchain_exec_tools import make_code_execution_tools

            tools.extend(make_code_execution_tools())
        executor = build_agent_executor(
            llm=llm, preloaded_tools=tools, system_prompt_override=ANALYSIS_WORKFLOW_PROMPT,
            agent_name="analysis_agent", skill_roots=skill_roots,
        )
        q = query
        if evidence:
            q = f"{query}\n\nContext evidence:\n{_format_documents(evidence)}"
        resp = invoke_agent_with_payload_fallback(
            executor, query=q, chat_history=state.get("chat_history"),
            config=agent_config(child_thread_id(thread_id, "analysis")),
        )
        artifacts = extract_search_artifacts(resp)
        result: Dict[str, Any] = {
            "summary": extract_final_answer(resp) or "",
            "tool_calls": artifacts.get("tool_calls") or [],
            "tool_results": artifacts.get("tool_results") or [],
        }
        caps = list(dict.fromkeys(r["capability"] for r in requests))
        if caps:
            result["needs"] = caps  # model-driven request(s)
        return result

    return fn


def default_code_fn(*, llm: Optional[Any] = None, skill_roots: Optional[List[str]] = None,
                    code_exec: Optional[bool] = None) -> CodeFn:
    """Code peer: writes code, and can request_capability(search/analyze) when it
    lacks the context to do so (model-driven — no nested search tool)."""

    def fn(query: str, evidence: List[Any], state: "SupervisorState") -> Any:
        from agent_runtime.executor_factory import (
            agent_config,
            build_agent_executor,
            child_thread_id,
            invoke_agent_with_payload_fallback,
        )
        from agent_runtime.runtime_utils import extract_final_answer
        from agent_runtime.skills import make_skill_tools

        request_tool, requests = _make_request_tool()
        tools = [*make_skill_tools(skill_roots=skill_roots), request_tool]
        from agent_runtime.code_execution import is_code_exec_enabled

        if code_exec if code_exec is not None else is_code_exec_enabled():
            from agent_runtime.langchain_exec_tools import make_code_execution_tools

            tools.extend(make_code_execution_tools())
        executor = build_agent_executor(
            llm=llm, preloaded_tools=tools, system_prompt_override=CODE_PEER_PROMPT,
            agent_name="code_agent", skill_roots=skill_roots,
        )
        parts = [query]
        if evidence:
            parts.append(f"Evidence:\n{_format_documents(evidence)}")
        if state.get("analysis_results"):
            parts.append(
                f"Analysis results:\n{json.dumps(state['analysis_results'], ensure_ascii=True, default=str)[:1500]}"
            )
        resp = invoke_agent_with_payload_fallback(
            executor, query="\n\n".join(parts), chat_history=state.get("chat_history"),
            config=agent_config(child_thread_id(state.get("thread_id"), "code")),
        )
        result: Dict[str, Any] = {"answer": extract_final_answer(resp) or "", "code_result": resp}
        caps = list(dict.fromkeys(r["capability"] for r in requests))
        if caps:
            result["needs"] = caps  # model-driven request(s)
        return result

    return fn


def default_synthesize_fn(llm: Optional[Any] = None) -> SynthesizeFn:
    """Compose the final grounded answer in the original AnalysisAgent format."""

    def fn(query: str, evidence: List[Any], analysis_results: Any, code_result: Any,
           chat_history: Optional[List[Any]] = None) -> str:
        from agent_runtime.executor_factory import ANALYSIS_AGENT_PROMPT

        active = llm
        if active is None:
            from agent_runtime.executor_factory import build_default_llm

            active = build_default_llm()
        parts = [
            ANALYSIS_AGENT_PROMPT,
            "(Compose the final answer from the materials below — including the conversation "
            "so far — and do not call tools.)",
        ]
        history = _format_chat_history(chat_history)
        if history:
            parts.append(f"Conversation so far:\n{history}")
        parts.append(f"Question:\n{query}")
        parts.append(f"Evidence:\n{_format_documents(evidence)}")
        if analysis_results:
            parts.append(f"Analysis results:\n{json.dumps(analysis_results, ensure_ascii=True, default=str)[:2000]}")
        if code_result:
            parts.append(f"Code result:\n{json.dumps(code_result, ensure_ascii=True, default=str)[:2000]}")
        prompt = "\n\n".join(parts)
        if hasattr(active, "invoke"):
            return _content_to_text(active.invoke(prompt))
        if callable(active):
            return str(active(prompt))
        raise TypeError("llm must expose .invoke() or be a str->str callable")

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
    synthesize_fn: Optional[SynthesizeFn] = None,
    llm: Optional[Any] = None,
    top_k: int = 5,
    do_rerank: bool = True,
    do_audit: bool = True,
) -> Any:
    """Compile the supervisor-over-peers graph. Workers default to existing agents."""
    decide = decide_fn or default_decide_fn(llm=llm)
    do_search = search_fn or default_search_fn(llm=llm)
    do_analyze = analyze_fn or default_analyze_fn(llm=llm)
    do_code = code_fn or default_code_fn(llm=llm)
    do_synthesize = synthesize_fn or default_synthesize_fn(llm=llm)

    def supervisor_node(state: SupervisorState) -> Dict[str, Any]:
        step = state.get("step", 0)
        needs = list(state.get("needs") or [])
        if step >= state.get("max_steps", DEFAULT_MAX_STEPS):
            nxt, remaining, why = "done", [], "max_steps"
        elif needs:
            # Fulfill the oldest peer request first (FIFO), then continue the loop.
            req = needs[0]
            cap = req.get("capability")
            nxt = cap if cap in _CAPABILITIES else "done"
            remaining, why = needs[1:], f"request by {req.get('by')}"
        else:
            nxt = decide(state, _distill(state))
            if nxt not in ALLOWED_ACTIONS:
                nxt = "done"
            remaining, why = needs, "decision"
        emit_trace_event(
            "node_completed",
            {"stage": "supervisor", "route": nxt, "message": f"supervisor → {nxt} ({why})"},
            node="supervisor",
        )
        return {
            "next_action": nxt,
            "actions": [*(state.get("actions") or []), nxt],
            "step": step + 1,
            "needs": remaining,
        }

    def search_node(state: SupervisorState) -> Dict[str, Any]:
        q = state.get("query", "")
        emit_trace_event("node_started", {"stage": "search", "message": "Searching"}, node="search")
        raw = do_search(q, state) or []
        if isinstance(raw, dict):
            docs = raw.get("documents") or []
            _, needs = _extract_needs(raw)
        else:
            docs, needs = raw, []
        if do_rerank and len(docs) > 1:
            docs = rerank_documents(q, docs, top_k=top_k, llm=llm)  # operator bundled into search
        merged = _merge_dedup(state.get("evidence") or [], docs)
        emit_trace_event(
            "node_completed", {"stage": "search", "message": f"{len(merged)} docs in evidence"}, node="search"
        )
        update: Dict[str, Any] = {"evidence": merged}
        enq = _enqueue_needs(state.get("needs"), needs, "search")
        if enq is not None:
            update["needs"] = enq
        return update

    def analysis_node(state: SupervisorState) -> Dict[str, Any]:
        q = state.get("query", "")
        emit_trace_event("node_started", {"stage": "analyze", "message": "Running analysis workflow"}, node="analyze")
        clean, needs = _extract_needs(do_analyze(q, state.get("evidence") or [], state))
        emit_trace_event("node_completed", {"stage": "analyze", "message": "Analysis workflow complete"}, node="analyze")
        update: Dict[str, Any] = {"analysis_results": clean}
        enq = _enqueue_needs(state.get("needs"), needs, "analyze")
        if enq is not None:
            update["needs"] = enq
        return update

    def code_node(state: SupervisorState) -> Dict[str, Any]:
        q = state.get("query", "")
        emit_trace_event("node_started", {"stage": "code", "message": "Generating code"}, node="code")
        clean, needs = _extract_needs(do_code(q, state.get("evidence") or [], state))
        emit_trace_event("node_completed", {"stage": "code", "message": "Code ready"}, node="code")
        update: Dict[str, Any] = {"code_result": clean}
        enq = _enqueue_needs(state.get("needs"), needs, "code")
        if enq is not None:
            update["needs"] = enq
        return update

    def synthesize_node(state: SupervisorState) -> Dict[str, Any]:
        q = state.get("query", "")
        evidence = state.get("evidence") or []
        emit_trace_event("node_started", {"stage": "synthesize", "message": "Composing answer"}, node="synthesize")
        answer = do_synthesize(q, evidence, state.get("analysis_results"), state.get("code_result"), state.get("chat_history"))
        audit = audit_answer_grounding(q, answer, evidence, llm=llm) if (do_audit and (answer or "").strip()) else {}
        emit_trace_event(
            "node_completed",
            {"stage": "synthesize", "message": audit.get("summary") or "Answer composed"},
            node="synthesize",
        )
        merged = {**state, "answer": answer, "audit": audit}
        return {"answer": answer, "final_answer": answer, "audit": audit, "distilled": {**_distill(merged), "answer": answer}}

    builder = StateGraph(SupervisorState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("search", search_node)
    builder.add_node("analyze", analysis_node)
    builder.add_node("code", code_node)
    builder.add_node("synthesize", synthesize_node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda s: s.get("next_action", "done"),
        {"search": "search", "analyze": "analyze", "code": "code", "done": "synthesize"},
    )
    # Peers loop back to the supervisor (restores dynamic ordering / multi-hop).
    builder.add_edge("search", "supervisor")
    builder.add_edge("analyze", "supervisor")
    builder.add_edge("code", "supervisor")
    builder.add_edge("synthesize", END)
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
            "needs": [],
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
    "default_analyze_fn",
    "default_code_fn",
    "default_synthesize_fn",
]
