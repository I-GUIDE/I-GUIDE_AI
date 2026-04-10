from __future__ import annotations

from typing import Any, Dict, List, Sequence

from .graph_state import AgentPolicy, AgentRole
from .intent_classifier import role_for_intent

DISCOVERY_TOOL_NAMES = {
    "rag_tool",
    "mcp_search_geospatial_resources",
    "mcp_search_publications",
}
RAG_COMPONENT_TOOL_NAMES = {
    "keyword_search",
    "semantic_search",
    "neo4j_search",
    "spatial_search",
    "opengeodata_search",
}
READ_ONLY_FILE_TOOL_NAMES = {
    "read_text_file",
    "inspect_file_for_analysis",
}
WRITE_FILE_TOOL_NAMES = {
    "write_text_file",
    "write_output_file",
}
FILE_TOOL_NAMES = READ_ONLY_FILE_TOOL_NAMES | WRITE_FILE_TOOL_NAMES


def _is_mcp_tool(name: str) -> bool:
    return str(name or "").startswith("mcp_")


def _allow_tool(name: str, *, role: AgentRole, can_write_files: bool, can_use_mcp: bool) -> bool:
    tool_name = str(name or "")
    if not tool_name:
        return False

    if _is_mcp_tool(tool_name) and not can_use_mcp:
        return False

    if tool_name in WRITE_FILE_TOOL_NAMES and not can_write_files:
        return False

    if role == "verification":
        return False

    if role == "analysis":
        return False

    if role == "search":
        return (
            tool_name in DISCOVERY_TOOL_NAMES
            or tool_name in RAG_COMPONENT_TOOL_NAMES
            or tool_name in READ_ONLY_FILE_TOOL_NAMES
            or (_is_mcp_tool(tool_name) and can_use_mcp)
        )

    if role == "code":
        return (
            tool_name in RAG_COMPONENT_TOOL_NAMES
            or tool_name in FILE_TOOL_NAMES
            or tool_name in DISCOVERY_TOOL_NAMES
        )

    return False


def resolve_agent_policy(route_trace: Dict[str, Any], available_tool_names: Sequence[str], include_mcp_tools: bool) -> AgentPolicy:
    intent = str(route_trace.get("intent") or "general_discovery")
    role, route_type = role_for_intent(intent)
    can_write_files = role == "code"
    can_use_mcp = bool(include_mcp_tools and role == "search")
    allowed = [
        name
        for name in available_tool_names
        if _allow_tool(
            name,
            role=role,
            can_write_files=can_write_files,
            can_use_mcp=can_use_mcp,
        )
    ]
    return AgentPolicy(
        intent=intent,  # type: ignore[arg-type]
        reason=str(route_trace.get("reason") or ""),
        route_type=route_type,
        role=role,
        allowed_tool_names=allowed,
        can_write_files=can_write_files,
        can_use_mcp=can_use_mcp,
        use_verification=role == "code",
    )

