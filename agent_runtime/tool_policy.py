"""Tool filtering and collection for the agent runtime.

Decides which tools are *available* (``collect_tools``) and which subset
an agent is *allowed* to use for a given intent (``select_allowed_tools``).
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from agent_runtime.graph_state import (
    ANALYSIS_TOOL_NAMES,
    DISCOVERY_TOOL_NAMES,
    FILE_TOOL_NAMES,
    RAG_COMPONENT_TOOL_NAMES,
)


# ---------------------------------------------------------------------------
# Tool selection based on intent
# ---------------------------------------------------------------------------

def select_allowed_tools(intent: str, available_tool_names: Sequence[str]) -> List[str]:
    """Return the subset of *available_tool_names* allowed for *intent*."""
    available = set(available_tool_names)
    selected: List[str] = []

    if intent == "analysis_task":
        selected = [name for name in ANALYSIS_TOOL_NAMES if name in available]
    elif intent == "code_task":
        preferred = DISCOVERY_TOOL_NAMES | RAG_COMPONENT_TOOL_NAMES | FILE_TOOL_NAMES
        selected = [name for name in available_tool_names if name in preferred]
    elif intent == "general_discovery":
        preferred = DISCOVERY_TOOL_NAMES | RAG_COMPONENT_TOOL_NAMES | FILE_TOOL_NAMES
        selected = [name for name in available_tool_names if name in preferred]
    else:  # hybrid
        preferred = DISCOVERY_TOOL_NAMES | RAG_COMPONENT_TOOL_NAMES | ANALYSIS_TOOL_NAMES | FILE_TOOL_NAMES
        selected = [name for name in available_tool_names if name in preferred]

    for file_tool_name in FILE_TOOL_NAMES:
        if file_tool_name in available and file_tool_name not in selected:
            selected.append(file_tool_name)

    if not selected:
        selected = list(available_tool_names)
    return selected


# ---------------------------------------------------------------------------
# Tool collection (instantiation)
# ---------------------------------------------------------------------------

def collect_tools(
    *,
    tool_strategy: str,
    include_mcp_tools: bool,
    mcp_modules: Optional[List[str]],
    enabled_search_methods: Optional[List[str]] = None,
    include_file_tools: bool = True,
) -> List[Any]:
    """Build the concrete list of LangChain ``StructuredTool`` objects.

    Imports are deferred so that this module stays lightweight when only
    ``select_allowed_tools`` is needed.
    """
    from rag_pipeline.rag_tool import make_langchain_rag_tool
    from services.langchain_file_tools import make_langchain_file_tools
    from services.langchain_granular_tools import make_langchain_granular_tools
    from services.langchain_mcp_tools import make_langchain_mcp_tools

    strategy = (tool_strategy or "full_pipeline").strip().lower()
    if strategy == "granular":
        tools = make_langchain_granular_tools(
            enabled_search_methods=enabled_search_methods,
            include_file_tools=include_file_tools,
        )
    elif strategy == "full_pipeline":
        tools = [make_langchain_rag_tool(), *make_langchain_file_tools()]
    else:
        raise ValueError("tool_strategy must be either 'full_pipeline' or 'granular'.")
    if include_mcp_tools:
        tools.extend(make_langchain_mcp_tools(include_modules=mcp_modules))
    return tools
