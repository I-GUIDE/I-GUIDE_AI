from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, List, Optional

from dotenv import load_dotenv

from .langchain_granular_tools import make_langchain_granular_tools
from .langchain_tool import make_langchain_rag_tool, rag_tool


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

    Uses (OpenAI-first):
    - OPENAI_KEY
    - OPENAI_BASE_URL (optional; defaults to OpenAI public endpoint)
    - OPENAI_CHAT_MODEL / OPENAI_MODEL (default: gpt-4o-mini)
    """
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing dependency `langchain-openai`. Install it to use the default LLM builder."
        ) from exc

    api_key = os.getenv("OPENAI_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_KEY is required to build the default LangChain LLM.")

    model = (
        os.getenv("OPENAI_CHAT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini"
    )
    base_url = _normalize_openai_base_url(os.getenv("OPENAI_BASE_URL"))

    kwargs = {"model": model, "temperature": 0.0, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def build_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "full_pipeline",
) -> Any:
    """
    Create a concrete LangChain AgentExecutor wired with the repository's RAG tool.
    """
    strategy = (tool_strategy or "full_pipeline").strip().lower()
    if strategy == "granular":
        tools = make_langchain_granular_tools()
    elif strategy == "full_pipeline":
        rag_tool = make_langchain_rag_tool()
        tools = [rag_tool]
    else:
        raise ValueError("tool_strategy must be either 'full_pipeline' or 'granular'.")
    active_llm = llm or _build_default_llm()

    system_prompt = (
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
            debug=verbose,
            name="rag_agent",
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


def _is_empty_model_response_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "nonetype" in text and "model_dump" in text
    ) or ("none" in text and "chat result" in text)


def _extract_final_answer(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None

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


def run_agent_query(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "full_pipeline",
) -> dict:
    """
    Run one query through the LangChain agent executor.
    """
    executor = build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
    )
    messages_payload = _messages_payload(query, chat_history)
    legacy_payload = {"input": query, "chat_history": chat_history or []}
    try:
        return executor.invoke(messages_payload)
    except Exception as exc:
        text = str(exc).lower()
        # Only retry with legacy payload for likely input-shape compatibility issues.
        payload_shape_error = (
            "input" in text and "messages" in text
        ) or ("invalid" in text and "messages" in text) or ("missing" in text and "messages" in text)
        if payload_shape_error:
            return executor.invoke(legacy_payload)
        # Anvil/OpenAI-compatible gateways occasionally return HTTP 200 with null payloads.
        # Fall back to direct pipeline execution in full_pipeline mode.
        if tool_strategy == "full_pipeline" and _is_empty_model_response_error(exc):
            direct = rag_tool(query=query)
            return {
                "fallback": "direct_rag_tool",
                "reason": str(exc),
                "result": direct,
            }
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LangChain RAG agent once.")
    parser.add_argument("query", help="User query for the agent.")
    parser.add_argument("--verbose", action="store_true", help="Enable AgentExecutor verbose logs.")
    parser.add_argument(
        "--tool-strategy",
        default="full_pipeline",
        choices=["full_pipeline", "granular"],
        help="Tool mode: full_pipeline keeps parity; granular exposes keyword/semantic/neo4j/spatial/opengeodata tools.",
    )
    args = parser.parse_args()

    result = run_agent_query(
        args.query,
        verbose=args.verbose,
        tool_strategy=args.tool_strategy,
    )
    print(result)
    final_answer = _extract_final_answer(result)
    if final_answer:
        print("\nFinal answer:")
        print(final_answer)


if __name__ == "__main__":
    main()
