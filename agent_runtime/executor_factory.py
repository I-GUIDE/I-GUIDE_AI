"""Agent executor builders and LLM configuration.

Constructs LangChain ``AgentExecutor`` instances for each agent role,
wires prompt templates, and manages LLM / checkpointer setup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from agent_runtime.tool_policy import collect_tools as _collect_tools

# ---------------------------------------------------------------------------
# Agent system prompts
# ---------------------------------------------------------------------------

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
    "7. When the user asks to render a map or use QGIS/PyQGIS, call the matching QGIS tool; do not fake binary files with write_output_file.\n"
    "8. Produce a final answer for the user after using the minimum sufficient set of tools."
)

DEFAULT_CHECKPOINTER = InMemorySaver()

# ---------------------------------------------------------------------------
# Environment / LLM helpers
# ---------------------------------------------------------------------------


def load_env() -> None:
    """Load the single canonical .env from the repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


load_env()


def normalize_openai_base_url(url: Optional[str]) -> Optional[str]:
    """Strip trailing path segments from an OpenAI-compatible base URL."""
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
    """Build a ``ChatOpenAI`` instance from environment variables.

    Priority: VLLM_* env vars → OPENAI_* env vars → defaults.
    """
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:
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


# ---------------------------------------------------------------------------
# Message / thread helpers
# ---------------------------------------------------------------------------

def messages_payload(query: str, chat_history: Optional[List[Any]]) -> dict:
    """Normalise *chat_history* + *query* into a ``messages`` payload."""
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
    """Build a LangGraph configurable dict for *thread_id*."""
    if not thread_id:
        return None
    return {"configurable": {"thread_id": thread_id}}


def resolve_thread_id(thread_id: Optional[str], checkpointer: Optional[Any]) -> Optional[str]:
    """Return *thread_id* or auto-generate one when a checkpointer is present."""
    if thread_id:
        return thread_id
    if checkpointer is None:
        return None
    return f"auto-thread-{uuid4()}"


def child_thread_id(thread_id: Optional[str], label: str) -> Optional[str]:
    """Create a hierarchical child thread id."""
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
    """Invoke *executor* trying the modern messages format first, then legacy."""
    msg_payload = messages_payload(query, chat_history)
    legacy_payload = {"input": query, "chat_history": chat_history or []}
    config = {**(config or {})}
    config.setdefault("recursion_limit", 25)
    try:
        return executor.invoke(msg_payload, config=config)
    except Exception as exc:
        text = str(exc).lower()
        payload_shape_error = (
            "input" in text and "messages" in text
        ) or ("invalid" in text and "messages" in text) or ("missing" in text and "messages" in text)
        if payload_shape_error:
            return executor.invoke(legacy_payload, config=config)
        raise


def is_empty_model_response_error(exc: Exception) -> bool:
    """Return True when *exc* looks like an empty model response."""
    text = str(exc).lower()
    return (
        "nonetype" in text and "model_dump" in text
    ) or ("none" in text and "chat result" in text)


# ---------------------------------------------------------------------------
# Agent executor builders
# ---------------------------------------------------------------------------

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
    session_id: Optional[str] = None,
) -> Any:
    """Create a LangChain AgentExecutor wired with the repository's RAG tool."""
    if preloaded_tools is not None:
        tools = preloaded_tools
    else:
        tools = _collect_tools(
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            session_id=session_id,
        )
    if allowed_tool_names:
        allowed = set(allowed_tool_names)
        filtered = [tool for tool in tools if getattr(tool, "name", "") in allowed]
        if filtered:
            tools = filtered
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
            max_iterations=15,
            max_execution_time=120,
        )
    except Exception:
        # Current API path (langchain>=1.x)
        try:
            from langchain.agents import create_agent
        except Exception as exc:
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
    session_id: Optional[str] = None,
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
        session_id=session_id,
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
