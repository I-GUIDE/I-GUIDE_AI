from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Generator, List, Mapping, Optional, Sequence
from uuid import uuid4

from .file_store import get_file_record
from .langchain_agent_executor import run_agent_query, stream_agent_query_events
from rag_pipeline.memory_module import create_memory, get_or_create_memory, update_memory

logger = logging.getLogger(__name__)


def _coerce_recent_history(chat_history: Sequence[Mapping[str, Any]], recent_k: Optional[int]) -> Sequence[Mapping[str, Any]]:
    if recent_k is None:
        return chat_history
    if recent_k <= 0:
        return []
    return chat_history[-recent_k:]


def _build_chat_history(memory_doc: Optional[Mapping[str, Any]], recent_k: Optional[int] = None) -> List[Dict[str, str]]:
    history = (memory_doc or {}).get("chat_history") or []
    selected = _coerce_recent_history(history, recent_k)
    messages: List[Dict[str, str]] = []
    for entry in selected:
        user_query = str(entry.get("userQuery") or "").strip()
        answer = str(entry.get("answer") or "").strip()
        if user_query:
            messages.append({"role": "user", "content": user_query})
        if answer:
            messages.append({"role": "assistant", "content": answer})
    return messages


def _extract_agent_answer(result: Mapping[str, Any]) -> str:
    final_answer = result.get("final_answer")
    if isinstance(final_answer, str) and final_answer.strip():
        return final_answer.strip()

    analysis_result = result.get("analysis_result")
    if isinstance(analysis_result, Mapping):
        messages = analysis_result.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, Mapping):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                content = getattr(message, "content", None)
                if isinstance(content, str) and content.strip():
                    return content.strip()
    return ""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

    content = getattr(value, "content", None)
    if content is not None:
        payload: Dict[str, Any] = {
            "type": value.__class__.__name__,
            "content": _json_safe(content),
        }
        name = getattr(value, "name", None)
        if name:
            payload["name"] = str(name)
        tool_call_id = getattr(value, "tool_call_id", None)
        if tool_call_id:
            payload["tool_call_id"] = str(tool_call_id)
        tool_calls = getattr(value, "tool_calls", None)
        if tool_calls:
            payload["tool_calls"] = _json_safe(tool_calls)
        return payload

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe(model_dump())
        except Exception:
            pass

    return str(value)


def _normalize_file_paths(file_paths: Optional[Sequence[Any]]) -> List[str]:
    if isinstance(file_paths, (str, bytes)):
        file_paths = [file_paths]
    normalized: List[str] = []
    for value in file_paths or []:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            normalized.append(str(Path(text).expanduser()))
        except Exception:
            normalized.append(text)
    return normalized


def _augment_user_input_with_files(user_input: str, file_paths: Sequence[str]) -> str:
    if not file_paths:
        return user_input
    attachment_lines = "\n".join(f"- {path}" for path in file_paths)
    return (
        f"{user_input}\n\n"
        "Attached files are available to the agent via local file tools. "
        "Use the provided paths if file inspection is needed:\n"
        f"{attachment_lines}"
    )


