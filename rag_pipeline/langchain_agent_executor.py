from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from .langchain_file_tools import make_langchain_file_tools
from .langchain_granular_tools import make_langchain_granular_tools
from .langchain_mcp_tools import make_langchain_mcp_tools
from .langchain_tool import make_langchain_rag_tool, rag_tool

from agent_runtime.graph_state import (
    ANALYSIS_TOOL_NAMES,
    DISCOVERY_TOOL_NAMES,
    FILE_TOOL_NAMES,
    IGUIDE_SEARCH_TOOL_NAMES,
    RAG_COMPONENT_TOOL_NAMES,
)
from agent_runtime.intent_classifier import (
    ANALYSIS_HINTS,
    CODE_HINTS,
    DISCOVERY_HINTS,
    FILE_ANALYSIS_HINTS,
    build_available_routes as _build_available_routes,
    build_route_trace as _build_route_trace_impl,
    chat_history_preview as _chat_history_preview,
    classify_intent as _classify_intent,
    extract_json_object as _extract_json_object,
    heuristic_route_decision as _heuristic_route_decision,
    llm_route_decision as _llm_route_decision_impl,
    query_has_file_context as _query_has_file_context,
)

SEARCH_AGENT_PROMPT = (
    "You are SearchAgent.\n"
    "Goal: gather relevant evidence using tools.\n"
    "Rules:\n"
    "1. Prefer tool calls over assumptions.\n"
    "2. Return concise evidence with doc_ids from tool outputs.\n"
    "3. Do not fabricate citations or sources.\n"
    "4. If evidence is insufficient, explicitly say so.\n"
    "5. Do not infer local file paths or use file tools unless the user explicitly provided attached/uploaded files."
)

ANALYSIS_AGENT_PROMPT = (
    "You are AnalysisAgent.\n"
    "Goal: synthesize a final answer from provided evidence.\n"
    "Rules:\n"
    "1. Use only evidence provided in the conversation context.\n"
    "2. Cite only doc_ids that appear in the evidence.\n"
    "3. If evidence is insufficient, state uncertainty clearly.\n"
    "4. Never invent titles, sources, or citation ids.\n"
    "5. If the user would benefit from executable code and the question cannot be fully resolved with the existing evidence alone, call `code_agent_answer`."
)

CODE_AGENT_PROMPT = (
    "You are CodeAgent.\n"
    "Goal: produce practical code and implementation guidance.\n"
    "Rules:\n"
    "1. Use the `search_agent_evidence` tool to fetch domain-specific references before finalizing technical details.\n"
    "2. Ground domain facts and citations only on tool evidence.\n"
    "3. When appropriate, output a runnable fenced code block.\n"
    "4. Include a short `Dependencies:` section listing required packages or system dependencies.\n"
    "5. If evidence is insufficient, say what is missing."
)

DIRECT_ANSWER_AGENT_PROMPT = (
    "You are DirectAnswerAgent.\n"
    "Goal: answer from the supplied conversation history only.\n"
    "Rules:\n"
    "1. Use only the provided chat history as evidence.\n"
    "2. Do not call tools.\n"
    "3. If the answer is not explicitly supported by the chat history, say you do not know.\n"
    "4. Keep the answer concise and directly responsive."
)

ORCHESTRATOR_AGENT_PROMPT = (
    "You are OrchestratorAgent.\n"
    "Goal: answer the user query with the minimum necessary work.\n"
    "Available capabilities may include answering from chat history, searching for evidence, and analysis.\n"
    "Rules:\n"
    "1. If the question can be answered directly from chat history, call `answer_from_memory` first and use that answer.\n"
    "2. If direct memory is insufficient, decide whether to call `search_agent_evidence`, `analysis_agent_answer`, or both.\n"
    "3. When external evidence is needed, prefer calling `search_agent_evidence` before `analysis_agent_answer`.\n"
    "4. Do not invent facts not grounded in chat history or tool outputs.\n"
    "5. If attached/uploaded file context is explicitly present, you may use file tools directly yourself.\n"
    "6. Do not assume a local file exists unless attached/uploaded file context is explicitly present.\n"
    "7. Produce a final answer for the user after using the minimum sufficient set of tools."
)

DEFAULT_CHECKPOINTER = InMemorySaver()


def _load_env() -> None:
    """
    Load environment variables from local dotenv files.
    Priority:
    1) rag_pipeline/.env
    2) repo-root .env
    3) repo-root .env.local
    """
    current_dir = Path(__file__).resolve().parent
    repo_root = current_dir.parent
    candidates = [
        current_dir / ".env",
        repo_root / ".env",
        repo_root / ".env.local",
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)


