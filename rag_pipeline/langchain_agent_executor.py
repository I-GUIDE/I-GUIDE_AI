from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dotenv import load_dotenv

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
}


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
) -> List[Any]:
    strategy = (tool_strategy or "full_pipeline").strip().lower()
    if strategy == "granular":
        tools = make_langchain_granular_tools()
    elif strategy == "full_pipeline":
        tools = [make_langchain_rag_tool()]
    else:
        raise ValueError("tool_strategy must be either 'full_pipeline' or 'granular'.")
    if include_mcp_tools:
        tools.extend(make_langchain_mcp_tools(include_modules=mcp_modules))
    return tools


def _classify_intent(query: str) -> Dict[str, Any]:
    text = (query or "").strip().lower()
    analysis_hits = sorted([kw for kw in ANALYSIS_HINTS if kw in text])
    discovery_hits = sorted([kw for kw in DISCOVERY_HINTS if kw in text])

    if analysis_hits and discovery_hits:
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
        "discovery_hits": discovery_hits,
    }


def _select_allowed_tools(intent: str, available_tool_names: Sequence[str]) -> List[str]:
    available = set(available_tool_names)
    selected: List[str] = []

    if intent == "analysis_task":
        selected = [name for name in ANALYSIS_TOOL_NAMES if name in available]
    elif intent == "general_discovery":
        preferred = DISCOVERY_TOOL_NAMES | RAG_COMPONENT_TOOL_NAMES
        selected = [name for name in available_tool_names if name in preferred]
    else:  # hybrid
        preferred = DISCOVERY_TOOL_NAMES | RAG_COMPONENT_TOOL_NAMES | ANALYSIS_TOOL_NAMES
        selected = [name for name in available_tool_names if name in preferred]

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
        "discovery_hits": classification["discovery_hits"],
        "available_tools": list(available_tool_names),
        "allowed_tools": allowed,
    }


def build_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "full_pipeline",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    allowed_tool_names: Optional[List[str]] = None,
    preloaded_tools: Optional[List[Any]] = None,
) -> Any:
    """
    Create a concrete LangChain AgentExecutor wired with the repository's RAG tool.
    """
    tools = preloaded_tools or _collect_tools(
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


def _print_tool_trace(result: Any) -> None:
    if not isinstance(result, dict):
        return
    messages = result.get("messages")
    if not isinstance(messages, list):
        return

    printed = False
    print("\nTool trace:")
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if isinstance(tool_calls, list) and tool_calls:
            for call in tool_calls:
                name = call.get("name", "unknown_tool")
                args = call.get("args", {})
                print(f"- CALL {name} args={args}")
                printed = True
            continue

        name = getattr(msg, "name", None)
        tool_call_id = getattr(msg, "tool_call_id", None)
        content = getattr(msg, "content", None)
        if name and tool_call_id:
            text = content if isinstance(content, str) else str(content)
            snippet = text if len(text) <= 240 else f"{text[:240]}..."
            print(f"- RESULT {name} ({tool_call_id}): {snippet}")
            printed = True

    if not printed:
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
    print(f"- discovery_hits: {route.get('discovery_hits')}")
    print(f"- allowed_tools: {route.get('allowed_tools')}")


def run_agent_query(
    query: str,
    *,
    chat_history: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "full_pipeline",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    smart_tool_routing: bool = True,
    forced_intent: Optional[str] = None,
) -> dict:
    """
    Run one query through the LangChain agent executor.
    """
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

    executor = build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        allowed_tool_names=allowed_tool_names,
        preloaded_tools=all_tools,
    )
    messages_payload = _messages_payload(query, chat_history)
    legacy_payload = {"input": query, "chat_history": chat_history or []}
    try:
        response = executor.invoke(messages_payload)
        if route_trace and isinstance(response, dict):
            response["route_trace"] = route_trace
        return response
    except Exception as exc:
        text = str(exc).lower()
        # Only retry with legacy payload for likely input-shape compatibility issues.
        payload_shape_error = (
            "input" in text and "messages" in text
        ) or ("invalid" in text and "messages" in text) or ("missing" in text and "messages" in text)
        if payload_shape_error:
            response = executor.invoke(legacy_payload)
            if route_trace and isinstance(response, dict):
                response["route_trace"] = route_trace
            return response
        # Anvil/OpenAI-compatible gateways occasionally return HTTP 200 with null payloads.
        # Fall back to direct pipeline execution in full_pipeline mode.
        if tool_strategy == "full_pipeline" and _is_empty_model_response_error(exc):
            direct = rag_tool(query=query)
            response = {
                "fallback": "direct_rag_tool",
                "reason": str(exc),
                "result": direct,
            }
            if route_trace:
                response["route_trace"] = route_trace
            return response
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
        choices=["general_discovery", "analysis_task", "hybrid"],
        help="Force routing intent instead of automatic classification.",
    )
    args = parser.parse_args()
    selected_mcp_modules = [item.strip() for item in args.mcp_modules.split(",") if item.strip()]

    result = run_agent_query(
        args.query,
        verbose=args.verbose,
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
