from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence, TypedDict
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .langchain_file_tools import make_langchain_file_tools
from .langchain_granular_tools import make_langchain_granular_tools
from .langchain_mcp_tools import make_langchain_mcp_tools
from .langchain_tool import make_langchain_rag_tool, rag_tool

ANALYSIS_TOOL_NAMES = {
    "mcp_load_chicago_community_areas",
    "mcp_load_chicago_crime_data",
    "mcp_get_crime_statistics",
    "mcp_count_crimes_per_community",
    "mcp_generate_crime_map",
}
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
FILE_TOOL_NAMES = {
    "read_text_file",
    "inspect_file_for_analysis",
    "write_text_file",
    "write_output_file",
}
IGUIDE_SEARCH_TOOL_NAMES = {
    "keyword_search",
    "semantic_search",
    "neo4j_search",
    "spatial_search",
    "opengeodata_search",
}
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

SEARCH_AGENT_PROMPT = (
    "You are SearchAgent.\n"
    "Goal: gather relevant evidence using tools.\n"
    "Rules:\n"
    "1. Prefer tool calls over assumptions.\n"
    "2. Return concise evidence with doc_ids from tool outputs.\n"
    "3. Do not fabricate citations or sources.\n"
    "4. If evidence is insufficient, explicitly say so."
)

ANALYSIS_AGENT_PROMPT = (
    "You are AnalysisAgent.\n"
    "Goal: synthesize a final answer from provided evidence.\n"
    "Rules:\n"
    "1. Use only evidence provided in the conversation context.\n"
    "2. Cite only doc_ids that appear in the evidence.\n"
    "3. If evidence is insufficient, state uncertainty clearly.\n"
    "4. Never invent titles, sources, or citation ids."
)

CODE_AGENT_PROMPT = (
    "You are CodeAgent.\n"
    "Goal: produce practical code and implementation guidance.\n"
    "Rules:\n"
    "1. Use the `search_agent_evidence` tool to fetch domain-specific references before finalizing technical details.\n"
    "2. Ground domain facts and citations only on tool evidence.\n"
    "3. Output runnable code snippets when possible.\n"
    "4. If evidence is insufficient, say what is missing."
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
) -> List[Any]:
    strategy = (tool_strategy or "full_pipeline").strip().lower()
    if strategy == "granular":
        tools = make_langchain_granular_tools(enabled_search_methods=enabled_search_methods)
    elif strategy == "full_pipeline":
        tools = [make_langchain_rag_tool(), *make_langchain_file_tools()]
    else:
        raise ValueError("tool_strategy must be either 'full_pipeline' or 'granular'.")
    if include_mcp_tools:
        tools.extend(make_langchain_mcp_tools(include_modules=mcp_modules))
    return tools


def _classify_intent(query: str) -> Dict[str, Any]:
    text = (query or "").strip().lower()
    analysis_hits = sorted([kw for kw in ANALYSIS_HINTS if kw in text])
    code_hits = sorted([kw for kw in CODE_HINTS if kw in text])
    discovery_hits = sorted([kw for kw in DISCOVERY_HINTS if kw in text])
    file_analysis_hits = sorted([kw for kw in FILE_ANALYSIS_HINTS if kw in text])
    has_attached_files = "attached files are available to the agent via local file tools" in text

    if has_attached_files and file_analysis_hits:
        intent = "hybrid"
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
    return {
        "intent": intent,
        "reason": reason,
        "analysis_hits": analysis_hits,
        "code_hits": code_hits,
        "discovery_hits": discovery_hits,
        "file_analysis_hits": file_analysis_hits,
        "has_attached_files": has_attached_files,
    }


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


