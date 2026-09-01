"""Orchestration-strategy registry — the single fork between the two independent paths.

``orchestrate_node`` builds an :class:`OrchestrationConfig` from the request and asks
``get_orchestration_strategy`` for the entrypoint to run. Each path lives in its own package
and exposes a ``run_*_orchestration(query, chat_history, cfg)`` callable returning the shared
``OrchestratorState`` key set:

* supervisor → ``agent_runtime.supervisor.orchestration.run_supervisor_orchestration``
* legacy     → ``agent_runtime.legacy.orchestration.run_legacy_orchestration``

This is what makes the two paths independent: adding/removing a path is a registry edit, and
neither package imports the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class OrchestrationConfig:
    """Per-request orchestration configuration passed to the selected strategy."""

    llm: Optional[Any] = None
    verbose: bool = False
    return_intermediate_steps: bool = True
    tool_strategy: str = "granular"
    include_mcp_tools: bool = False
    mcp_modules: Optional[List[str]] = None
    enabled_search_methods: Optional[List[str]] = None
    smart_tool_routing: bool = True
    forced_intent: Optional[str] = None
    thread_id: Optional[str] = None
    checkpointer: Optional[Any] = None
    skill_roots: Optional[List[str]] = None
    code_exec: Optional[bool] = None
    input_file_ids: Optional[List[str]] = None
    # Per-request override of AGENT_UNIFIED_PEER: run search+analyze as ONE agent. None means
    # "use the env default", so an unset request keeps whatever the deployment is configured for.
    unified_peer: Optional[bool] = None
    # Which code-peer backend this request wants; None falls back to AGENT_CODE_PEER.
    code_peer: Optional[str] = None
    # Model for that peer when it is a CLI backend; None falls back to its own env default.
    code_peer_model: Optional[str] = None


# (query, chat_history, cfg) -> orchestration result dict (OrchestratorState keys)
OrchestrationStrategy = Callable[[str, Optional[List[Any]], OrchestrationConfig], Dict[str, Any]]


def get_orchestration_strategy(use_supervisor: Optional[bool]) -> OrchestrationStrategy:
    """Resolve the orchestration entrypoint for this request.

    ``use_supervisor`` overrides per request; ``None`` falls back to the ``AGENT_SUPERVISOR``
    env default (on). Entrypoints are imported lazily so selecting one path never imports the
    other.
    """
    from agent_runtime.supervisor.graph import is_supervisor_enabled

    supervisor_on = use_supervisor if use_supervisor is not None else is_supervisor_enabled()
    if supervisor_on:
        from agent_runtime.supervisor.orchestration import run_supervisor_orchestration

        return run_supervisor_orchestration
    from agent_runtime.legacy.orchestration import run_legacy_orchestration

    return run_legacy_orchestration


__all__ = ["OrchestrationConfig", "OrchestrationStrategy", "get_orchestration_strategy"]
