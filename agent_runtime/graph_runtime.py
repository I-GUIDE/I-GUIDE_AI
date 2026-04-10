from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from langgraph.graph import END, START, StateGraph

from .executor_factory import DEFAULT_CHECKPOINTER
from .graph_nodes import (
    DefaultSubagentRunner,
    classify_intent_node,
    finalize_response_node,
    initialize_request_node,
    resolve_policy_node,
    run_analysis_agent_node,
    run_code_agent_node,
    run_search_agent_node,
    run_verification_agent_node,
)
from .graph_state import AgentQueryGraphState, AgentRequest


def _route_selector(state: AgentQueryGraphState) -> str:
    return str(state["policy"].route_type)


def _verification_selector(state: AgentQueryGraphState) -> str:
    return "verify" if state["policy"].use_verification else "skip"


def build_agent_query_graph() -> Any:
    runner = DefaultSubagentRunner()
    graph = StateGraph(AgentQueryGraphState)
    graph.add_node("initialize_request", initialize_request_node)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("resolve_policy", resolve_policy_node)
    graph.add_node("run_search_agent", lambda state: run_search_agent_node(state, runner))
    graph.add_node("run_analysis_agent", lambda state: run_analysis_agent_node(state, runner))
    graph.add_node("run_code_agent", lambda state: run_code_agent_node(state, runner))
    graph.add_node("run_verification_agent", lambda state: run_verification_agent_node(state, runner))
    graph.add_node("finalize_response", finalize_response_node)

    graph.add_edge(START, "initialize_request")
    graph.add_edge("initialize_request", "classify_intent")
    graph.add_edge("classify_intent", "resolve_policy")
    graph.add_conditional_edges(
        "resolve_policy",
        _route_selector,
        {
            "search": "run_search_agent",
            "analysis": "run_analysis_agent",
            "code": "run_code_agent",
        },
    )
    graph.add_edge("run_search_agent", "finalize_response")
    graph.add_edge("run_analysis_agent", "finalize_response")
    graph.add_conditional_edges(
        "run_code_agent",
        _verification_selector,
        {
            "verify": "run_verification_agent",
            "skip": "finalize_response",
        },
    )
    graph.add_edge("run_verification_agent", "finalize_response")
    graph.add_edge("finalize_response", END)
    return graph.compile()


AGENT_QUERY_GRAPH = build_agent_query_graph()


def _build_request(
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
) -> AgentRequest:
    return AgentRequest(
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
        thread_id=thread_id,
        checkpointer=checkpointer,
    )


def _event_envelope(name: str, payload: Any, *, node: str, final_state: AgentQueryGraphState) -> Dict[str, Any]:
    response = final_state.get("response") or {}
    policy = final_state.get("policy")
    thread_id = response.get("thread_id")
    if not thread_id and final_state.get("runtime") is not None:
        thread_id = final_state["runtime"].effective_thread_id
    agent_role = response.get("agent_role")
    if not agent_role and policy is not None:
        agent_role = policy.role
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "event": name,
        "data": payload,
        "thread_id": thread_id,
        "agent_role": agent_role,
        "node": node,
        "timestamp": timestamp,
        "payload": payload,
    }


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
) -> Dict[str, Any]:
    final_state = AGENT_QUERY_GRAPH.invoke(
        {
            "request": _build_request(
                query,
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
                thread_id=thread_id,
                checkpointer=checkpointer,
            )
        }
    )
    response = final_state.get("response")
    if isinstance(response, dict):
        return response
    return {}


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
    initial_request = _build_request(
        query,
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
        thread_id=thread_id,
        checkpointer=checkpointer,
    )
    yield {
        "event": "status",
        "data": {
            "stage": "started",
            "thread_id": thread_id,
            "tool_strategy": tool_strategy,
        },
        "thread_id": thread_id,
        "agent_role": None,
        "node": "request",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "stage": "started",
            "thread_id": thread_id,
            "tool_strategy": tool_strategy,
        },
    }
    final_state: AgentQueryGraphState = {"request": initial_request}
    for update in AGENT_QUERY_GRAPH.stream({"request": initial_request}):
        if not isinstance(update, dict):
            continue
        for node_name, payload in update.items():
            if not isinstance(payload, dict):
                continue
            final_state.update(payload)
            if node_name == "initialize_request":
                runtime = payload.get("runtime")
                effective_thread_id = getattr(runtime, "effective_thread_id", None)
                yield _event_envelope(
                    "status",
                    {
                        "stage": "initialized",
                        "thread_id": effective_thread_id or thread_id,
                        "tool_strategy": tool_strategy,
                    },
                    node=node_name,
                    final_state=final_state,
                )
            elif node_name == "classify_intent":
                route_trace = (payload.get("artifacts") or getattr(payload.get("artifacts"), "route_trace", None))
                artifacts = payload.get("artifacts")
                if artifacts is not None:
                    route_trace = getattr(artifacts, "route_trace", {}) or {}
                    yield _event_envelope("route_trace", route_trace, node=node_name, final_state=final_state)
                    yield _event_envelope(
                        "status",
                        {
                            "stage": "intent_classified",
                            "intent": route_trace.get("intent"),
                            "role": route_trace.get("role"),
                        },
                        node=node_name,
                        final_state=final_state,
                    )
            elif node_name == "resolve_policy":
                policy = payload.get("policy")
                if policy is not None:
                    yield _event_envelope(
                        "status",
                        {
                            "stage": "policy_resolved",
                            "role": policy.role,
                            "allowed_tools": policy.allowed_tool_names,
                        },
                        node=node_name,
                        final_state=final_state,
                    )
            elif node_name in {"run_search_agent", "run_analysis_agent", "run_code_agent"}:
                role = final_state["policy"].role
                raw_result = (
                    payload.get("search_result")
                    or payload.get("analysis_result")
                    or payload.get("code_result")
                    or {}
                )
                envelope = (
                    payload.get("search_envelope")
                    or payload.get("analysis_envelope")
                    or payload.get("code_envelope")
                    or {}
                )
                yield _event_envelope(
                    "subagent_started",
                    {"role": role},
                    node=node_name,
                    final_state=final_state,
                )
                for tool_call in envelope.get("tool_calls") or []:
                    yield _event_envelope("tool_call", tool_call, node=node_name, final_state=final_state)
                for tool_result in envelope.get("tool_results") or []:
                    yield _event_envelope("tool_result", tool_result, node=node_name, final_state=final_state)
                for generated_file in (payload.get("artifacts").generated_files if payload.get("artifacts") is not None else []):
                    yield _event_envelope("artifact", generated_file, node=node_name, final_state=final_state)
                yield _event_envelope(
                    "subagent_completed",
                    {
                        "role": role,
                        "summary": envelope.get("summary") or "",
                        "result": raw_result,
                    },
                    node=node_name,
                    final_state=final_state,
                )
            elif node_name == "run_verification_agent":
                verification = payload.get("verification_result") or {}
                yield _event_envelope("verification_result", verification, node=node_name, final_state=final_state)
            elif node_name == "finalize_response":
                response = payload.get("response") or {}
                yield _event_envelope("completed", response, node=node_name, final_state=final_state)


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
) -> Dict[str, Any]:
    return run_agent_query(
        query,
        chat_history=chat_history,
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=None,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent or "code_task",
        thread_id=thread_id,
        checkpointer=checkpointer,
    )
