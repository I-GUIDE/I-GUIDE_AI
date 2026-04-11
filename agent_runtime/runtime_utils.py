"""Response parsing, artifact extraction, and trace building utilities.

Pure functions that inspect agent results and build structured traces
for observability.  No side effects, no LLM calls, no external I/O.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Answer extraction
# ---------------------------------------------------------------------------

def extract_final_answer(result: Any) -> Optional[str]:
    """Extract the final answer string from an agent result dict."""
    if not isinstance(result, dict):
        return None
    answer = result.get("final_answer")
    if isinstance(answer, str) and answer.strip():
        return answer.strip()

    # Direct fallback path
    if result.get("fallback") == "direct_rag_tool":
        direct = result.get("result")
        if isinstance(direct, dict):
            answer = direct.get("answer")
            if isinstance(answer, str) and answer.strip():
                return answer.strip()

    messages = result.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(msg, dict):
                c = msg.get("content")
                if isinstance(c, str) and c.strip():
                    return c.strip()
    return None


# ---------------------------------------------------------------------------
# Search artifact extraction
# ---------------------------------------------------------------------------

def extract_search_artifacts(result: Any) -> Dict[str, Any]:
    """Parse agent messages into tool_calls, tool_results, and raw_messages."""
    artifacts: Dict[str, Any] = {"tool_calls": [], "tool_results": [], "raw_messages": []}
    if not isinstance(result, dict):
        return artifacts
    messages = result.get("messages")
    if not isinstance(messages, list):
        return artifacts

    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            artifacts["raw_messages"].append(content.strip())
        elif isinstance(msg, dict):
            text = msg.get("content")
            if isinstance(text, str) and text.strip():
                artifacts["raw_messages"].append(text.strip())

        tool_calls = getattr(msg, "tool_calls", None)
        if isinstance(tool_calls, list):
            for call in tool_calls:
                artifacts["tool_calls"].append(
                    {
                        "name": call.get("name", "unknown_tool"),
                        "args": call.get("args", {}),
                    }
                )

        name = getattr(msg, "name", None)
        tool_call_id = getattr(msg, "tool_call_id", None)
        if name and tool_call_id:
            text = content if isinstance(content, str) else str(content)
            artifacts["tool_results"].append(
                {
                    "name": name,
                    "tool_call_id": tool_call_id,
                    "content": text,
                }
            )
    return artifacts


def extract_tool_result_json(artifacts: Dict[str, Any], tool_name: str) -> Optional[Dict[str, Any]]:
    """Find and parse the last JSON tool result for *tool_name*."""
    for item in reversed(artifacts.get("tool_results") or []):
        if str(item.get("name") or "") != tool_name:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


# ---------------------------------------------------------------------------
# Orchestration trace
# ---------------------------------------------------------------------------

def _nested_called_tools_from_analysis_payload(artifacts: Dict[str, Any]) -> List[str]:
    payload = extract_tool_result_json(artifacts, "analysis_agent_answer") or {}
    analysis_result = payload.get("analysis_result")
    nested_artifacts = extract_search_artifacts(analysis_result if isinstance(analysis_result, dict) else {})
    return [str(item.get("name") or "") for item in nested_artifacts.get("tool_calls") or []]


def build_orchestration_trace(
    *,
    query: str,
    chat_history: Optional[List[Any]],
    available_agent_names: Sequence[str],
    orchestration_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Analyse *orchestration_result* to determine the actual route taken."""
    artifacts = extract_search_artifacts(orchestration_result)
    tool_calls = artifacts.get("tool_calls") or []
    called_tools = [str(item.get("name") or "") for item in tool_calls]
    called_set = set(called_tools)
    nested_analysis_called_tools = _nested_called_tools_from_analysis_payload(artifacts)
    nested_analysis_called_set = set(nested_analysis_called_tools)
    if "answer_from_memory" in called_set:
        memory_payload = extract_tool_result_json(artifacts, "answer_from_memory") or {}
        if memory_payload.get("can_answer") and memory_payload.get("answer"):
            route = "direct_answer"
        elif "search_agent_evidence" in called_set and "analysis_agent_answer" in called_set and "code_agent_answer" in nested_analysis_called_set:
            route = "search_then_analysis_with_code"
        elif "analysis_agent_answer" in called_set and "code_agent_answer" in nested_analysis_called_set:
            route = "analysis_with_code"
        elif "search_agent_evidence" in called_set and "analysis_agent_answer" in called_set:
            route = "search_then_analysis"
        elif "search_agent_evidence" in called_set:
            route = "search"
        elif "analysis_agent_answer" in called_set:
            route = "analysis"
        else:
            route = "direct_answer_attempted"
    elif "search_agent_evidence" in called_set and "analysis_agent_answer" in called_set and "code_agent_answer" in nested_analysis_called_set:
        route = "search_then_analysis_with_code"
    elif "analysis_agent_answer" in called_set and "code_agent_answer" in nested_analysis_called_set:
        route = "analysis_with_code"
    elif "search_agent_evidence" in called_set and "analysis_agent_answer" in called_set:
        route = "search_then_analysis"
    elif "search_agent_evidence" in called_set:
        route = "search"
    elif "analysis_agent_answer" in called_set:
        route = "analysis"
    else:
        route = "orchestrator_only"
    return {
        "query": query,
        "route": route,
        "available_agents": list(available_agent_names),
        "called_tools": called_tools,
        "analysis_called_tools": nested_analysis_called_tools,
        "chat_history_available": bool(chat_history),
    }


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