_load_env()


def _normalize_openai_base_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    normalized = url.rstrip("/")
    suffixes = (
        "/chat/completions",
        "/completions",
        "/chat",
    )
    lowered = normalized.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def _build_default_llm() -> Any:
    """
    Build a default chat model for LangChain agents.

    Uses (vLLM-first):
    - VLLM_API_KEY (fallback: OPENAI_KEY)
    - VLLM_PROXY (fallback: OPENAI_BASE_URL)
    - VLLM_MODEL (fallback: OPENAI_CHAT_MODEL / OPENAI_MODEL / Qwen/Qwen3.5-9B)
    """
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing dependency `langchain-openai`. Install it to use the default LLM builder."
        ) from exc

    api_key = os.getenv("VLLM_API_KEY") or os.getenv("OPENAI_KEY")
    if not api_key:
        raise RuntimeError("VLLM_API_KEY (or OPENAI_KEY) is required to build the default LangChain LLM.")

    model = (
        os.getenv("VLLM_MODEL")
        or os.getenv("OPENAI_CHAT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "Qwen/Qwen3.5-9B"
    )
    base_url = _normalize_openai_base_url(os.getenv("VLLM_PROXY") or os.getenv("OPENAI_BASE_URL"))

    kwargs = {"model": model, "temperature": 0.0, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def _collect_tools(
    *,
    tool_strategy: str,
    include_mcp_tools: bool,
    mcp_modules: Optional[List[str]],
    enabled_search_methods: Optional[List[str]] = None,
    include_file_tools: bool = True,
) -> List[Any]:
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



# _classify_intent, _build_available_routes, _query_has_file_context
# moved to agent_runtime.intent_classifier


def _select_allowed_tools(intent: str, available_tool_names: Sequence[str]) -> List[str]:
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


# Thin wrappers that inject local dependencies into intent_classifier functions
# to avoid circular imports.  These will move to tool_policy / executor_factory
# in later steps.

def _build_route_trace(
    query: str,
    available_tool_names: Sequence[str],
    available_routes: Sequence[Dict[str, Any]],
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    forced_intent: Optional[str] = None,
) -> Dict[str, Any]:
    return _build_route_trace_impl(
        query,
        available_tool_names,
        available_routes,
        chat_history=chat_history,
        llm=llm,
        forced_intent=forced_intent,
        build_default_llm=_build_default_llm,
        select_allowed_tools=_select_allowed_tools,
    )


def build_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    allowed_tool_names: Optional[List[str]] = None,
    preloaded_tools: Optional[List[Any]] = None,
    system_prompt_override: Optional[str] = None,
    agent_name: str = "rag_agent",
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> Any:
    """
    Create a concrete LangChain AgentExecutor wired with the repository's RAG tool.
    """
    if preloaded_tools is not None:
        tools = preloaded_tools
    else:
        tools = _collect_tools(
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
        )
    if allowed_tool_names:
        allowed = set(allowed_tool_names)
        filtered = [tool for tool in tools if getattr(tool, "name", "") in allowed]
        if filtered:
            tools = filtered
    active_llm = llm or _build_default_llm()

    system_prompt = system_prompt_override or (
        "You are a retrieval-grounded assistant.\n"
        "Guardrails:\n"
        "1. Use only tool outputs as evidence; don't hallucinate citations.\n"
        "2. If the tool output does not support a claim, explicitly say you do not have enough information.\n"
        "3. Cite only doc_ids that appear in the tool response.\n"
        "4. Never invent titles, sources, or citation ids.\n"
        "5. Prefer calling tools over guessing."
    )

    # Legacy API path (older langchain)
    try:
        from langchain.agents import AgentExecutor, create_tool_calling_agent
        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        agent = create_tool_calling_agent(active_llm, tools, prompt)
        return AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
        )
    except Exception:
        # Current API path (langchain>=1.x)
        try:
            from langchain.agents import create_agent
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Missing compatible LangChain dependencies. Install `langchain`, `langchain-core`, and `langchain-openai`."
            ) from exc
        return create_agent(
            model=active_llm,
            tools=tools,
            system_prompt=system_prompt,
            checkpointer=checkpointer,
            debug=verbose,
            name=agent_name,
        )


def build_search_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    allowed_tool_names: Optional[List[str]] = None,
    preloaded_tools: Optional[List[Any]] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> Any:
    return build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        allowed_tool_names=allowed_tool_names,
        preloaded_tools=preloaded_tools,
        system_prompt_override=SEARCH_AGENT_PROMPT,
        agent_name="search_agent",
        checkpointer=checkpointer,
    )


