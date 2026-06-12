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

from agent_runtime.evidence_quality import _extract_json_object, audit_answer_grounding, rerank_documents
from agent_runtime.evidence_subgraph import (
    _content_to_text,
    _doc_field,
    _format_documents,
    extract_documents_from_search_evidence,
)
from agent_runtime.streaming_trace import emit_trace_event

ALLOWED_ACTIONS = ("search", "analyze", "code", "done")
DEFAULT_MAX_STEPS = 8


def _max_searches() -> int:
    """Hard cap on how many times the search peer may run per request."""
    try:
        return max(1, int(os.getenv("AGENT_SUPERVISOR_MAX_SEARCHES", "2")))
    except (TypeError, ValueError):
        return 2


def _max_peer_runs() -> int:
    """Cap on how many times any single peer may run (bounds needs-driven re-run loops)."""
    try:
        return max(1, int(os.getenv("AGENT_SUPERVISOR_MAX_PEER_RUNS", "3")))
    except (TypeError, ValueError):
        return 3


def _search_exhausted(state: "SupervisorState") -> bool:
    """Whether further searching is pointless: it hit the attempt cap, or the most
    recent search returned NO new evidence. Stops the supervisor (and peer 'needs')
    from hammering the search agent when the knowledge base has nothing to return."""
    if state.get("search_attempts", 0) >= _max_searches():
        return True
    return state.get("search_empty_streak", 0) >= 1

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
    "verify results. To read an UPLOADED file inside execute_code, pass its file_id(s) in "
    "the `input_files` argument; the file is then available in the working directory under "
    "both its file_id and its original filename.\n"
    "For an UPLOADED vector dataset or shapefile (e.g. Census TIGER/Line), use the geo "
    "tools: inspect_vector to read CRS/extent/columns/feature-count, plot_vector to "
    "visualize a map, reproject_vector / vector_spatial_join / vector_to_geojson to "
    "analyze and export. A TIGER .zip is read directly by file_id; an EXTRACTED shapefile "
    "is several files (.shp/.shx/.dbf/.prj) — just pass the .shp's file_id (or any one "
    "component); the tool auto-finds the rest among the attached files."
)

