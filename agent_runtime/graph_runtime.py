"""Public API for running agent queries.

This is the main entry point for the agent runtime.  It exposes
``run_agent_query``, ``stream_agent_query_events``, and
``run_code_agent_query`` which are called by the chat service and
the Flask API layer.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Generator, List, Optional

from agent_runtime.executor_factory import (
    DEFAULT_CHECKPOINTER,
    agent_config,
    build_code_agent_executor,
    build_orchestrator_agent_executor,
    child_thread_id,
    invoke_agent_with_payload_fallback,
    resolve_thread_id,
)
from agent_runtime.graph_nodes import (
    collect_orchestration_tools,
    make_search_agent_evidence_tool,
)
from agent_runtime.runtime_utils import (
    build_llm_interaction_trace,
    build_orchestration_trace,
    extract_final_answer,
    extract_search_artifacts,
)


# ---------------------------------------------------------------------------
# Synchronous query execution
# ---------------------------------------------------------------------------

def run_agent_query(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> dict:
    """Run one query through the orchestrator agent pipeline."""
    effective_thread_id = resolve_thread_id(thread_id, checkpointer)
    orchestration_tools = collect_orchestration_tools(
        query=query,
        chat_history=chat_history,
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        thread_id=effective_thread_id,
        checkpointer=checkpointer,
    )
    orchestrator = build_orchestrator_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tools=orchestration_tools,
        checkpointer=checkpointer,
    )
    orchestration_result = invoke_agent_with_payload_fallback(
        orchestrator,
        query=query,
        chat_history=chat_history,
        config=agent_config(child_thread_id(effective_thread_id, "orchestrator")),
    )
    available_agent_names = [getattr(tool, "name", "") for tool in orchestration_tools if getattr(tool, "name", "")]
    response: Dict[str, Any] = {
        "orchestration_result": orchestration_result,
        "route_trace": build_orchestration_trace(
            query=query,
            chat_history=chat_history,
            available_agent_names=available_agent_names,
            orchestration_result=orchestration_result if isinstance(orchestration_result, dict) else {},
        ),
    }
    final_answer = extract_final_answer(orchestration_result)
    if final_answer:
        response["final_answer"] = final_answer
    if effective_thread_id:
        response["thread_id"] = effective_thread_id
    return response


# ---------------------------------------------------------------------------
# Streaming query execution
# ---------------------------------------------------------------------------

def stream_agent_query_events(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> Generator[Dict[str, Any], None, None]:
    """Yield structured SSE events while running a query."""
    effective_thread_id = resolve_thread_id(thread_id, checkpointer)
    yield {
        "event": "status",
        "data": {
            "stage": "started",
            "thread_id": effective_thread_id or thread_id,
            "tool_strategy": tool_strategy,
        },
    }
    orchestration_tools = collect_orchestration_tools(
        query=query,
        chat_history=chat_history,
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        thread_id=effective_thread_id,
        checkpointer=checkpointer,
    )
    available_agent_names = [getattr(tool, "name", "") for tool in orchestration_tools if getattr(tool, "name", "")]
    yield {
        "event": "status",
        "data": {
            "stage": "initialized",
            "thread_id": effective_thread_id or thread_id,
            "tool_strategy": tool_strategy,
            "available_agents": available_agent_names,
        },
    }
    yield {"event": "status", "data": {"stage": "orchestration_agent_started"}}
    try:
        orchestrator = build_orchestrator_agent_executor(
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            tools=orchestration_tools,
            checkpointer=checkpointer,
        )
        orchestration_result = invoke_agent_with_payload_fallback(
            orchestrator,
            query=query,
            chat_history=chat_history,
            config=agent_config(child_thread_id(effective_thread_id, "orchestrator")),
        )
        artifacts = extract_search_artifacts(orchestration_result if isinstance(orchestration_result, dict) else {})
        route_trace = build_orchestration_trace(
            query=query,
            chat_history=chat_history,
            available_agent_names=available_agent_names,
            orchestration_result=orchestration_result if isinstance(orchestration_result, dict) else {},
        )
        yield {"event": "route_trace", "data": route_trace}
        yield {
            "event": "decision",
            "data": {
                "agent": "orchestrator_agent",
                "query": query,
                "route": route_trace.get("route"),
                "called_tools": route_trace.get("called_tools") or [],
                "analysis_called_tools": route_trace.get("analysis_called_tools") or [],
                "chat_history_available": route_trace.get("chat_history_available"),
            },
        }
        for interaction in build_llm_interaction_trace(
            orchestration_result if isinstance(orchestration_result, dict) else {},
            agent_name="orchestrator_agent",
        ):
            yield {"event": "llm_interaction", "data": interaction}
        for tool_call in artifacts.get("tool_calls") or []:
            yield {"event": "tool_call", "data": tool_call}
        for tool_result in artifacts.get("tool_results") or []:
            yield {"event": "tool_result", "data": tool_result}
        if "search_agent_evidence" in route_trace.get("called_tools", []):
            yield {
                "event": "search_complete",
                "data": {
                    "summary": extract_final_answer(orchestration_result) or "",
                    "tool_call_count": len(artifacts.get("tool_calls") or []),
                    "tool_result_count": len(artifacts.get("tool_results") or []),
                },
            }
        final_answer = extract_final_answer(orchestration_result)
        if final_answer:
            yield {"event": "final_answer", "data": {"answer": final_answer}}
        yield {"event": "status", "data": {"stage": "orchestration_agent_completed"}}
        response: Dict[str, Any] = {
            "orchestration_result": orchestration_result,
            "route_trace": route_trace,
        }
        if final_answer:
            response["final_answer"] = final_answer
        if effective_thread_id:
            response["thread_id"] = effective_thread_id
        yield {"event": "completed", "data": response}
    except Exception as exc:
        yield {"event": "error", "data": {"message": str(exc)}}
        raise


# ---------------------------------------------------------------------------
# Code agent query
# ---------------------------------------------------------------------------

def run_code_agent_query(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> dict:
    """Run one query through CodeAgent, with SearchAgent available as a tool."""
    effective_thread_id = resolve_thread_id(thread_id, checkpointer)
    search_invocations: List[Dict[str, Any]] = []
    search_tool = make_search_agent_evidence_tool(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=None,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        search_invocations=search_invocations,
        thread_id=effective_thread_id,
        checkpointer=checkpointer,
    )
    code_executor = build_code_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tools=[search_tool],
        checkpointer=checkpointer,
    )
    code_response = invoke_agent_with_payload_fallback(
        code_executor,
        query=query,
        chat_history=chat_history,
        config=agent_config(effective_thread_id),
    )

    response: Dict[str, Any] = {
        "code_result": code_response,
        "code_agent_search_invocations": search_invocations,
    }
    final_answer = extract_final_answer(code_response)
    if final_answer:
        response["final_answer"] = final_answer
    if effective_thread_id:
        response["thread_id"] = effective_thread_id
    return response


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_tool_trace(result: Any) -> None:
    if not isinstance(result, dict):
        return

    def _print_messages(agent_name: str, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return False

        printed_local = False
        print(f"- AGENT {agent_name} INVOKE")
        for msg in messages:
            tool_calls = getattr(msg, "tool_calls", None)
            if isinstance(tool_calls, list) and tool_calls:
                for call in tool_calls:
                    name = call.get("name", "unknown_tool")
                    args = call.get("args", {})
                    print(f"  - CALL {name} args={args}")
                    printed_local = True
                continue

            name = getattr(msg, "name", None)
            tool_call_id = getattr(msg, "tool_call_id", None)
            content = getattr(msg, "content", None)
            if name and tool_call_id:
                text = content if isinstance(content, str) else str(content)
                snippet = text if len(text) <= 240 else f"{text[:240]}..."
                print(f"  - RESULT {name} ({tool_call_id}): {snippet}")
                printed_local = True

        if not printed_local:
            print("  - (no tool calls)")
        return True

    print("\nTool trace:")
    has_two_agent_result = isinstance(result.get("search_result"), dict) or isinstance(result.get("analysis_result"), dict)
    if has_two_agent_result:
        printed_any = False
        printed_any = _print_messages("SearchAgent", result.get("search_result")) or printed_any
        printed_any = _print_messages("AnalysisAgent", result.get("analysis_result")) or printed_any
        if not printed_any:
            print("- (no agent trace)")
        return

    if isinstance(result.get("code_result"), dict):
        printed_any = False
        printed_any = _print_messages("CodeAgent", result.get("code_result")) or printed_any
        for idx, item in enumerate(result.get("code_agent_search_invocations") or [], start=1):
            route = item.get("route_trace") if isinstance(item, dict) else None
            intent = route.get("intent") if isinstance(route, dict) else None
            search_payload = item.get("search_result") if isinstance(item, dict) else None
            if _print_messages(f"SearchAgent (via CodeAgent #{idx})", search_payload):
                if intent:
                    print(f"  - ROUTE intent={intent}")
                printed_any = True
            else:
                print(f"- AGENT SearchAgent (via CodeAgent #{idx}) INVOKE")
                if intent:
                    print(f"  - ROUTE intent={intent}")
                print("  - (no tool calls)")
                printed_any = True
        if not printed_any:
            print("- (no agent trace)")
        return

    if not _print_messages("Agent", result):
        print("- (no tool calls)")


def _print_route_trace(result: Any) -> None:
    if not isinstance(result, dict):
        return
    route = result.get("route_trace")
    if not isinstance(route, dict):
        return
    print("\nRoute trace:")
    print(f"- route: {route.get('route')}")
    print(f"- intent: {route.get('intent')}")
    print(f"- reason: {route.get('reason')}")
    print(f"- analysis_hits: {route.get('analysis_hits')}")
    print(f"- code_hits: {route.get('code_hits')}")
    print(f"- discovery_hits: {route.get('discovery_hits')}")
    print(f"- allowed_tools: {route.get('allowed_tools')}")


def main() -> None:
    """CLI entry point for running a single agent query."""
    parser = argparse.ArgumentParser(description="Run the LangChain RAG agent once.")
    parser.add_argument("query", help="User query for the agent.")
    parser.add_argument("--verbose", action="store_true", help="Enable AgentExecutor verbose logs.")
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Conversation thread id used by the LangGraph checkpointer for short-term memory.",
    )
    parser.add_argument(
        "--agent-mode",
        default="analysis",
        choices=["analysis", "code"],
        help="Agent pipeline mode: analysis (SearchAgent -> AnalysisAgent) or code (CodeAgent with SearchAgent tool).",
    )
    parser.add_argument(
        "--tool-strategy",
        default="granular",
        choices=["full_pipeline", "granular"],
        help="Tool mode: granular uses keyword/semantic/neo4j/spatial/opengeodata tools; full_pipeline uses rag_tool.",
    )
    parser.add_argument(
        "--include-mcp-tools",
        action="store_true",
        help="Also load MCP tools from MCP_server/tools (search/data/spatial/image adapters).",
    )
    parser.add_argument(
        "--mcp-modules",
        default="search_tools,data_tools,spatial_analysis_tools,image_tools",
        help="Comma-separated MCP tool module names to load when --include-mcp-tools is set.",
    )
    parser.add_argument(
        "--no-smart-routing",
        action="store_true",
        help="Disable intent-based tool filtering and allow the full selected tool set.",
    )
    parser.add_argument(
        "--force-intent",
        default=None,
        choices=["general_discovery", "analysis_task", "code_task", "hybrid"],
        help="Force routing intent instead of automatic classification.",
    )
    args = parser.parse_args()
    selected_mcp_modules = [item.strip() for item in args.mcp_modules.split(",") if item.strip()]

    runner = run_code_agent_query if args.agent_mode == "code" else run_agent_query
    result = runner(
        args.query,
        verbose=args.verbose,
        thread_id=args.thread_id,
        tool_strategy=args.tool_strategy,
        include_mcp_tools=args.include_mcp_tools,
        mcp_modules=selected_mcp_modules,
        smart_tool_routing=not args.no_smart_routing,
        forced_intent=args.force_intent,
    )
    print(result)
    _print_route_trace(result)
    _print_tool_trace(result)
    final_answer = extract_final_answer(result)
    if final_answer:
        print("\nFinal answer:")
        print(final_answer)


if __name__ == "__main__":
    main()