def _build_route_trace(
    query: str,
    available_tool_names: Sequence[str],
    forced_intent: Optional[str] = None,
) -> Dict[str, Any]:
    classification = _classify_intent(query)
    intent = (forced_intent or classification["intent"]).strip().lower()
    allowed = _select_allowed_tools(intent, available_tool_names)
    return {
        "query": query,
        "intent": intent,
        "forced_intent": forced_intent,
        "reason": classification["reason"],
        "analysis_hits": classification["analysis_hits"],
        "code_hits": classification["code_hits"],
        "discovery_hits": classification["discovery_hits"],
        "available_tools": list(available_tool_names),
        "allowed_tools": allowed,
    }


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


def _make_search_agent_evidence_tool(
    *,
    llm: Optional[Any],
    verbose: bool,
    return_intermediate_steps: bool,
    tool_strategy: str,
    include_mcp_tools: bool,
    mcp_modules: Optional[List[str]],
    smart_tool_routing: bool,
    forced_intent: Optional[str],
    search_invocations: List[Dict[str, Any]],
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
        all_tools = _collect_tools(
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
        )
        route_trace: Optional[Dict[str, Any]] = None
        allowed_tool_names: Optional[List[str]] = None
        if smart_tool_routing:
            available_names = [getattr(tool, "name", "") for tool in all_tools if getattr(tool, "name", "")]
            route_trace = _build_route_trace(query, available_names, forced_intent=forced_intent)
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
            config=_agent_config(thread_id),
        )
        evidence = _build_search_evidence_payload(query, search_response, route_trace)
        search_invocations.append(
            {
                "query": query,
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
    print(f"- intent: {route.get('intent')}")
    print(f"- reason: {route.get('reason')}")
    print(f"- analysis_hits: {route.get('analysis_hits')}")
    print(f"- code_hits: {route.get('code_hits')}")
    print(f"- discovery_hits: {route.get('discovery_hits')}")
    print(f"- allowed_tools: {route.get('allowed_tools')}")


class AgentQueryGraphState(TypedDict, total=False):
    query: str
    chat_history: Optional[List[Any]]
    llm: Optional[Any]
    verbose: bool
    return_intermediate_steps: bool
    tool_strategy: str
    include_mcp_tools: bool
    mcp_modules: Optional[List[str]]
    enabled_search_methods: Optional[List[str]]
    smart_tool_routing: bool
    forced_intent: Optional[str]
    thread_id: Optional[str]
    checkpointer: Optional[Any]
    all_tools: List[Any]
    effective_thread_id: Optional[str]
    route_trace: Optional[Dict[str, Any]]
    allowed_tool_names: Optional[List[str]]
    search_result: Optional[Dict[str, Any]]
    search_artifacts: Dict[str, Any]
    analysis_context: Dict[str, Any]
    analysis_query: Optional[str]
    analysis_result: Optional[Dict[str, Any]]
    response: Dict[str, Any]
    error: Optional[str]


def _agent_query_initialize_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    all_tools = _collect_tools(
        tool_strategy=state["tool_strategy"],
        include_mcp_tools=state["include_mcp_tools"],
        mcp_modules=state.get("mcp_modules"),
        enabled_search_methods=state.get("enabled_search_methods"),
    )
    effective_thread_id = _resolve_thread_id(state.get("thread_id"), state.get("checkpointer"))
    return {
        "all_tools": all_tools,
        "effective_thread_id": effective_thread_id,
    }


def _agent_query_route_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    route_trace: Optional[Dict[str, Any]] = None
    allowed_tool_names: Optional[List[str]] = None
    if state.get("smart_tool_routing"):
        available_names = [getattr(tool, "name", "") for tool in state.get("all_tools", []) if getattr(tool, "name", "")]
        route_trace = _build_route_trace(state["query"], available_names, forced_intent=state.get("forced_intent"))
        allowed_tool_names = route_trace.get("allowed_tools") or None
    return {
        "route_trace": route_trace,
        "allowed_tool_names": allowed_tool_names,
    }


def _agent_query_search_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    search_executor = build_search_agent_executor(
        llm=state.get("llm"),
        verbose=bool(state.get("verbose")),
        return_intermediate_steps=bool(state.get("return_intermediate_steps", True)),
        tool_strategy=state["tool_strategy"],
        include_mcp_tools=bool(state.get("include_mcp_tools")),
        mcp_modules=state.get("mcp_modules"),
        allowed_tool_names=state.get("allowed_tool_names"),
        preloaded_tools=state.get("all_tools"),
        checkpointer=state.get("checkpointer"),
    )
    try:
        search_response = _invoke_agent_with_payload_fallback(
            search_executor,
            query=state["query"],
            chat_history=state.get("chat_history"),
            config=_agent_config(state.get("effective_thread_id")),
        )
        return {"search_result": search_response}
    except Exception as exc:
        if state["tool_strategy"] == "full_pipeline" and _is_empty_model_response_error(exc):
            direct = rag_tool(query=state["query"])
            response: Dict[str, Any] = {
                "fallback": "direct_rag_tool",
                "reason": str(exc),
                "result": direct,
            }
            if state.get("route_trace"):
                response["route_trace"] = state["route_trace"]
            return {"response": response}
        raise


def _agent_query_build_analysis_query_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    search_response = state.get("search_result")
    if not isinstance(search_response, dict):
        return {}

    analysis_context = _build_search_evidence_payload(state["query"], search_response, state.get("route_trace"))
    analysis_query = (
        "User query:\n"
        f"{state['query']}\n\n"
        "Search evidence bundle (JSON):\n"
        f"{json.dumps(analysis_context, ensure_ascii=True, default=str)}\n\n"
        "Produce the final answer grounded only in this evidence."
    )
    return {
        "analysis_context": analysis_context,
        "analysis_query": analysis_query,
    }


def _agent_query_extract_search_artifacts_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    search_response = state.get("search_result")
    if not isinstance(search_response, dict):
        return {}
    return {"search_artifacts": _extract_search_artifacts(search_response)}


def _agent_query_analysis_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    analysis_query = state.get("analysis_query")
    if not isinstance(analysis_query, str) or not analysis_query:
        return {}

    analysis_executor = build_analysis_agent_executor(
        llm=state.get("llm"),
        verbose=bool(state.get("verbose")),
        return_intermediate_steps=bool(state.get("return_intermediate_steps", True)),
    )
    analysis_response = _invoke_agent_with_payload_fallback(
        analysis_executor,
        query=analysis_query,
        chat_history=None,
    )
    return {"analysis_result": analysis_response}


def _agent_query_finalize_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    if isinstance(state.get("response"), dict) and state["response"]:
        return {}

    response: Dict[str, Any] = {
        "search_result": state.get("search_result"),
        "analysis_result": state.get("analysis_result"),
    }
    final_answer = _extract_final_answer(state.get("analysis_result"))
    if final_answer:
        response["final_answer"] = final_answer
    if state.get("route_trace"):
        response["route_trace"] = state["route_trace"]
    if state.get("effective_thread_id"):
        response["thread_id"] = state["effective_thread_id"]
    return {"response": response}


def _agent_query_should_skip_analysis(state: AgentQueryGraphState) -> str:
    if isinstance(state.get("response"), dict) and state["response"]:
        return "finalize"
    return "build_analysis_query"


def _build_agent_query_graph() -> Any:
    graph = StateGraph(AgentQueryGraphState)
    graph.add_node("initialize", _agent_query_initialize_node)
    graph.add_node("route", _agent_query_route_node)
    graph.add_node("search", _agent_query_search_node)
    graph.add_node("extract_search_artifacts", _agent_query_extract_search_artifacts_node)
    graph.add_node("build_analysis_query", _agent_query_build_analysis_query_node)
    graph.add_node("analysis", _agent_query_analysis_node)
    graph.add_node("finalize", _agent_query_finalize_node)

    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "route")
    graph.add_edge("route", "search")
    graph.add_conditional_edges(
        "search",
        _agent_query_should_skip_analysis,
        {
            "build_analysis_query": "extract_search_artifacts",
            "finalize": "finalize",
        },
    )
    graph.add_edge("extract_search_artifacts", "build_analysis_query")
    graph.add_edge("build_analysis_query", "analysis")
    graph.add_edge("analysis", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


AGENT_QUERY_GRAPH = _build_agent_query_graph()


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
    final_state = AGENT_QUERY_GRAPH.invoke(
        {
            "query": query,
            "chat_history": chat_history,
            "llm": llm,
            "verbose": verbose,
            "return_intermediate_steps": return_intermediate_steps,
            "tool_strategy": tool_strategy,
            "include_mcp_tools": include_mcp_tools,
            "mcp_modules": mcp_modules,
            "enabled_search_methods": enabled_search_methods,
            "smart_tool_routing": smart_tool_routing,
            "forced_intent": forced_intent,
            "thread_id": thread_id,
            "checkpointer": checkpointer,
            "response": {},
        }
    )
    response = final_state.get("response")
    if isinstance(response, dict):
        return response
    return {}


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
    yield {
        "event": "status",
        "data": {
            "stage": "started",
            "thread_id": thread_id,
            "tool_strategy": tool_strategy,
        },
    }
    try:
        final_state: AgentQueryGraphState = {}
        for update in AGENT_QUERY_GRAPH.stream(
            {
                "query": query,
                "chat_history": chat_history,
                "llm": llm,
                "verbose": verbose,
                "return_intermediate_steps": return_intermediate_steps,
                "tool_strategy": tool_strategy,
                "include_mcp_tools": include_mcp_tools,
                "mcp_modules": mcp_modules,
                "enabled_search_methods": enabled_search_methods,
                "smart_tool_routing": smart_tool_routing,
                "forced_intent": forced_intent,
                "thread_id": thread_id,
                "checkpointer": checkpointer,
                "response": {},
            }
        ):
            if not isinstance(update, dict):
                continue
            for node_name, payload in update.items():
                if not isinstance(payload, dict):
                    continue
                final_state.update(payload)
                if node_name == "initialize":
                    yield {
                        "event": "status",
                        "data": {
                            "stage": "initialized",
                            "thread_id": payload.get("effective_thread_id") or thread_id,
                            "tool_strategy": tool_strategy,
                        },
                    }
                elif node_name == "route":
                    route_trace = payload.get("route_trace")
                    if route_trace:
                        yield {"event": "route_trace", "data": route_trace}
                    yield {"event": "status", "data": {"stage": "search_agent_started"}}
                elif node_name == "search":
                    yield {"event": "status", "data": {"stage": "search_agent_completed"}}
                    if isinstance(payload.get("response"), dict):
                        response = payload["response"]
                        yield {"event": "final_answer", "data": response}
                elif node_name == "extract_search_artifacts":
                    artifacts = payload.get("search_artifacts") or {}
                    yield {
                        "event": "search_complete",
                        "data": {
                            "summary": _extract_final_answer(final_state.get("search_result")) or "",
                            "tool_call_count": len(artifacts.get("tool_calls") or []),
                            "tool_result_count": len(artifacts.get("tool_results") or []),
                        },
                    }
                    for tool_call in artifacts.get("tool_calls") or []:
                        yield {"event": "tool_call", "data": tool_call}
                    for tool_result in artifacts.get("tool_results") or []:
                        yield {"event": "tool_result", "data": tool_result}
                elif node_name == "build_analysis_query":
                    analysis_context = payload.get("analysis_context")
                    if isinstance(analysis_context, dict):
                        yield {"event": "search_evidence", "data": analysis_context}
                    yield {"event": "status", "data": {"stage": "analysis_agent_started"}}
                elif node_name == "analysis":
                    final_answer = _extract_final_answer(payload.get("analysis_result"))
                    if final_answer:
                        yield {"event": "final_answer", "data": {"answer": final_answer}}
                elif node_name == "finalize":
                    response = payload.get("response")
                    if isinstance(response, dict):
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
