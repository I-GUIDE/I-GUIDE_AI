"""Executor builders owned by the legacy agent-as-tools path.

``build_orchestrator_agent_executor`` is legacy-only (its sole caller is
``legacy.orchestration.run_legacy_orchestration``). It wraps the shared
``build_agent_executor`` (core) with the legacy ``ORCHESTRATOR_AGENT_PROMPT`` — so it lives
here (legacy → core, never the reverse) rather than in the shared executor_factory.
"""

from __future__ import annotations

from typing import Any, List, Optional

from agent_runtime.executor_factory import DEFAULT_CHECKPOINTER, build_agent_executor
from agent_runtime.legacy.prompts import ORCHESTRATOR_AGENT_PROMPT


def build_orchestrator_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tools: Optional[List[Any]] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
    skill_roots: Optional[List[str]] = None,
) -> Any:
    return build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy="granular",
        include_mcp_tools=False,
        mcp_modules=None,
        preloaded_tools=tools or [],
        system_prompt_override=ORCHESTRATOR_AGENT_PROMPT,
        agent_name="orchestrator_agent",
        checkpointer=checkpointer,
        skill_roots=skill_roots,
    )


__all__ = ["build_orchestrator_agent_executor"]
