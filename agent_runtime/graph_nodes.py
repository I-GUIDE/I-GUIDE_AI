"""Dynamic tool factories for the orchestrator agent.

Each ``_make_*_tool`` function returns a LangChain ``StructuredTool`` that,
when called by the orchestrator, spins up a sub-agent internally.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from agent_runtime.executor_factory import (
    ANALYSIS_AGENT_PROMPT,
    DEFAULT_CHECKPOINTER,
    agent_config,
    build_agent_executor,
    build_code_agent_executor,
    build_default_llm,
    build_orchestrator_agent_executor,
    build_search_agent_executor,
    child_thread_id,
    invoke_agent_with_payload_fallback,
    resolve_thread_id,
)
from agent_runtime.intent_classifier import (
    build_route_trace as _build_route_trace_impl,
    chat_history_preview,
    extract_json_object,
    query_has_file_context,
)
from agent_runtime.runtime_utils import (
    build_orchestration_trace,
    build_search_evidence_payload,
    extract_final_answer,
    extract_search_artifacts,
)
from agent_runtime.tool_policy import (
    collect_tools,
    select_allowed_tools,
)
from agent_runtime.skills import make_skill_tools


# ---------------------------------------------------------------------------
# Internal helper — wraps build_route_trace with dependency injection
# ---------------------------------------------------------------------------

def _build_route_trace(
    query: str,
    available_tool_names: Sequence[str],
    available_routes: Sequence[Dict[str, Any]],
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    forced_intent: Optional[str] = None,
) -> Dict[str, Any]:
    return _build_route_trace_impl(
        query,
        available_tool_names,
        available_routes,
        chat_history=chat_history,
        llm=llm,
        forced_intent=forced_intent,
        build_default_llm=build_default_llm,
        select_allowed_tools=select_allowed_tools,
    )


# ---------------------------------------------------------------------------
# Tool factories
# ---------------------------------------------------------------------------

def make_answer_from_memory_tool(
    *,
    llm: Optional[Any],
    chat_history: Optional[List[Any]],
) -> Any:
    """Create an ``answer_from_memory`` tool backed by the LLM."""
    from langchain_core.tools import StructuredTool

    history_preview = chat_history_preview(chat_history, max_items=12)

    def answer_from_memory(query: str) -> str:
        if not history_preview:
            return json.dumps(
                {
                    "can_answer": False,
                    "answer": "",
                    "reason": "chat_history_unavailable",
                },
                ensure_ascii=True,
            )
        active_llm = llm or build_default_llm()
        prompt = (
            "You decide whether a user query can be answered directly from prior chat history.\n"
            "Return JSON only with keys: can_answer, answer, reason.\n"
            "Set can_answer=true only if the answer is directly supported by the chat history.\n"
            "If can_answer=false, leave answer empty."
        )
        payload = {
            "query": query,
            "chat_history": history_preview,
        }
        response = active_llm.invoke(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
            ]
        )
        content = getattr(response, "content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else getattr(part, "text", str(part))
                for part in content
            )
        parsed = extract_json_object(str(content)) or {}
        can_answer = bool(parsed.get("can_answer"))
        answer = str(parsed.get("answer") or "").strip()
        reason = str(parsed.get("reason") or ("supported_by_chat_history" if can_answer else "insufficient_chat_history"))
        if can_answer and answer:
            return json.dumps(
                {
                    "can_answer": True,
                    "answer": answer,
                    "reason": reason,
                    "history_items_used": len(history_preview),
                },
                ensure_ascii=True,
            )
        return json.dumps(
            {
                "can_answer": False,
                "answer": "",
                "reason": reason,
                "history_items_used": len(history_preview),
            },
            ensure_ascii=True,
        )

    return StructuredTool.from_function(
        func=answer_from_memory,
        name="answer_from_memory",
        description="Use the already-loaded chat history to answer memory-based questions directly. Returns JSON with can_answer and answer.",
    )


def make_search_agent_evidence_tool(
    *,
    llm: Optional[Any],
    verbose: bool,
    return_intermediate_steps: bool,
    tool_strategy: str,
    include_mcp_tools: bool,
    mcp_modules: Optional[List[str]],
    enabled_search_methods: Optional[List[str]],
    smart_tool_routing: bool,
    forced_intent: Optional[str],
    search_invocations: List[Dict[str, Any]],
    allow_file_tools: bool = False,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
    skill_roots: Optional[List[str]] = None,
) -> Any:
    """Create a ``search_agent_evidence`` tool that invokes SearchAgent."""
    from langchain_core.tools import StructuredTool

    def search_agent_evidence(query: str) -> str:
        invocation_index = len(search_invocations) + 1
        nested_thread_id = thread_id
        if nested_thread_id:
            nested_thread_id = f"{nested_thread_id}::search_tool::{invocation_index}"
        all_tools = collect_tools(
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            enabled_search_methods=enabled_search_methods,
            include_file_tools=allow_file_tools,
            skill_roots=skill_roots,
        )
        route_trace: Optional[Dict[str, Any]] = None
        allowed_tool_names: Optional[List[str]] = None
        if smart_tool_routing:
            available_names = [getattr(tool, "name", "") for tool in all_tools if getattr(tool, "name", "")]
            route_trace = _build_route_trace(
                query,
                available_names,
                [{"route": "search", "agent": "search_agent", "tool_names": available_names}],
                chat_history=None,
                llm=llm,
                forced_intent=forced_intent,
            )
            allowed_tool_names = route_trace.get("allowed_tools") or None

        search_executor = build_search_agent_executor(
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            allowed_tool_names=allowed_tool_names,
            preloaded_tools=all_tools,
            checkpointer=checkpointer,
            skill_roots=skill_roots,
        )
        search_response = invoke_agent_with_payload_fallback(
            search_executor,
            query=query,
            chat_history=None,
            config=agent_config(nested_thread_id),
        )
        evidence = build_search_evidence_payload(query, search_response, route_trace)
        search_invocations.append(
            {
                "query": query,
                "thread_id": nested_thread_id,
                "route_trace": route_trace,
                "search_result": search_response,
                "evidence": evidence,
            }
        )
        return json.dumps(evidence, ensure_ascii=True, default=str)

    return StructuredTool.from_function(
        func=search_agent_evidence,
        name="search_agent_evidence",
        description=(
            "Call SearchAgent to retrieve domain-specific evidence and references. "
            "Use before generating code that relies on external facts."
        ),
    )


def make_analysis_agent_answer_tool(
    *,
    llm: Optional[Any],
    verbose: bool,
    return_intermediate_steps: bool,
    chat_history: Optional[List[Any]],
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    allow_file_tools: bool = False,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
    skill_roots: Optional[List[str]] = None,
) -> Any:
    """Create an ``analysis_agent_answer`` tool that invokes AnalysisAgent."""
    from langchain_core.tools import StructuredTool
    from rag_pipeline.langchain_mcp_tools import make_langchain_mcp_tools

    # Avoid circular import — import sibling at call time
    from agent_runtime.graph_runtime import run_code_agent_query

    search_invocations: List[Dict[str, Any]] = []
    code_search_invocations: List[Dict[str, Any]] = []
    search_tool = make_search_agent_evidence_tool(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        search_invocations=search_invocations,
        allow_file_tools=allow_file_tools,
        thread_id=child_thread_id(thread_id, "analysis_search"),
        checkpointer=checkpointer,
        skill_roots=skill_roots,
    )

    def code_agent_answer(query: str, search_evidence_json: str = "") -> str:
        nested_thread_id = child_thread_id(thread_id, "code_tool")
        code_query = query
        if search_evidence_json:
            code_query = (
                f"{query}\n\n"
                "Relevant search evidence is provided below as JSON. Use it when writing code and dependencies.\n"
                f"{search_evidence_json}"
            )
        code_response = run_code_agent_query(
            code_query,
            chat_history=chat_history,
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            smart_tool_routing=smart_tool_routing,
            forced_intent=forced_intent,
            thread_id=nested_thread_id,
            checkpointer=checkpointer,
            skill_roots=skill_roots,
        )
        code_search_invocations.extend(code_response.get("code_agent_search_invocations") or [])
        return json.dumps(
            {
                "answer": code_response.get("final_answer", ""),
                "code_result": code_response.get("code_result"),
                "code_agent_search_invocations": code_response.get("code_agent_search_invocations") or [],
            },
            ensure_ascii=True,
            default=str,
        )

    code_tool = StructuredTool.from_function(
        func=code_agent_answer,
        name="code_agent_answer",
        description="Use CodeAgent to provide runnable code and a Dependencies section when analysis alone is insufficient.",
    )

    analysis_tools: List[Any] = [*make_skill_tools(skill_roots=skill_roots), search_tool, code_tool]
    if include_mcp_tools:
        analysis_tools = [
            *make_langchain_mcp_tools(include_modules=mcp_modules),
            *analysis_tools,
        ]

    analysis_executor = build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy="granular",
        include_mcp_tools=False,
        mcp_modules=None,
        preloaded_tools=analysis_tools,
        system_prompt_override=ANALYSIS_AGENT_PROMPT,
        agent_name="analysis_agent",
        checkpointer=checkpointer,
        skill_roots=skill_roots,
    )

    def analysis_agent_answer(query: str, search_evidence_json: str = "") -> str:
        nested_thread_id = child_thread_id(thread_id, "analysis_tool")
        evidence_payload = {}
        if search_evidence_json:
            try:
                evidence_payload = json.loads(search_evidence_json)
            except Exception:
                evidence_payload = {"raw_search_evidence": search_evidence_json}
        analysis_query = query
        if evidence_payload:
            analysis_query = (
                f"{query}\n\n"
                "Search evidence is provided below as JSON. Use only this evidence and the chat history.\n"
                f"{json.dumps(evidence_payload, ensure_ascii=True)}"
            )
        analysis_response = invoke_agent_with_payload_fallback(
            analysis_executor,
            query=analysis_query,
            chat_history=chat_history,
            config=agent_config(nested_thread_id),
        )
        answer = extract_final_answer(analysis_response) or ""
        return json.dumps(
            {
                "answer": answer,
                "analysis_result": analysis_response,
                "analysis_agent_search_invocations": search_invocations,
                "analysis_agent_code_search_invocations": code_search_invocations,
            },
            ensure_ascii=True,
            default=str,
        )

    return StructuredTool.from_function(
        func=analysis_agent_answer,
        name="analysis_agent_answer",
        description="Use AnalysisAgent to synthesize an answer from chat history and optional search evidence JSON.",
    )


# ---------------------------------------------------------------------------
# Orchestration tool assembly
# ---------------------------------------------------------------------------

def collect_orchestration_tools(
    *,
    query: str,
    chat_history: Optional[List[Any]],
    llm: Optional[Any],
    verbose: bool,
    return_intermediate_steps: bool,
    tool_strategy: str,
    include_mcp_tools: bool,
    mcp_modules: Optional[List[str]],
    enabled_search_methods: Optional[List[str]],
    smart_tool_routing: bool,
    forced_intent: Optional[str],
    thread_id: Optional[str],
    checkpointer: Optional[Any],
    skill_roots: Optional[List[str]] = None,
) -> List[Any]:
    """Assemble the tool set for the OrchestratorAgent."""
    from rag_pipeline.langchain_file_tools import make_langchain_file_tools

    tools: List[Any] = []
    allow_file_tools = query_has_file_context(query)
    if allow_file_tools:
        tools.extend(make_langchain_file_tools())
    tools.extend(make_skill_tools(skill_roots=skill_roots))
    if chat_history:
        tools.append(make_answer_from_memory_tool(llm=llm, chat_history=chat_history))
    tools.append(
        make_search_agent_evidence_tool(
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            enabled_search_methods=enabled_search_methods,
            smart_tool_routing=smart_tool_routing,
            forced_intent=forced_intent,
            search_invocations=[],
            allow_file_tools=False,
            thread_id=child_thread_id(thread_id, "search"),
            checkpointer=checkpointer,
            skill_roots=skill_roots,
        )
    )
    tools.append(
        make_analysis_agent_answer_tool(
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            chat_history=chat_history,
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            enabled_search_methods=enabled_search_methods,
            smart_tool_routing=smart_tool_routing,
            forced_intent=forced_intent,
            allow_file_tools=allow_file_tools,
            thread_id=child_thread_id(thread_id, "analysis"),
            checkpointer=checkpointer,
            skill_roots=skill_roots,
        )
    )
    return tools