def message_role(msg: Any) -> str:
    """Extract a normalised role string from a LangChain message."""
    if isinstance(msg, dict):
        role = msg.get("role") or msg.get("type")
        if isinstance(role, str) and role.strip():
            return role.strip()
    role = getattr(msg, "type", None)
    if isinstance(role, str) and role.strip():
        return role.strip()
    role = getattr(msg, "role", None)
    if isinstance(role, str) and role.strip():
        return role.strip()
    return msg.__class__.__name__.replace("Message", "").lower() or "message"


def message_text(msg: Any) -> str:
    """Extract text content from a LangChain message."""
    content = getattr(msg, "content", None)
    if isinstance(msg, dict) and content is None:
        content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def parse_tool_result_payload(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Try to JSON-parse the content of a tool result."""
    content = item.get("content")
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


# ---------------------------------------------------------------------------
# LLM interaction trace building
# ---------------------------------------------------------------------------

def build_search_payload_interactions(
    payload: Dict[str, Any],
    *,
    agent_name: str,
    parent_tool_name: str,
    sequence_start: int,
) -> List[Dict[str, Any]]:
    """Build interaction entries from a search_agent_evidence payload."""
    interactions: List[Dict[str, Any]] = []
    sequence = sequence_start

    route_trace = payload.get("route_trace")
    if isinstance(route_trace, dict):
        interactions.append(
            {
                "sequence": sequence,
                "kind": "agent_route_decision",
                "agent": agent_name,
                "parent_tool": parent_tool_name,
                "route_trace": route_trace,
            }
        )
        sequence += 1

    for call in payload.get("search_agent_tool_calls") or []:
        interactions.append(
            {
                "sequence": sequence,
                "kind": "llm_tool_decision",
                "agent": agent_name,
                "parent_tool": parent_tool_name,
                "tool_name": call.get("name", "unknown_tool"),
                "tool_args": call.get("args", {}),
            }
        )
        sequence += 1

    for result in payload.get("search_agent_tool_results") or []:
        interactions.append(
            {
                "sequence": sequence,
                "kind": "tool_result",
                "agent": agent_name,
                "parent_tool": parent_tool_name,
                "tool_name": result.get("name", "unknown_tool"),
                "tool_call_id": result.get("tool_call_id"),
                "content": result.get("content", ""),
            }
        )
        sequence += 1

    summary = payload.get("search_agent_summary")
    if isinstance(summary, str) and summary.strip():
        interactions.append(
            {
                "sequence": sequence,
                "kind": "llm_message",
                "agent": agent_name,
                "parent_tool": parent_tool_name,
                "role": "assistant",
                "content": summary.strip(),
            }
        )

    return interactions


def build_llm_interaction_trace(
    result: Any,
    *,
    agent_name: str,
    sequence_start: int = 1,
) -> List[Dict[str, Any]]:
    """Recursively build an interaction trace from agent messages."""
    if not isinstance(result, dict):
        return []

    messages = result.get("messages")
    if not isinstance(messages, list):
        return []

    interactions: List[Dict[str, Any]] = []
    sequence = sequence_start

    for index, msg in enumerate(messages):
        role = message_role(msg)
        content = message_text(msg)
        tool_calls = getattr(msg, "tool_calls", None)
        if isinstance(msg, dict) and tool_calls is None:
            tool_calls = msg.get("tool_calls")

        if isinstance(tool_calls, list) and tool_calls:
            interactions.append(
                {
                    "sequence": sequence,
                    "kind": "llm_tool_decision",
                    "agent": agent_name,
                    "message_index": index,
                    "role": role,
                    "content": content,
                    "tool_calls": [
                        {
                            "name": call.get("name", "unknown_tool"),
                            "args": call.get("args", {}),
                        }
                        for call in tool_calls
                    ],
                }
            )
            sequence += 1
            continue

        name = getattr(msg, "name", None)
        tool_call_id = getattr(msg, "tool_call_id", None)
        if isinstance(msg, dict):
            if name is None:
                name = msg.get("name")
            if tool_call_id is None:
                tool_call_id = msg.get("tool_call_id")

        if name and tool_call_id:
            payload = parse_tool_result_payload(
                {
                    "name": str(name),
                    "tool_call_id": str(tool_call_id),
                    "content": content,
                }
            )
            tool_name = str(name)
            if isinstance(payload, dict):
                if tool_name == "search_agent_evidence":
                    nested = build_search_payload_interactions(
                        payload,
                        agent_name="search_agent",
                        parent_tool_name=tool_name,
                        sequence_start=sequence,
                    )
                    interactions.extend(nested)
                    if nested:
                        sequence = nested[-1]["sequence"] + 1
                elif tool_name == "analysis_agent_answer":
                    analysis_result = payload.get("analysis_result")
                    nested = build_llm_interaction_trace(
                        analysis_result,
                        agent_name="analysis_agent",
                        sequence_start=sequence,
                    )
                    interactions.extend(nested)
                    if nested:
                        sequence = nested[-1]["sequence"] + 1
                elif tool_name == "code_agent_answer":
                    code_result = payload.get("code_result")
                    nested = build_llm_interaction_trace(
                        code_result,
                        agent_name="code_agent",
                        sequence_start=sequence,
                    )
                    interactions.extend(nested)
                    if nested:
                        sequence = nested[-1]["sequence"] + 1
                elif tool_name == "answer_from_memory":
                    interactions.append(
                        {
                            "sequence": sequence,
                            "kind": "memory_decision",
                            "agent": agent_name,
                            "tool_name": tool_name,
                            "can_answer": payload.get("can_answer"),
                            "reason": payload.get("reason"),
                            "answer": payload.get("answer", ""),
                        }
                    )
                    sequence += 1

            interactions.append(
                {
                    "sequence": sequence,
                    "kind": "tool_result",
                    "agent": agent_name,
                    "message_index": index,
                    "tool_name": tool_name,
                    "tool_call_id": str(tool_call_id),
                    "content": content,
                }
            )
            sequence += 1
            continue

        if content:
            interactions.append(
                {
                    "sequence": sequence,
                    "kind": "llm_message",
                    "agent": agent_name,
                    "message_index": index,
                    "role": role,
                    "content": content,
                }
            )
            sequence += 1

    return interactions


# ---------------------------------------------------------------------------
# Search evidence payload builder
# ---------------------------------------------------------------------------

def build_search_evidence_payload(
    query: str,
    search_response: Any,
    route_trace: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Bundle search results into a payload for analysis/code agents."""
    search_summary = extract_final_answer(search_response) or ""
    search_artifacts = extract_search_artifacts(search_response)
    return {
        "user_query": query,
        "route_trace": route_trace,
        "search_agent_summary": search_summary,
        "search_agent_tool_calls": search_artifacts["tool_calls"],
        "search_agent_tool_results": search_artifacts["tool_results"],
    }
