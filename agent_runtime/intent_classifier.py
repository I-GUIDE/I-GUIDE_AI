from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .graph_state import AgentIntent, AgentRole, RouteType

ANALYSIS_HINTS = {
    "analyze",
    "analysis",
    "count",
    "join",
    "spatial join",
    "buffer",
    "intersect",
    "within",
    "map",
    "plot",
    "statistics",
    "statistical",
    "hotspot",
}
CODE_HINTS = {
    "code",
    "python",
    "script",
    "function",
    "class",
    "implement",
    "implementation",
    "api",
    "endpoint",
    "refactor",
    "debug",
    "fix",
    "unit test",
    "sql",
}
DISCOVERY_HINTS = {
    "what is",
    "overview",
    "background",
    "resources",
    "dataset",
    "datasets",
    "publication",
    "publications",
    "notebook",
    "notebooks",
    "find",
    "discover",
    "file",
    "csv",
    "json",
    "column",
    "columns",
    "attached",
    "attachment",
}
FILE_ANALYSIS_HINTS = {
    "inspect",
    "attached file",
    "attached files",
    "attachment",
    "attachments",
    "main columns",
    "column names",
    "save a short summary",
    "uploaded file",
    "file id",
    "downloadable",
}


@dataclass(frozen=True)
class IntentClassification:
    intent: AgentIntent
    reason: str
    analysis_hits: List[str]
    code_hits: List[str]
    discovery_hits: List[str]
    file_analysis_hits: List[str]
    has_attached_files: bool

    def to_trace(self, available_tool_names: Sequence[str], allowed_tool_names: Sequence[str]) -> Dict[str, Any]:
        role, route_type = role_for_intent(self.intent)
        return {
            "intent": self.intent,
            "reason": self.reason,
            "analysis_hits": self.analysis_hits,
            "code_hits": self.code_hits,
            "discovery_hits": self.discovery_hits,
            "file_analysis_hits": self.file_analysis_hits,
            "has_attached_files": self.has_attached_files,
            "role": role,
            "route_type": route_type,
            "available_tools": list(available_tool_names),
            "allowed_tools": list(allowed_tool_names),
        }


def classify_intent(query: str) -> IntentClassification:
    text = (query or "").strip().lower()
    analysis_hits = sorted([kw for kw in ANALYSIS_HINTS if kw in text])
    code_hits = sorted([kw for kw in CODE_HINTS if kw in text])
    discovery_hits = sorted([kw for kw in DISCOVERY_HINTS if kw in text])
    file_analysis_hits = sorted([kw for kw in FILE_ANALYSIS_HINTS if kw in text])
    has_attached_files = "attached files are available to the agent via local file tools" in text

    if has_attached_files and file_analysis_hits:
        intent: AgentIntent = "hybrid"
        reason = "attached_file_analysis_request"
    elif code_hits:
        intent = "code_task"
        reason = "matched_code_hints"
    elif analysis_hits and discovery_hits:
        intent = "hybrid"
        reason = "matched_analysis_and_discovery_hints"
    elif analysis_hits:
        intent = "analysis_task"
        reason = "matched_analysis_hints"
    else:
        intent = "general_discovery"
        reason = "default_to_discovery"

    return IntentClassification(
        intent=intent,
        reason=reason,
        analysis_hits=analysis_hits,
        code_hits=code_hits,
        discovery_hits=discovery_hits,
        file_analysis_hits=file_analysis_hits,
        has_attached_files=has_attached_files,
    )


def role_for_intent(intent: str) -> tuple[AgentRole, RouteType]:
    normalized = str(intent or "").strip().lower()
    if normalized == "code_task":
        return "code", "code"
    if normalized in {"analysis_task", "hybrid"}:
        return "analysis", "analysis"
    return "search", "search"


def build_route_trace(
    query: str,
    available_tool_names: Sequence[str],
    allowed_tool_names: Sequence[str],
    forced_intent: Optional[str] = None,
) -> Dict[str, Any]:
    classification = classify_intent(query)
    resolved_intent = str(forced_intent or classification.intent).strip().lower() or classification.intent
    if resolved_intent not in {"general_discovery", "analysis_task", "code_task", "hybrid"}:
        resolved_intent = classification.intent
    effective = IntentClassification(
        intent=resolved_intent,  # type: ignore[arg-type]
        reason=classification.reason,
        analysis_hits=classification.analysis_hits,
        code_hits=classification.code_hits,
        discovery_hits=classification.discovery_hits,
        file_analysis_hits=classification.file_analysis_hits,
        has_attached_files=classification.has_attached_files,
    )
    trace = effective.to_trace(available_tool_names=available_tool_names, allowed_tool_names=allowed_tool_names)
    trace["forced_intent"] = forced_intent
    return trace

