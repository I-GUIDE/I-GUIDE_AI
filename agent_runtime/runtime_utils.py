from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .graph_state import AgentRole, SubagentResultEnvelope


def extract_final_answer(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    answer = result.get("final_answer")
    if isinstance(answer, str) and answer.strip():
        return answer.strip()

    if result.get("fallback") == "direct_rag_tool":
        direct = result.get("result")
        if isinstance(direct, dict):
            direct_answer = direct.get("answer")
            if isinstance(direct_answer, str) and direct_answer.strip():
                return direct_answer.strip()

    messages = result.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(msg, dict):
                msg_content = msg.get("content")
                if isinstance(msg_content, str) and msg_content.strip():
                    return msg_content.strip()
    return None


def extract_search_artifacts(result: Any) -> Dict[str, Any]:
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


def extract_citations_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    citations = payload.get("citations")
    if isinstance(citations, list):
        return [item for item in citations if isinstance(item, dict)]
    return []


def extract_generated_files(result: Any) -> List[Dict[str, Any]]:
    artifacts = extract_search_artifacts(result)
    generated: List[Dict[str, Any]] = []
    for item in artifacts.get("tool_results") or []:
        content = str(item.get("content") or "").strip()
        try:
            parsed = json.loads(content)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        if not any(key in parsed for key in ("file_id", "download_url", "path", "filename")):
            continue
        generated.append(
            {
                "tool_name": item.get("name"),
                "file_id": parsed.get("file_id"),
                "filename": parsed.get("filename"),
                "path": parsed.get("path"),
                "download_url": parsed.get("download_url"),
            }
        )
    return generated


def build_search_evidence_payload(
    query: str,
    search_response: Any,
    route_trace: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    search_summary = extract_final_answer(search_response) or ""
    search_artifacts = extract_search_artifacts(search_response)
    return {
        "user_query": query,
        "route_trace": route_trace or {},
        "search_agent_summary": search_summary,
        "search_agent_tool_calls": search_artifacts["tool_calls"],
        "search_agent_tool_results": search_artifacts["tool_results"],
    }


def build_subagent_envelope(
    *,
    role: AgentRole,
    raw_result: Dict[str, Any],
    route_trace: Optional[Dict[str, Any]] = None,
    extra_artifacts: Optional[Dict[str, Any]] = None,
) -> SubagentResultEnvelope:
    artifacts = extract_search_artifacts(raw_result)
    merged_artifacts: Dict[str, Any] = {
        "raw_messages": artifacts.get("raw_messages") or [],
    }
    if extra_artifacts:
        merged_artifacts.update(extra_artifacts)
    if route_trace:
        merged_artifacts["route_trace"] = route_trace
    return {
        "role": role,
        "summary": extract_final_answer(raw_result) or "",
        "citations": extract_citations_from_payload(raw_result),
        "tool_calls": artifacts.get("tool_calls") or [],
        "tool_results": artifacts.get("tool_results") or [],
        "artifacts": merged_artifacts,
        "raw_result": raw_result,
    }

