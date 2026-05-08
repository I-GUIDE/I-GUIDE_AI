"""Trace parsing, filtering, and summary helpers for agent execution events."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

TraceEvent = Dict[str, Any]
TraceSummary = Dict[str, Any]


def _event_data(event: TraceEvent) -> Dict[str, Any]:
    return event.get("data") or {}


def parse_trace_events(events: Iterable[TraceEvent]) -> List[TraceEvent]:
    """Normalize a sequence of trace events into a list."""
    parsed: List[TraceEvent] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        item = {"event": event.get("event"), "data": _event_data(event)}
        if "agent_role" in event:
            item["agent_role"] = event["agent_role"]
        if "node" in event:
            item["node"] = event["node"]
        parsed.append(item)
    return parsed


def filter_trace(
    events: Sequence[TraceEvent],
    event_types: Optional[Sequence[str]] = None,
    agent_roles: Optional[Sequence[str]] = None,
    tool_name: Optional[str] = None,
    has_error: Optional[bool] = None,
) -> List[TraceEvent]:
    """Return a filtered copy of trace events."""
    filtered: List[TraceEvent] = []
    event_types_set = set(event_types or [])
    agent_roles_set = set(agent_roles or [])
    for event in events:
        if not isinstance(event, dict):
            continue
        if event_types_set and str(event.get("event")) not in event_types_set:
            continue
        if agent_roles_set and str(event.get("agent_role") or _event_data(event).get("agent")) not in agent_roles_set:
            continue
        if tool_name is not None:
            data = _event_data(event)
            if str(data.get("name") or data.get("tool_name") or "") != tool_name:
                continue
        if has_error is not None:
            is_error = str(event.get("event")) in {"error", "tool_error", "llm_error"}
            if is_error != has_error:
                continue
        filtered.append(event)
    return filtered


def get_route_trace(events: Sequence[TraceEvent]) -> Optional[Dict[str, Any]]:
    """Return the first route_trace payload in the event stream."""
    for event in events:
        if event.get("event") == "route_trace":
            return _event_data(event)
    return None


def get_final_answer(events: Sequence[TraceEvent]) -> Optional[str]:
    """Return the last final_answer payload from the event stream."""
    answer: Optional[str] = None
    for event in events:
        if event.get("event") == "final_answer":
            data = _event_data(event)
            if isinstance(data, dict):
                text = data.get("answer")
                if isinstance(text, str) and text.strip():
                    answer = text.strip()
    return answer


def get_errors(events: Sequence[TraceEvent]) -> List[TraceEvent]:
    return [event for event in events if str(event.get("event")) in {"error", "tool_error", "llm_error"}]


def get_tool_calls(events: Sequence[TraceEvent]) -> List[TraceEvent]:
    return [event for event in events if str(event.get("event")) == "tool_call"]


def get_tool_results(events: Sequence[TraceEvent]) -> List[TraceEvent]:
    return [event for event in events if str(event.get("event")) == "tool_result"]


def get_llm_interactions(events: Sequence[TraceEvent]) -> List[TraceEvent]:
    return [event for event in events if str(event.get("event")) == "llm_interaction"]


def summarize_trace(events: Sequence[TraceEvent]) -> TraceSummary:
    """Build a lightweight summary of a trace event stream."""
    parsed = list(events)
    route = get_route_trace(parsed) or {}
    errors = get_errors(parsed)
    tool_calls = get_tool_calls(parsed)
    tool_results = get_tool_results(parsed)
    llm_interactions = get_llm_interactions(parsed)
    return {
        "route": route.get("route"),
        "query": route.get("query"),
        "available_agents": route.get("available_agents") or [],
        "called_tools": route.get("called_tools") or [],
        "analysis_called_tools": route.get("analysis_called_tools") or [],
        "selected_skills": route.get("selected_skills") or [],
        "final_answer": get_final_answer(parsed),
        "error_count": len(errors),
        "tool_call_count": len(tool_calls),
        "tool_result_count": len(tool_results),
        "llm_interaction_count": len(llm_interactions),
    }


def format_summary(summary: TraceSummary) -> str:
    lines: List[str] = []
    lines.append("Trace summary:")
    if summary.get("query"):
        lines.append(f"  Query: {summary['query']}")
    if summary.get("route"):
        lines.append(f"  Route: {summary['route']}")
    if summary.get("available_agents"):
        lines.append(f"  Available agents: {', '.join(summary['available_agents'])}")
    if summary.get("called_tools"):
        lines.append(f"  Called tools: {', '.join(summary['called_tools'])}")
    if summary.get("analysis_called_tools"):
        lines.append(f"  Analysis tools: {', '.join(summary['analysis_called_tools'])}")
    if summary.get("selected_skills"):
        lines.append(f"  Selected skills: {', '.join(summary['selected_skills'])}")
    lines.append(f"  Final answer present: {'yes' if summary.get('final_answer') else 'no'}")
    lines.append(f"  Errors: {summary['error_count']}")
    lines.append(f"  Tool calls: {summary['tool_call_count']}")
    lines.append(f"  Tool results: {summary['tool_result_count']}")
    lines.append(f"  LLM interactions: {summary['llm_interaction_count']}")
    return "\n".join(lines)


def format_event(event: TraceEvent) -> str:
    data = _event_data(event)
    parts: List[str] = [f"[{event.get('event') or 'unknown'}]"]
    if role := event.get("agent_role") or data.get("agent"):
        parts.append(f"agent={role}")
    if node := event.get("node"):
        parts.append(f"node={node}")
    if sequence := data.get("sequence"):
        parts.append(f"seq={sequence}")
    main = data.get("label") or data.get("message") or ""
    if main:
        parts.append(main)
    if event.get("event") in {"tool_call", "tool_result", "tool_error"}:
        name = data.get("name") or data.get("tool_name")
        if name:
            parts.append(f"tool={name}")
    return " ".join(parts)


def format_error_details(events: Sequence[TraceEvent]) -> str:
    errors = get_errors(events)
    if not errors:
        return "No errors found."
    lines: List[str] = ["Error details:"]
    for event in errors:
        data = _event_data(event)
        lines.append(f"- {event.get('event')}: {data.get('message') or data.get('label') or ''}")
        if data.get("diagnosticText"):
            lines.append(f"  Diagnostics: {data['diagnosticText']}")
        if data.get("diagnostics"):
            lines.append(f"  Raw diagnostics: {json.dumps(data['diagnostics'], ensure_ascii=False)}")
    return "\n".join(lines)