def _normalize_file_ids(file_ids: Optional[Sequence[Any]]) -> List[str]:
    if isinstance(file_ids, (str, bytes)):
        file_ids = [file_ids]
    normalized: List[str] = []
    for value in file_ids or []:
        text = str(value or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _augment_user_input_with_file_ids(user_input: str, file_ids: Sequence[str]) -> str:
    if not file_ids:
        return user_input

    lines: List[str] = []
    for file_id in file_ids:
        record = get_file_record(file_id)
        if record:
            lines.append(f"- {file_id} ({record.get('filename', 'unknown')})")
        else:
            lines.append(f"- {file_id}")
    attachment_lines = "\n".join(lines)
    return (
        f"{user_input}\n\n"
        "Uploaded files are available to the agent via local file tools. "
        "Use these uploaded file ids when inspecting files, and use `write_output_file` for downloadable outputs:\n"
        f"{attachment_lines}"
    )


def _normalize_enabled_search_methods(enabled_search_methods: Optional[Sequence[Any]]) -> Optional[List[str]]:
    if enabled_search_methods is None:
        return None
    if isinstance(enabled_search_methods, (str, bytes)):
        enabled_search_methods = [item.strip() for item in str(enabled_search_methods).split(",")]
    normalized = [str(item).strip() for item in enabled_search_methods if str(item).strip()]
    return normalized or []


def run_agent_chat(
    *,
    user_input: str,
    thread_id: Optional[str] = None,
    memory_id: Optional[str] = None,
    conversation_name: Optional[str] = None,
    recent_k: Optional[int] = None,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[Sequence[Any]] = None,
    use_persistent_memory: bool = True,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    file_paths: Optional[Sequence[Any]] = None,
    file_ids: Optional[Sequence[Any]] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    effective_memory_id = memory_id
    memory_doc: Optional[Mapping[str, Any]] = None
    memory_warning: Optional[str] = None
    normalized_file_paths = _normalize_file_paths(file_paths)
    normalized_file_ids = _normalize_file_ids(file_ids)
    normalized_enabled_search_methods = _normalize_enabled_search_methods(enabled_search_methods)

    if use_persistent_memory:
        try:
            if effective_memory_id:
                memory_doc = get_or_create_memory(effective_memory_id)
            else:
                effective_memory_id = create_memory(conversation_name or "agent-chat")
                memory_doc = get_or_create_memory(effective_memory_id)
        except Exception as exc:
            logger.warning("Persistent agent chat memory unavailable: %s", exc)
            memory_warning = f"persistent_memory_unavailable: {exc}"
            effective_memory_id = memory_id
    else:
        effective_memory_id = None

    chat_history = _build_chat_history(memory_doc, recent_k=recent_k)
    effective_input = _augment_user_input_with_files(user_input, normalized_file_paths)
    effective_input = _augment_user_input_with_file_ids(effective_input, normalized_file_ids)

    result = run_agent_query(
        effective_input,
        chat_history=chat_history,
        verbose=verbose,
        return_intermediate_steps=True,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=normalized_enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        thread_id=thread_id,
    )

    answer = _extract_agent_answer(result)
    message_id = str(uuid4())
    if use_persistent_memory and effective_memory_id:
        try:
            update_memory(
                effective_memory_id,
                user_query=user_input,
                message_id=message_id,
                answer=answer,
                elements=[],
            )
        except Exception as exc:
            logger.warning("Failed to persist agent chat turn for %s: %s", effective_memory_id, exc)
            if memory_warning is None:
                memory_warning = f"persistent_memory_update_failed: {exc}"

    response: Dict[str, Any] = {
        "answer": answer,
        "message_id": message_id,
        "memory_id": effective_memory_id,
        "thread_id": result.get("thread_id") or thread_id,
        "agent_role": result.get("agent_role"),
        "intent": result.get("intent"),
        "file_paths": normalized_file_paths,
        "file_ids": normalized_file_ids,
        "enabled_search_methods": normalized_enabled_search_methods,
        "use_persistent_memory": use_persistent_memory,
        "route_trace": result.get("route_trace") or {},
        "artifacts": result.get("artifacts") or {},
        "agent_result": _json_safe(result),
    }
    if memory_warning:
        response["warning"] = memory_warning
    return response


def stream_agent_chat_events(
    *,
    user_input: str,
    thread_id: Optional[str] = None,
    memory_id: Optional[str] = None,
    conversation_name: Optional[str] = None,
    recent_k: Optional[int] = None,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[Sequence[Any]] = None,
    use_persistent_memory: bool = True,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    file_paths: Optional[Sequence[Any]] = None,
    file_ids: Optional[Sequence[Any]] = None,
    verbose: bool = False,
) -> Generator[Dict[str, Any], None, None]:
    effective_memory_id = memory_id
    memory_doc: Optional[Mapping[str, Any]] = None
    memory_warning: Optional[str] = None
    normalized_file_paths = _normalize_file_paths(file_paths)
    normalized_file_ids = _normalize_file_ids(file_ids)
    normalized_enabled_search_methods = _normalize_enabled_search_methods(enabled_search_methods)

    yield {
        "event": "status",
        "data": {
            "stage": "agent_chat_started",
            "memory_id": effective_memory_id,
            "thread_id": thread_id,
            "file_paths": normalized_file_paths,
            "file_ids": normalized_file_ids,
            "enabled_search_methods": normalized_enabled_search_methods,
            "use_persistent_memory": use_persistent_memory,
        },
    }

    if use_persistent_memory:
        try:
            if effective_memory_id:
                memory_doc = get_or_create_memory(effective_memory_id)
            else:
                effective_memory_id = create_memory(conversation_name or "agent-chat")
                memory_doc = get_or_create_memory(effective_memory_id)
            yield {
                "event": "memory_loaded",
                "data": {
                    "memory_id": effective_memory_id,
                    "recent_k": recent_k,
                    "history_length": len((memory_doc or {}).get("chat_history") or []),
                },
            }
        except Exception as exc:
            logger.warning("Persistent agent chat memory unavailable: %s", exc)
            memory_warning = f"persistent_memory_unavailable: {exc}"
            effective_memory_id = memory_id
            yield {
                "event": "warning",
                "data": {
                    "stage": "memory_load",
                    "message": memory_warning,
                },
            }
    else:
        effective_memory_id = None
        yield {
            "event": "status",
            "data": {
                "stage": "persistent_memory_disabled",
            },
        }

    chat_history = _build_chat_history(memory_doc, recent_k=recent_k)
    effective_input = _augment_user_input_with_files(user_input, normalized_file_paths)
    effective_input = _augment_user_input_with_file_ids(effective_input, normalized_file_ids)
    completed_response: Optional[Dict[str, Any]] = None
    for event in stream_agent_query_events(
        effective_input,
        chat_history=chat_history,
        verbose=verbose,
        return_intermediate_steps=True,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=normalized_enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        thread_id=thread_id,
    ):
        if event.get("event") == "completed" and isinstance(event.get("data"), Mapping):
            completed_response = dict(event["data"])
        yield event

    answer = _extract_agent_answer(completed_response or {})
    message_id = str(uuid4())
    if use_persistent_memory and effective_memory_id:
        try:
            update_memory(
                effective_memory_id,
                user_query=user_input,
                message_id=message_id,
                answer=answer,
                elements=[],
            )
            yield {
                "event": "memory_saved",
                "data": {
                    "memory_id": effective_memory_id,
                    "message_id": message_id,
                },
            }
        except Exception as exc:
            logger.warning("Failed to persist agent chat turn for %s: %s", effective_memory_id, exc)
            if memory_warning is None:
                memory_warning = f"persistent_memory_update_failed: {exc}"
            yield {
                "event": "warning",
                "data": {
                    "stage": "memory_save",
                    "message": memory_warning,
                },
            }

    final_response: Dict[str, Any] = {
        "answer": answer,
        "message_id": message_id,
        "memory_id": effective_memory_id,
        "thread_id": (completed_response or {}).get("thread_id") or thread_id,
        "agent_role": (completed_response or {}).get("agent_role"),
        "intent": (completed_response or {}).get("intent"),
        "file_paths": normalized_file_paths,
        "file_ids": normalized_file_ids,
        "enabled_search_methods": normalized_enabled_search_methods,
        "use_persistent_memory": use_persistent_memory,
        "route_trace": (completed_response or {}).get("route_trace") or {},
        "artifacts": (completed_response or {}).get("artifacts") or {},
        "agent_result": _json_safe(completed_response or {}),
    }
    if memory_warning:
        final_response["warning"] = memory_warning
    yield {"event": "response", "data": final_response}


__all__ = ["run_agent_chat", "stream_agent_chat_events"]
