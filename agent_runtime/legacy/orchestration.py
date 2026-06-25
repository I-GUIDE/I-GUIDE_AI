"""Legacy agent-as-tools orchestration entrypoint (the legacy arm of the fork).

The orchestrator LLM is wrapped over sub-agent tools (search/analysis/code as StructuredTools)
and invoked. Exposed via the strategy registry (``agent_runtime.strategy``); returns the same
``OrchestratorState`` key set as the supervisor arm so the public response contract is
path-agnostic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent_runtime.executor_factory import (
    agent_config,
    child_thread_id,
    invoke_agent_with_payload_fallback,
)
from agent_runtime.legacy.builders import build_orchestrator_agent_executor
from agent_runtime.legacy.graph_nodes import collect_orchestration_tools
from agent_runtime.runtime_utils import extract_final_answer
from agent_runtime.streaming_trace import emit_trace_event


def run_legacy_orchestration(query: str, chat_history: Optional[List[Any]], cfg) -> Dict[str, Any]:
    emit_trace_event(
        "node_started",
        {"stage": "orchestrate", "message": "Orchestrator agent started"},
        agent_role="orchestrator_agent",
        node="orchestrate",
    )
    tools = collect_orchestration_tools(
        query=query,
        chat_history=chat_history,
        llm=cfg.llm,
        verbose=cfg.verbose,
        return_intermediate_steps=cfg.return_intermediate_steps,
        tool_strategy=cfg.tool_strategy,
        include_mcp_tools=cfg.include_mcp_tools,
        mcp_modules=cfg.mcp_modules,
        enabled_search_methods=cfg.enabled_search_methods,
        smart_tool_routing=cfg.smart_tool_routing,
        forced_intent=cfg.forced_intent,
        thread_id=cfg.thread_id,
        checkpointer=cfg.checkpointer,
        skill_roots=cfg.skill_roots,
    )
    names = [getattr(t, "name", "") for t in tools if getattr(t, "name", "")]
    orchestrator = build_orchestrator_agent_executor(
        llm=cfg.llm,
        verbose=cfg.verbose,
        return_intermediate_steps=cfg.return_intermediate_steps,
        tools=tools,
        checkpointer=cfg.checkpointer,
        skill_roots=cfg.skill_roots,
    )
    orchestration_result = invoke_agent_with_payload_fallback(
        orchestrator,
        query=query,
        chat_history=chat_history,
        config=agent_config(child_thread_id(cfg.thread_id, "orchestrator")),
    )
    final_answer = extract_final_answer(orchestration_result) or ""
    emit_trace_event(
        "node_completed",
        {"stage": "orchestrate", "message": "Orchestrator agent completed"},
        agent_role="orchestrator_agent",
        node="orchestrate",
    )
    return {
        "orchestration_result": orchestration_result,
        "final_answer": final_answer,
        "available_agent_names": names,
    }