def build_analysis_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    checkpointer: Optional[Any] = None,
) -> Any:
    return build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy="granular",
        include_mcp_tools=False,
        mcp_modules=None,
        preloaded_tools=[],
        system_prompt_override=ANALYSIS_AGENT_PROMPT,
        agent_name="analysis_agent",
        checkpointer=checkpointer,
    )


def build_direct_answer_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> Any:
    return build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy="granular",
        include_mcp_tools=False,
        mcp_modules=None,
        preloaded_tools=[],
        system_prompt_override=DIRECT_ANSWER_AGENT_PROMPT,
        agent_name="direct_answer_agent",
        checkpointer=checkpointer,
    )


def build_code_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tools: Optional[List[Any]] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> Any:
    return build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy="granular",
        include_mcp_tools=False,
        mcp_modules=None,
        preloaded_tools=tools or [],
        system_prompt_override=CODE_AGENT_PROMPT,
        agent_name="code_agent",
        checkpointer=checkpointer,
    )


def build_orchestrator_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tools: Optional[List[Any]] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
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
    )


def _messages_payload(query: str, chat_history: Optional[List[Any]]) -> dict:
    messages: List[dict] = []
    for item in chat_history or []:
        if isinstance(item, dict) and "role" in item and "content" in item:
            messages.append({"role": str(item["role"]), "content": str(item["content"])})
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            messages.append({"role": str(item[0]), "content": str(item[1])})
        else:
            messages.append({"role": "user", "content": str(item)})
    messages.append({"role": "user", "content": query})
    return {"messages": messages}


