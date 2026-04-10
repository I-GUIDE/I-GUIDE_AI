from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from .langchain_file_tools import make_langchain_file_tools
from .langchain_granular_tools import make_langchain_granular_tools
from .langchain_mcp_tools import make_langchain_mcp_tools
from .langchain_tool import make_langchain_rag_tool, rag_tool

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

VERIFICATION_AGENT_PROMPT = (
    "You are VerificationAgent.\n"
    "Goal: verify whether a generated answer is internally consistent and grounded in the provided evidence.\n"
    "Return a concise verdict. Prefer strictness over optimism.\n"
    "When possible, return JSON with keys status, summary, and issues."
)

DEFAULT_CHECKPOINTER = InMemorySaver()


def _load_env() -> None:
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


def normalize_openai_base_url(url: Optional[str]) -> Optional[str]:
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


def build_default_llm() -> Any:
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:  # pragma: no cover
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
    base_url = normalize_openai_base_url(os.getenv("VLLM_PROXY") or os.getenv("OPENAI_BASE_URL"))
    kwargs = {"model": model, "temperature": 0.0, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def collect_tools(
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
    if preloaded_tools is not None:
        tools = preloaded_tools
    else:
        tools = collect_tools(
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
        )
    if allowed_tool_names is not None:
        allowed = set(allowed_tool_names)
        tools = [tool for tool in tools if getattr(tool, "name", "") in allowed]

    active_llm = llm or build_default_llm()
    system_prompt = system_prompt_override or (
        "You are a retrieval-grounded assistant.\n"
        "Guardrails:\n"
        "1. Use only tool outputs as evidence; don't hallucinate citations.\n"
        "2. If the tool output does not support a claim, explicitly say you do not have enough information.\n"
        "3. Cite only doc_ids that appear in the tool response.\n"
        "4. Never invent titles, sources, or citation ids.\n"
        "5. Prefer calling tools over guessing."
    )

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
        try:
            from langchain.agents import create_agent
        except Exception as exc:  # pragma: no cover
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


def build_verification_agent_executor(
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
        system_prompt_override=VERIFICATION_AGENT_PROMPT,
        agent_name="verification_agent",
        checkpointer=checkpointer,
    )


def messages_payload(query: str, chat_history: Optional[List[Any]]) -> dict:
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


def agent_config(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    return {"configurable": {"thread_id": thread_id}}


def resolve_thread_id(thread_id: Optional[str], checkpointer: Optional[Any]) -> Optional[str]:
    if thread_id:
        return thread_id
    if checkpointer is None:
        return None
    return f"auto-thread-{uuid4()}"


def child_thread_id(thread_id: Optional[str], label: str) -> Optional[str]:
    if not thread_id:
        return None
    return f"{thread_id}::{label}"


def invoke_agent_with_payload_fallback(
    executor: Any,
    *,
    query: str,
    chat_history: Optional[List[Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Any:
    graph_payload = messages_payload(query, chat_history)
    legacy_payload = {"input": query, "chat_history": chat_history or []}
    try:
        if config is None:
            return executor.invoke(graph_payload)
        return executor.invoke(graph_payload, config=config)
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


def is_empty_model_response_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "nonetype" in text and "model_dump" in text
    ) or ("none" in text and "chat result" in text)