CODE_PEER_PROMPT = (
    "You are CodeAgent. Produce practical, runnable code with a short `Dependencies:` "
    "section. Ground domain facts only on the provided evidence; do not invent APIs or "
    "sources.\n"
    "If an execute_code tool is available, RUN and DEBUG your code with it: execute the "
    "code, read stdout/stderr, fix any errors, and re-run until it works — then report the "
    "final working code and its output. If your code needs third-party packages, pass them "
    "via execute_code's `dependencies` argument (e.g. dependencies=[\"numpy\",\"pandas\"]); "
    "they are installed before the code runs. If your code reads an UPLOADED file, pass its "
    "file_id(s) in execute_code's `input_files` argument (e.g. input_files=[\"file_1a2b3c\"]); "
    "the file is then available in the working directory under both its file_id and its "
    "original filename.\n"
    "When you produce a plot/figure, SAVE it to a file with matplotlib "
    "`plt.savefig('result.png', bbox_inches='tight')` — the headless sandbox cannot "
    "display windows, so do NOT rely on `plt.show()`; the saved image is returned as a "
    "downloadable artifact.\n"
    "For an UPLOADED vector dataset / shapefile (e.g. Census TIGER), call inspect_vector "
    "first to learn its CRS, columns, and geometry type before writing code, and use "
    "plot_vector / vector_to_geojson when a map or export is enough. For an EXTRACTED "
    "shapefile (.shp/.shx/.dbf/.prj uploaded separately) just pass the .shp's file_id (or "
    "any one component) — the tool auto-finds the rest among the attached files. If you read "
    "it in execute_code instead, geopandas needs the whole shapefile set — prefer a .zip via "
    "input_files, and include geopandas in `dependencies`.\n"
    "When the evidence references ingested knowledge-base blocks (by doc_id), call "
    "get_kb_block(doc_id) to read the FULL source of a referenced function/notebook and "
    "REUSE it verbatim — including real data-loading URLs/APIs — instead of stubbing "
    "loaders or inventing local file paths. You may also agent_kb_search for more.\n"
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
    search_attempts: int           # how many times the search peer has run
    search_empty_streak: int       # consecutive searches that added NO new evidence


def is_supervisor_enabled() -> bool:
    """Whether the orchestrate path should use the supervisor-over-peers graph.

    Default **on**; set ``AGENT_SUPERVISOR`` to a falsy value (0/false/no/off) to
    fall back to the legacy agents-as-tools orchestrator.
    """
    return (os.getenv("AGENT_SUPERVISOR") or "").strip().lower() not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Shared-state helpers
# ---------------------------------------------------------------------------

def _doc_key(doc: Any) -> str:
    """Stable dedup key for a document.

    Uses an explicit id when present; otherwise falls back to a content hash
    (title + contents) so ID-less documents from the multi-pass search the design
    encourages still dedup against each other — the previous positional fallbacks
    (``_{i}`` vs ``new-{j}``) lived in disjoint namespaces and could never collide.
    """
    ident = _doc_field(doc, "doc_id", "id", "_id", default="")
    if ident:
        return f"id:{ident}"
    import hashlib

    title = _doc_field(doc, "title", default="")
    contents = _doc_field(doc, "contents", "content", "text", "summary", default="")
    digest = hashlib.sha1(f"{title}\n{contents}".strip().encode("utf-8", "replace")).hexdigest()
    return f"c:{digest}"


def _merge_dedup(existing: List[Any], new: List[Any]) -> List[Any]:
    merged = list(existing or [])
    seen = {_doc_key(d) for d in merged}
    for d in new or []:
        key = _doc_key(d)
        if key in seen:
            continue
        seen.add(key)
        merged.append(d)
    return merged


def _distill(state: SupervisorState) -> Dict[str, Any]:
    """Compact progress view for the supervisor (no heavy documents)."""
    docs = state.get("evidence") or []
    audit = state.get("audit") or {}
    actions = list(state.get("actions") or [])
    return {
        "has_evidence": bool(docs),
        "document_count": len(docs),
        "has_analysis": state.get("analysis_results") is not None,
        "has_code": state.get("code_result") is not None,
        "has_answer": bool((state.get("answer") or "").strip()),
        "audit_severity": audit.get("severity"),
        "pending_needs": [n.get("capability") for n in (state.get("needs") or []) if isinstance(n, dict)],
        "actions_taken": actions,
        # How many times each peer has already run — so the decider can see (and
        # avoid) unproductive repetition.
        "action_counts": {c: actions.count(c) for c in ("search", "analyze", "code") if actions.count(c)},
        "search_attempts": state.get("search_attempts", 0),
        # True once the knowledge base has nothing more to give (cap hit, or the last
        # search returned no new evidence) — the decider must NOT search again.
        "search_exhausted": _search_exhausted(state),
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


def _is_unproductive_repeat(nxt: str, state: SupervisorState) -> bool:
    """True if *nxt* re-runs the peer that JUST ran and already produced a result,
    with no pending need driving it.

    Applies to ``analyze`` / ``code``: each overwrites a single result slot and
    iterates internally (the code peer runs+debugs its own code), so re-running it
    back-to-back with the same inputs just reproduces the same result — the
    signature of a decision loop. ``search`` is intentionally NOT guarded: it
    *accumulates* (dedup-merges) into evidence, so a follow-up search can add new
    documents. A genuine multi-hop refinement interleaves a *different* peer (or a
    request_capability need), so only consecutive same-peer repeats are blocked.
    """
    actions = state.get("actions") or []
    if not actions or actions[-1] != nxt:
        return False  # not a back-to-back repeat
    if nxt == "code":
        return state.get("code_result") is not None
    if nxt == "analyze":
        return state.get("analysis_results") is not None
    return False


# Severities at/above which the grounding audit appends a user-visible caveat.
_AUDIT_FLAG_SEVERITIES = {"medium", "high"}


def _audit_flagged(audit: Optional[Dict[str, Any]]) -> bool:
    """Whether the grounding audit found a problem worth warning the user about."""
    if not audit:
        return False
    severity = str(audit.get("severity") or "").strip().lower()
    return bool(audit.get("hallucination_detected")) or severity in _AUDIT_FLAG_SEVERITIES


def _apply_grounding_caveat(answer: str, audit: Optional[Dict[str, Any]]) -> str:
    """Append a clearly-marked grounding caveat to *answer* when the audit flags it.

    This is what makes the grounding audit non-cosmetic: a flagged verdict changes
    the text the user actually sees, rather than being computed and discarded.
    """
    if not _audit_flagged(audit):
        return answer
    severity = str((audit or {}).get("severity") or "").strip().lower()
    summary = str((audit or {}).get("summary") or "").strip()
    note = (
        "⚠️ Grounding check: parts of this answer may not be fully supported by the "
        "retrieved evidence"
    )
    if severity:
        note += f" (severity: {severity})"
    note += f". {summary}" if summary else "."
    return f"{answer}\n\n---\n\n{note}" if (answer or "").strip() else note


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
            "Each peer ITERATES INTERNALLY (the code peer runs AND debugs its own code; search "
            "issues multiple queries in one pass). So once a peer has produced its result "
            "(see has_code / has_analysis / has_evidence and action_counts in Progress), do NOT "
            "pick it again to 'retry' or 'improve' — that just repeats work. Choose 'done' once "
            "the request is covered; the final answer is composed automatically. Only pick a peer "
            "again if you genuinely need NEW work it has not done yet.\n"
            "If 'search_exhausted' is true in Progress, the knowledge base returned nothing new — "
            "do NOT choose 'search' again. Proceed with analyze/code (which can work on uploaded "
            "files and prior results) or choose 'done'.\n"
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
            # Reuse the fenced-block-aware extractor (handles ```json fences and
            # prose around the object) instead of naive first-{/last-} slicing.
            parsed = _extract_json_object(text)
            nxt = str((parsed or {}).get("next") or "").strip().lower() if isinstance(parsed, dict) else ""
            if nxt in ALLOWED_ACTIONS:
                return nxt
        except Exception:
            pass
        # The decider output was unusable; fall back to the deterministic heuristic.
        # Emit a (detail-tier) marker so this degraded path is distinguishable from a
        # genuine LLM "done" in the trace.
        emit_trace_event(
            "decider_fallback",
            {"stage": "supervisor", "message": "decider output not parseable; used heuristic fallback"},
            node="supervisor",
        )
        return _heuristic_decision(distilled)

    return decide


# ---------------------------------------------------------------------------
# Default worker adapters (best-effort; wire to existing agents — need live validation)
# ---------------------------------------------------------------------------

def _as_retrieval_request(query: str) -> str:
    """Reframe the (possibly action-shaped) user query as a RETRIEVAL task for the
    search peer, so it gathers evidence instead of trying to perform the task itself
    (which makes capable models loop). The original query is still used for citation."""
    return (
        "Retrieve relevant evidence for the request below. Use the search tools to "
        "gather documents/code, then STOP and return the evidence — do NOT perform "
        "the task, write code, or produce the final answer yourself.\n\n"
        f"Request: {query}"
    )


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
            mcp_modules=mcp_modules, enabled_search_methods=enabled_search_methods,
            skill_roots=skill_roots,
        )
        resp = invoke_agent_with_payload_fallback(
            executor, query=_as_retrieval_request(query), chat_history=None,
            config=agent_config(child_thread_id(state.get("thread_id"), "sup_search")),
        )
        return extract_documents_from_search_evidence(build_search_evidence_payload(query, resp, None))

    return fn


def default_analyze_fn(*, llm: Optional[Any] = None, include_mcp_tools: bool = True,
                       mcp_modules: Optional[List[str]] = None,
                       skill_roots: Optional[List[str]] = None,
                       code_exec: Optional[bool] = None,
                       input_file_ids: Optional[List[str]] = None) -> AnalyzeFn:
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
        # Geospatial KB tools: run extracted spatial functions + chain GIS ops by file_id
        # (the GIS runs as executed tool steps; GeoDataFrames pass as files, not in memory).
        try:
            from extractors.geo_handles import make_geo_analysis_tools
            tools.extend(make_geo_analysis_tools())
        except Exception:
            pass
        tools.append(request_tool)
        # When files are attached to the conversation, let the analysis peer inspect
        # them directly (read_text_file / inspect_file_for_analysis) instead of only
        # being able to touch them via execute_code.
        if input_file_ids:
            from agent_runtime.langchain_file_tools import make_langchain_file_tools

            tools.extend(make_langchain_file_tools())
            # Vector / shapefile tools (read + visualize + analyze uploaded TIGER files,
            # zip or extracted). Guarded so a missing geopandas never breaks the agent.
            try:
                from agent_runtime.langchain_geo_tools import make_langchain_geo_tools
                tools.extend(make_langchain_geo_tools(default_input_file_ids=input_file_ids))
            except Exception:
                pass
        from agent_runtime.code_execution import is_code_exec_enabled

        if code_exec if code_exec is not None else is_code_exec_enabled():
            from agent_runtime.langchain_exec_tools import make_code_execution_tools

            tools.extend(make_code_execution_tools(default_input_file_ids=input_file_ids))
        executor = build_agent_executor(
            llm=llm, preloaded_tools=tools, system_prompt_override=ANALYSIS_WORKFLOW_PROMPT,
            agent_name="analysis_agent", skill_roots=skill_roots,
        )
        q = query
        if evidence:
            q = f"{query}\n\nContext evidence:\n{_format_documents(evidence)}"
        # Cross-turn continuity comes from this peer's own checkpointed child
        # thread (and the supervisor's chat_history drives routing/synthesis), so
        # we do NOT re-feed chat_history here — that would replay prior turns twice
        # on re-runs. Mirrors the search peer.
        resp = invoke_agent_with_payload_fallback(
            executor, query=q, chat_history=None,
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
                    code_exec: Optional[bool] = None,
                    input_file_ids: Optional[List[str]] = None) -> CodeFn:
    """Code peer: writes code, and can request_capability(search/analyze) when it
    lacks the context to do so (model-driven — no nested search tool)."""

    def fn(query: str, evidence: List[Any], state: "SupervisorState") -> Any:
        from agent_runtime.executor_factory import (
            agent_config,
            build_agent_executor,
            child_thread_id,
            invoke_agent_with_payload_fallback,
        )
        from agent_runtime.runtime_utils import extract_final_answer, extract_search_artifacts
        from agent_runtime.skills import make_skill_tools

        request_tool, requests = _make_request_tool()
        tools = [*make_skill_tools(skill_roots=skill_roots), request_tool]
        # KB read tools so the code peer can pull the FULL source of referenced blocks
        # (get_kb_block) and reuse it verbatim instead of stubbing loaders.
        try:
            from agent_runtime.langchain_granular_tools import make_langchain_granular_tools
            tools.extend(t for t in make_langchain_granular_tools(
                enabled_search_methods=["agent_kb_search", "get_kb_block"])
                if getattr(t, "name", "") in {"agent_kb_search", "get_kb_block"})
        except Exception:
            pass
        # When files are attached, give the code peer the vector/shapefile tools too, so it
        # can inspect an uploaded TIGER shapefile's schema/CRS before writing code (and
        # plot/convert/reproject without round-tripping through the sandbox).
        if input_file_ids:
            try:
                from agent_runtime.langchain_geo_tools import make_langchain_geo_tools
                tools.extend(make_langchain_geo_tools(default_input_file_ids=input_file_ids))
            except Exception:
                pass
        from agent_runtime.code_execution import is_code_exec_enabled

        if code_exec if code_exec is not None else is_code_exec_enabled():
            from agent_runtime.langchain_exec_tools import make_code_execution_tools

            tools.extend(make_code_execution_tools(default_input_file_ids=input_file_ids))
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
        # See analyze peer: continuity is owned by this peer's checkpointed thread,
        # so chat_history is not re-fed here (avoids double-replay on re-runs).
        resp = invoke_agent_with_payload_fallback(
            executor, query="\n\n".join(parts), chat_history=None,
            config=agent_config(child_thread_id(state.get("thread_id"), "code")),
        )
        # Flat result: the human-readable answer + a compact artifacts extract.
        # Do NOT nest the whole raw response object (it would crowd out / truncate
        # the real code+output when synthesis serializes code_result).
        artifacts = extract_search_artifacts(resp)
        result: Dict[str, Any] = {
            "answer": extract_final_answer(resp) or "",
            "tool_calls": artifacts.get("tool_calls") or [],
            "tool_results": artifacts.get("tool_results") or [],
        }
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
            # Prefer the code peer's human-readable answer; only fall back to a
            # serialized dump if no answer text is present (keeps the real code /
            # output from being truncated away by a large nested object).
            if isinstance(code_result, dict) and str(code_result.get("answer") or "").strip():
                parts.append(f"Code result:\n{str(code_result['answer'])[:2000]}")
            else:
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
        actions = state.get("actions") or []
        needs = list(state.get("needs") or [])
        # Drop peer requests that can no longer be productive, so a peer that keeps
        # asking for the same dead capability can't loop us:
        #  - 'search' once it is exhausted (cap hit / last search returned nothing)
        #  - ANY capability already run the max number of times
        def _dead(n):
            cap = n.get("capability")
            if cap == "search" and _search_exhausted(state):
                return True
            return actions.count(cap) >= _max_peer_runs()
        needs = [n for n in needs if not _dead(n)]

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
            # Backstop: a peer that just ran and already produced its result should
            # not be re-run back-to-back (it self-iterates internally). Prevents the
            # decider from looping on the same action until max_steps.
            if nxt != "done" and _is_unproductive_repeat(nxt, state):
                nxt, why = "done", f"no-progress repeat ({nxt})"
            # Don't keep hitting the search agent once the KB has nothing left to give.
            elif nxt == "search" and _search_exhausted(state):
                nxt, why = "done", "search exhausted"
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
        before = len(state.get("evidence") or [])
        merged = _merge_dedup(state.get("evidence") or [], docs)
        added = len(merged) - before
        emit_trace_event(
            "node_completed", {"stage": "search", "message": f"{len(merged)} docs in evidence"}, node="search"
        )
        # Track productivity so the supervisor stops searching when it adds nothing.
        prev_streak = state.get("search_empty_streak", 0)
        update: Dict[str, Any] = {
            "evidence": merged,
            "search_attempts": state.get("search_attempts", 0) + 1,
            "search_empty_streak": 0 if added > 0 else prev_streak + 1,
        }
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
        # Act on the verdict: a flagged audit appends a user-visible caveat to the
        # answer (so an ungrounded answer never ships silently).
        final = _apply_grounding_caveat(answer, audit)
        # Surface the verdict as its own event so clients can show grounded/flagged.
        if audit:
            emit_trace_event(
                "grounding_audit",
                {
                    "stage": "grounding_audit",
                    "flagged": _audit_flagged(audit),
                    "hallucination_detected": bool(audit.get("hallucination_detected")),
                    "severity": audit.get("severity"),
                    "issues": audit.get("issues") or [],
                    "message": audit.get("summary") or "Grounding audit complete",
                },
                node="synthesize",
            )
        emit_trace_event(
            "node_completed",
            {"stage": "synthesize", "message": audit.get("summary") or "Answer composed"},
            node="synthesize",
        )
        merged = {**state, "answer": final, "audit": audit}
        return {"answer": final, "final_answer": final, "audit": audit, "distilled": {**_distill(merged), "answer": final}}

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