def _agent_config(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    return {"configurable": {"thread_id": thread_id}}


def _resolve_thread_id(thread_id: Optional[str], checkpointer: Optional[Any]) -> Optional[str]:
    if thread_id:
        return thread_id
    if checkpointer is None:
        return None
    return f"auto-thread-{uuid4()}"


def _child_thread_id(thread_id: Optional[str], label: str) -> Optional[str]:
    if not thread_id:
        return None
    return f"{thread_id}::{label}"


def _invoke_agent_with_payload_fallback(
    executor: Any,
    *,
    query: str,
    chat_history: Optional[List[Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Any:
    messages_payload = _messages_payload(query, chat_history)
    legacy_payload = {"input": query, "chat_history": chat_history or []}
    try:
        if config is None:
            return executor.invoke(messages_payload)
        return executor.invoke(messages_payload, config=config)
    except Exception as exc:
        text = str(exc).lower()
        payload_shape_error = (
            "input" in text and "messages" in text
        ) or ("invalid" in text and "messages" in text) or ("missing" in text and "messages" in text)
        if payload_shape_error:
            if config is None:
                return executor.invoke(legacy_payload)
            return executor.invoke(legacy_payload, config=config)
        raise


def _is_empty_model_response_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "nonetype" in text and "model_dump" in text
    ) or ("none" in text and "chat result" in text)


def _extract_final_answer(result: Any) -> Optional[str]:
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


def _extract_tool_result_json(artifacts: Dict[str, Any], tool_name: str) -> Optional[Dict[str, Any]]:
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


def _nested_called_tools_from_analysis_payload(artifacts: Dict[str, Any]) -> List[str]:
    payload = _extract_tool_result_json(artifacts, "analysis_agent_answer") or {}
    analysis_result = payload.get("analysis_result")
    nested_artifacts = _extract_search_artifacts(analysis_result if isinstance(analysis_result, dict) else {})
    return [str(item.get("name") or "") for item in nested_artifacts.get("tool_calls") or []]


def _build_orchestration_trace(
    *,
    query: str,
    chat_history: Optional[List[Any]],
    available_agent_names: Sequence[str],
    orchestration_result: Dict[str, Any],
) -> Dict[str, Any]:
    artifacts = _extract_search_artifacts(orchestration_result)
    tool_calls = artifacts.get("tool_calls") or []
    called_tools = [str(item.get("name") or "") for item in tool_calls]
    called_set = set(called_tools)
    nested_analysis_called_tools = _nested_called_tools_from_analysis_payload(artifacts)
    nested_analysis_called_set = set(nested_analysis_called_tools)
    if "answer_from_memory" in called_set:
        memory_payload = _extract_tool_result_json(artifacts, "answer_from_memory") or {}
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


def _extract_search_artifacts(result: Any) -> Dict[str, Any]:
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


def _message_role(msg: Any) -> str:
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


def _message_text(msg: Any) -> str:
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


def _parse_tool_result_payload(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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


def _build_search_payload_interactions(
    payload: Dict[str, Any],
    *,
    agent_name: str,
    parent_tool_name: str,
    sequence_start: int,
) -> List[Dict[str, Any]]:
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


def _build_llm_interaction_trace(
    result: Any,
    *,
    agent_name: str,
    sequence_start: int = 1,
) -> List[Dict[str, Any]]:
    if not isinstance(result, dict):
        return []

    messages = result.get("messages")
    if not isinstance(messages, list):
        return []

    interactions: List[Dict[str, Any]] = []
    sequence = sequence_start

    for index, msg in enumerate(messages):
        role = _message_role(msg)
        content = _message_text(msg)
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
            payload = _parse_tool_result_payload(
                {
                    "name": str(name),
                    "tool_call_id": str(tool_call_id),
                    "content": content,
                }
            )
            tool_name = str(name)
            if isinstance(payload, dict):
                if tool_name == "search_agent_evidence":
                    nested = _build_search_payload_interactions(
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
                    nested = _build_llm_interaction_trace(
                        analysis_result,
                        agent_name="analysis_agent",
                        sequence_start=sequence,
                    )
                    interactions.extend(nested)
                    if nested:
                        sequence = nested[-1]["sequence"] + 1
                elif tool_name == "code_agent_answer":
                    code_result = payload.get("code_result")
                    nested = _build_llm_interaction_trace(
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


def _build_search_evidence_payload(
    query: str,
    search_response: Any,
    route_trace: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    search_summary = _extract_final_answer(search_response) or ""
    search_artifacts = _extract_search_artifacts(search_response)
    return {
        "user_query": query,
        "route_trace": route_trace,
        "search_agent_summary": search_summary,
        "search_agent_tool_calls": search_artifacts["tool_calls"],
        "search_agent_tool_results": search_artifacts["tool_results"],
    }


def _make_answer_from_memory_tool(
    *,
    llm: Optional[Any],
    chat_history: Optional[List[Any]],
) -> Any:
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    history_preview = _chat_history_preview(chat_history, max_items=12)

    def answer_from_memory(query: str) -> str:
        if not history_preview:
            return json.dumps(
                {
                    "can_answer": False,
                    "answer": "",
                    "reason": "chat_history_unavailable",
                },
                ensure_ascii=True,
            )
        active_llm = llm or _build_default_llm()
        prompt = (
            "You decide whether a user query can be answered directly from prior chat history.\n"
            "Return JSON only with keys: can_answer, answer, reason.\n"
            "Set can_answer=true only if the answer is directly supported by the chat history.\n"
            "If can_answer=false, leave answer empty."
        )
        payload = {
            "query": query,
            "chat_history": history_preview,
        }
        response = active_llm.invoke(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
            ]
        )
        content = getattr(response, "content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else getattr(part, "text", str(part))
                for part in content
            )
        parsed = _extract_json_object(str(content)) or {}
        can_answer = bool(parsed.get("can_answer"))
        answer = str(parsed.get("answer") or "").strip()
        reason = str(parsed.get("reason") or ("supported_by_chat_history" if can_answer else "insufficient_chat_history"))
        if can_answer and answer:
            return json.dumps(
                {
                    "can_answer": True,
                    "answer": answer,
                    "reason": reason,
                    "history_items_used": len(history_preview),
                },
                ensure_ascii=True,
            )
        return json.dumps(
            {
                "can_answer": False,
                "answer": "",
                "reason": reason,
                "history_items_used": len(history_preview),
            },
            ensure_ascii=True,
        )

    return StructuredTool.from_function(
        func=answer_from_memory,
        name="answer_from_memory",
        description="Use the already-loaded chat history to answer memory-based questions directly. Returns JSON with can_answer and answer.",
    )


def _make_search_agent_evidence_tool(
    *,
    llm: Optional[Any],
    verbose: bool,
    return_intermediate_steps: bool,
    tool_strategy: str,
    include_mcp_tools: bool,
    mcp_modules: Optional[List[str]],
    enabled_search_methods: Optional[List[str]],
    smart_tool_routing: bool,
    forced_intent: Optional[str],
    search_invocations: List[Dict[str, Any]],
    allow_file_tools: bool = False,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> Any:
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    def search_agent_evidence(query: str) -> str:
        invocation_index = len(search_invocations) + 1
        nested_thread_id = thread_id
        if nested_thread_id:
            nested_thread_id = f"{nested_thread_id}::search_tool::{invocation_index}"
        all_tools = _collect_tools(
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            enabled_search_methods=enabled_search_methods,
            include_file_tools=allow_file_tools,
        )
        route_trace: Optional[Dict[str, Any]] = None
        allowed_tool_names: Optional[List[str]] = None
        if smart_tool_routing:
            available_names = [getattr(tool, "name", "") for tool in all_tools if getattr(tool, "name", "")]
            route_trace = _build_route_trace(
                query,
                available_names,
                [{"route": "search", "agent": "search_agent", "tool_names": available_names}],
                chat_history=None,
                llm=llm,
                forced_intent=forced_intent,
            )
            allowed_tool_names = route_trace.get("allowed_tools") or None

        search_executor = build_search_agent_executor(
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            allowed_tool_names=allowed_tool_names,
            preloaded_tools=all_tools,
            checkpointer=checkpointer,
        )
        search_response = _invoke_agent_with_payload_fallback(
            search_executor,
            query=query,
            chat_history=None,
            config=_agent_config(nested_thread_id),
        )
        evidence = _build_search_evidence_payload(query, search_response, route_trace)
        search_invocations.append(
            {
                "query": query,
                "thread_id": nested_thread_id,
                "route_trace": route_trace,
                "search_result": search_response,
                "evidence": evidence,
            }
        )
        return json.dumps(evidence, ensure_ascii=True, default=str)

    return StructuredTool.from_function(
        func=search_agent_evidence,
        name="search_agent_evidence",
        description=(
            "Call SearchAgent to retrieve domain-specific evidence and references. "
            "Use before generating code that relies on external facts."
        ),
    )


def _make_analysis_agent_answer_tool(
    *,
    llm: Optional[Any],
    verbose: bool,
    return_intermediate_steps: bool,
    chat_history: Optional[List[Any]],
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    allow_file_tools: bool = False,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> Any:
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    search_invocations: List[Dict[str, Any]] = []
    code_search_invocations: List[Dict[str, Any]] = []
    search_tool = _make_search_agent_evidence_tool(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        search_invocations=search_invocations,
        allow_file_tools=allow_file_tools,
        thread_id=_child_thread_id(thread_id, "analysis_search"),
        checkpointer=checkpointer,
    )

    def code_agent_answer(query: str, search_evidence_json: str = "") -> str:
        nested_thread_id = _child_thread_id(thread_id, "code_tool")
        code_query = query
        if search_evidence_json:
            code_query = (
                f"{query}\n\n"
                "Relevant search evidence is provided below as JSON. Use it when writing code and dependencies.\n"
                f"{search_evidence_json}"
            )
        code_response = run_code_agent_query(
            code_query,
            chat_history=chat_history,
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            smart_tool_routing=smart_tool_routing,
            forced_intent=forced_intent,
            thread_id=nested_thread_id,
            checkpointer=checkpointer,
        )
        code_search_invocations.extend(code_response.get("code_agent_search_invocations") or [])
        return json.dumps(
            {
                "answer": code_response.get("final_answer", ""),
                "code_result": code_response.get("code_result"),
                "code_agent_search_invocations": code_response.get("code_agent_search_invocations") or [],
            },
            ensure_ascii=True,
            default=str,
        )

    code_tool = StructuredTool.from_function(
        func=code_agent_answer,
        name="code_agent_answer",
        description="Use CodeAgent to provide runnable code and a Dependencies section when analysis alone is insufficient.",
    )

    analysis_tools: List[Any] = [search_tool, code_tool]
    if include_mcp_tools:
        analysis_tools = [
            *make_langchain_mcp_tools(include_modules=mcp_modules),
            *analysis_tools,
        ]

    analysis_executor = build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy="granular",
        include_mcp_tools=False,
        mcp_modules=None,
        preloaded_tools=analysis_tools,
        system_prompt_override=ANALYSIS_AGENT_PROMPT,
        agent_name="analysis_agent",
        checkpointer=checkpointer,
    )

    def analysis_agent_answer(query: str, search_evidence_json: str = "") -> str:
        nested_thread_id = _child_thread_id(thread_id, "analysis_tool")
        evidence_payload = {}
        if search_evidence_json:
            try:
                evidence_payload = json.loads(search_evidence_json)
            except Exception:
                evidence_payload = {"raw_search_evidence": search_evidence_json}
        analysis_query = query
        if evidence_payload:
            analysis_query = (
                f"{query}\n\n"
                "Search evidence is provided below as JSON. Use only this evidence and the chat history.\n"
                f"{json.dumps(evidence_payload, ensure_ascii=True)}"
            )
        analysis_response = _invoke_agent_with_payload_fallback(
            analysis_executor,
            query=analysis_query,
            chat_history=chat_history,
            config=_agent_config(nested_thread_id),
        )
        answer = _extract_final_answer(analysis_response) or ""
        return json.dumps(
            {
                "answer": answer,
                "analysis_result": analysis_response,
                "analysis_agent_search_invocations": search_invocations,
                "analysis_agent_code_search_invocations": code_search_invocations,
            },
            ensure_ascii=True,
            default=str,
        )

    return StructuredTool.from_function(
        func=analysis_agent_answer,
        name="analysis_agent_answer",
        description="Use AnalysisAgent to synthesize an answer from chat history and optional search evidence JSON.",
    )


def _print_tool_trace(result: Any) -> None:
    if not isinstance(result, dict):
        return

    def _print_messages(agent_name: str, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return False

        printed_local = False
        print(f"- AGENT {agent_name} INVOKE")
        for msg in messages:
            tool_calls = getattr(msg, "tool_calls", None)
            if isinstance(tool_calls, list) and tool_calls:
                for call in tool_calls:
                    name = call.get("name", "unknown_tool")
                    args = call.get("args", {})
                    print(f"  - CALL {name} args={args}")
                    printed_local = True
                continue

            name = getattr(msg, "name", None)
            tool_call_id = getattr(msg, "tool_call_id", None)
            content = getattr(msg, "content", None)
            if name and tool_call_id:
                text = content if isinstance(content, str) else str(content)
                snippet = text if len(text) <= 240 else f"{text[:240]}..."
                print(f"  - RESULT {name} ({tool_call_id}): {snippet}")
                printed_local = True

        if not printed_local:
            print("  - (no tool calls)")
        return True

    print("\nTool trace:")
    has_two_agent_result = isinstance(result.get("search_result"), dict) or isinstance(result.get("analysis_result"), dict)
    if has_two_agent_result:
        printed_any = False
        printed_any = _print_messages("SearchAgent", result.get("search_result")) or printed_any
        printed_any = _print_messages("AnalysisAgent", result.get("analysis_result")) or printed_any
        if not printed_any:
            print("- (no agent trace)")
        return

    if isinstance(result.get("code_result"), dict):
        printed_any = False
        printed_any = _print_messages("CodeAgent", result.get("code_result")) or printed_any
        for idx, item in enumerate(result.get("code_agent_search_invocations") or [], start=1):
            route = item.get("route_trace") if isinstance(item, dict) else None
            intent = route.get("intent") if isinstance(route, dict) else None
            search_payload = item.get("search_result") if isinstance(item, dict) else None
            if _print_messages(f"SearchAgent (via CodeAgent #{idx})", search_payload):
                if intent:
                    print(f"  - ROUTE intent={intent}")
                printed_any = True
            else:
                print(f"- AGENT SearchAgent (via CodeAgent #{idx}) INVOKE")
                if intent:
                    print(f"  - ROUTE intent={intent}")
                print("  - (no tool calls)")
                printed_any = True
        if not printed_any:
            print("- (no agent trace)")
        return

    if not _print_messages("Agent", result):
        print("- (no tool calls)")


def _print_route_trace(result: Any) -> None:
    if not isinstance(result, dict):
        return
    route = result.get("route_trace")
    if not isinstance(route, dict):
        return
    print("\nRoute trace:")
    print(f"- route: {route.get('route')}")
    print(f"- intent: {route.get('intent')}")
    print(f"- reason: {route.get('reason')}")
    print(f"- analysis_hits: {route.get('analysis_hits')}")
    print(f"- code_hits: {route.get('code_hits')}")
    print(f"- discovery_hits: {route.get('discovery_hits')}")
    print(f"- allowed_tools: {route.get('allowed_tools')}")


def _collect_orchestration_tools(
    *,
    query: str,
    chat_history: Optional[List[Any]],
    llm: Optional[Any],
    verbose: bool,
    return_intermediate_steps: bool,
    tool_strategy: str,
    include_mcp_tools: bool,
    mcp_modules: Optional[List[str]],
    enabled_search_methods: Optional[List[str]],
    smart_tool_routing: bool,
    forced_intent: Optional[str],
    thread_id: Optional[str],
    checkpointer: Optional[Any],
) -> List[Any]:
    tools: List[Any] = []
    allow_file_tools = _query_has_file_context(query)
    if allow_file_tools:
        tools.extend(make_langchain_file_tools())
    if chat_history:
        tools.append(_make_answer_from_memory_tool(llm=llm, chat_history=chat_history))
    tools.append(
        _make_search_agent_evidence_tool(
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            enabled_search_methods=enabled_search_methods,
            smart_tool_routing=smart_tool_routing,
            forced_intent=forced_intent,
            search_invocations=[],
            allow_file_tools=False,
            thread_id=_child_thread_id(thread_id, "search"),
            checkpointer=checkpointer,
        )
    )
    tools.append(
        _make_analysis_agent_answer_tool(
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            chat_history=chat_history,
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            enabled_search_methods=enabled_search_methods,
            smart_tool_routing=smart_tool_routing,
            forced_intent=forced_intent,
            allow_file_tools=allow_file_tools,
            thread_id=_child_thread_id(thread_id, "analysis"),
            checkpointer=checkpointer,
        )
    )
    return tools


def _run_orchestrated_agent_query(
    query: str,
    *,
    chat_history: Optional[List[Any]],
    llm: Optional[Any],
    verbose: bool,
    return_intermediate_steps: bool,
    tool_strategy: str,
    include_mcp_tools: bool,
    mcp_modules: Optional[List[str]],
    enabled_search_methods: Optional[List[str]],
    smart_tool_routing: bool,
    forced_intent: Optional[str],
    thread_id: Optional[str],
    checkpointer: Optional[Any],
) -> Dict[str, Any]:
    effective_thread_id = _resolve_thread_id(thread_id, checkpointer)
    orchestration_tools = _collect_orchestration_tools(
        query=query,
        chat_history=chat_history,
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        thread_id=effective_thread_id,
        checkpointer=checkpointer,
    )
    orchestrator = build_orchestrator_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tools=orchestration_tools,
        checkpointer=checkpointer,
    )
    orchestration_result = _invoke_agent_with_payload_fallback(
        orchestrator,
        query=query,
        chat_history=chat_history,
        config=_agent_config(_child_thread_id(effective_thread_id, "orchestrator")),
    )
    available_agent_names = [getattr(tool, "name", "") for tool in orchestration_tools if getattr(tool, "name", "")]
    response: Dict[str, Any] = {
        "orchestration_result": orchestration_result,
        "route_trace": _build_orchestration_trace(
            query=query,
            chat_history=chat_history,
            available_agent_names=available_agent_names,
            orchestration_result=orchestration_result if isinstance(orchestration_result, dict) else {},
        ),
    }
    final_answer = _extract_final_answer(orchestration_result)
    if final_answer:
        response["final_answer"] = final_answer
    if effective_thread_id:
        response["thread_id"] = effective_thread_id
    return response


def run_agent_query(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> dict:
    """
    Run one query through the LangChain agent executor.
    """
    return _run_orchestrated_agent_query(
        query,
        chat_history=chat_history,
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        thread_id=thread_id,
        checkpointer=checkpointer,
    )


def stream_agent_query_events(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> Generator[Dict[str, Any], None, None]:
    effective_thread_id = _resolve_thread_id(thread_id, checkpointer)
    yield {
        "event": "status",
        "data": {
            "stage": "started",
            "thread_id": effective_thread_id or thread_id,
            "tool_strategy": tool_strategy,
        },
    }
    orchestration_tools = _collect_orchestration_tools(
        query=query,
        chat_history=chat_history,
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=enabled_search_methods,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        thread_id=effective_thread_id,
        checkpointer=checkpointer,
    )
    available_agent_names = [getattr(tool, "name", "") for tool in orchestration_tools if getattr(tool, "name", "")]
    yield {
        "event": "status",
        "data": {
            "stage": "initialized",
            "thread_id": effective_thread_id or thread_id,
            "tool_strategy": tool_strategy,
            "available_agents": available_agent_names,
        },
    }
    yield {"event": "status", "data": {"stage": "orchestration_agent_started"}}
    try:
        orchestrator = build_orchestrator_agent_executor(
            llm=llm,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            tools=orchestration_tools,
            checkpointer=checkpointer,
        )
        orchestration_result = _invoke_agent_with_payload_fallback(
            orchestrator,
            query=query,
            chat_history=chat_history,
            config=_agent_config(_child_thread_id(effective_thread_id, "orchestrator")),
        )
        artifacts = _extract_search_artifacts(orchestration_result if isinstance(orchestration_result, dict) else {})
        route_trace = _build_orchestration_trace(
            query=query,
            chat_history=chat_history,
            available_agent_names=available_agent_names,
            orchestration_result=orchestration_result if isinstance(orchestration_result, dict) else {},
        )
        yield {"event": "route_trace", "data": route_trace}
        yield {
            "event": "decision",
            "data": {
                "agent": "orchestrator_agent",
                "query": query,
                "route": route_trace.get("route"),
                "called_tools": route_trace.get("called_tools") or [],
                "analysis_called_tools": route_trace.get("analysis_called_tools") or [],
                "chat_history_available": route_trace.get("chat_history_available"),
            },
        }
        for interaction in _build_llm_interaction_trace(
            orchestration_result if isinstance(orchestration_result, dict) else {},
            agent_name="orchestrator_agent",
        ):
            yield {"event": "llm_interaction", "data": interaction}
        for tool_call in artifacts.get("tool_calls") or []:
            yield {"event": "tool_call", "data": tool_call}
        for tool_result in artifacts.get("tool_results") or []:
            yield {"event": "tool_result", "data": tool_result}
        if "search_agent_evidence" in route_trace.get("called_tools", []):
            yield {
                "event": "search_complete",
                "data": {
                    "summary": _extract_final_answer(orchestration_result) or "",
                    "tool_call_count": len(artifacts.get("tool_calls") or []),
                    "tool_result_count": len(artifacts.get("tool_results") or []),
                },
            }
        final_answer = _extract_final_answer(orchestration_result)
        if final_answer:
            yield {"event": "final_answer", "data": {"answer": final_answer}}
        yield {"event": "status", "data": {"stage": "orchestration_agent_completed"}}
        response: Dict[str, Any] = {
            "orchestration_result": orchestration_result,
            "route_trace": route_trace,
        }
        if final_answer:
            response["final_answer"] = final_answer
        if effective_thread_id:
            response["thread_id"] = effective_thread_id
        yield {"event": "completed", "data": response}
    except Exception as exc:
        yield {"event": "error", "data": {"message": str(exc)}}
        raise


def run_code_agent_query(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
) -> dict:
    """
    Run one query through CodeAgent, with SearchAgent available as a tool.
    """
    effective_thread_id = _resolve_thread_id(thread_id, checkpointer)
    search_invocations: List[Dict[str, Any]] = []
    search_tool = _make_search_agent_evidence_tool(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=None,
        smart_tool_routing=smart_tool_routing,
        forced_intent=forced_intent,
        search_invocations=search_invocations,
        thread_id=effective_thread_id,
        checkpointer=checkpointer,
    )
    code_executor = build_code_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tools=[search_tool],
        checkpointer=checkpointer,
    )
    code_response = _invoke_agent_with_payload_fallback(
        code_executor,
        query=query,
        chat_history=chat_history,
        config=_agent_config(effective_thread_id),
    )

    response: Dict[str, Any] = {
        "code_result": code_response,
        "code_agent_search_invocations": search_invocations,
    }
    final_answer = _extract_final_answer(code_response)
    if final_answer:
        response["final_answer"] = final_answer
    if effective_thread_id:
        response["thread_id"] = effective_thread_id
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LangChain RAG agent once.")
    parser.add_argument("query", help="User query for the agent.")
    parser.add_argument("--verbose", action="store_true", help="Enable AgentExecutor verbose logs.")
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Conversation thread id used by the LangGraph checkpointer for short-term memory.",
    )
    parser.add_argument(
        "--agent-mode",
        default="analysis",
        choices=["analysis", "code"],
        help="Agent pipeline mode: analysis (SearchAgent -> AnalysisAgent) or code (CodeAgent with SearchAgent tool).",
    )
    parser.add_argument(
        "--tool-strategy",
        default="granular",
        choices=["full_pipeline", "granular"],
        help="Tool mode: granular uses keyword/semantic/neo4j/spatial/opengeodata tools; full_pipeline uses rag_tool.",
    )
    parser.add_argument(
        "--include-mcp-tools",
        action="store_true",
        help="Also load MCP tools from MCP_server/tools (search/data/spatial/biomass/image adapters).",
    )
    parser.add_argument(
        "--mcp-modules",
        default="search_tools,data_tools,spatial_analysis_tools,biomass_tools,image_tools",
        help="Comma-separated MCP tool module names to load when --include-mcp-tools is set.",
    )
    parser.add_argument(
        "--no-smart-routing",
        action="store_true",
        help="Disable intent-based tool filtering and allow the full selected tool set.",
    )
    parser.add_argument(
        "--force-intent",
        default=None,
        choices=["general_discovery", "analysis_task", "code_task", "hybrid"],
        help="Force routing intent instead of automatic classification.",
    )
    args = parser.parse_args()
    selected_mcp_modules = [item.strip() for item in args.mcp_modules.split(",") if item.strip()]

    runner = run_code_agent_query if args.agent_mode == "code" else run_agent_query
    result = runner(
        args.query,
        verbose=args.verbose,
        thread_id=args.thread_id,
        tool_strategy=args.tool_strategy,
        include_mcp_tools=args.include_mcp_tools,
        mcp_modules=selected_mcp_modules,
        smart_tool_routing=not args.no_smart_routing,
        forced_intent=args.force_intent,
    )
    print(result)
    _print_route_trace(result)
    _print_tool_trace(result)
    final_answer = _extract_final_answer(result)
    if final_answer:
        print("\nFinal answer:")
        print(final_answer)


if __name__ == "__main__":
    main()
