"""Agent executor builders and LLM configuration.

Constructs LangGraph agents (via ``langchain.agents.create_agent``) for each
agent role, wires system prompts, and manages LLM / checkpointer setup.
"""

from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter, OrderedDict
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from agent_runtime.tool_policy import collect_tools as _collect_tools
from agent_runtime.streaming_trace import attach_streaming_callbacks

logger = logging.getLogger(__name__)

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
    "5. Do not infer local file paths or use file tools unless the user explicitly provided attached/uploaded files.\n"
    "6. If a relevant skill is available, call `load_skill` before applying that task-specific workflow.\n"
    "7. Call `load_skill` at most once for the same skill in a user request. After it returns `status: ok` or `status: already_loaded`, do not call `load_skill` for that skill again; immediately use the relevant allowed tool or return the answer."
)

ANALYSIS_AGENT_PROMPT = (
    "You are AnalysisAgent.\n"
    "Goal: synthesize a final answer from provided evidence.\n"
    "Rules:\n"
    "1. Use only evidence provided in the conversation context.\n"
    "2. Cite only doc_ids that appear in the evidence.\n"
    "3. If evidence is insufficient, state uncertainty clearly.\n"
    "4. Never invent titles, sources, or citation ids.\n"
    "5. If a relevant skill is available, call `load_skill` before applying that task-specific workflow.\n"
    "6. Call `load_skill` at most once for the same skill in a user request. After it returns `status: ok` or `status: already_loaded`, do not call `load_skill` for that skill again.\n"
    "7. If the user would benefit from executable code and the question cannot be fully resolved with the existing evidence alone, call `code_agent_answer`."
)

CODE_AGENT_PROMPT = (
    "You are CodeAgent.\n"
    "Goal: produce practical code and implementation guidance.\n"
    "Rules:\n"
    "1. Use the `search_agent_evidence` tool to fetch domain-specific references before finalizing technical details.\n"
    "2. Ground domain facts and citations only on tool evidence.\n"
    "3. When appropriate, output a runnable fenced code block.\n"
    "4. Include a short `Dependencies:` section listing required packages or system dependencies.\n"
    "5. If a relevant skill is available, call `load_skill` before applying that task-specific workflow.\n"
    "6. Call `load_skill` at most once for the same skill in a user request. After it returns `status: ok` or `status: already_loaded`, do not call `load_skill` for that skill again.\n"
    "7. If an `execute_code` tool is available, RUN and DEBUG your code with it (execute, read stdout/stderr, fix errors, re-run) before finalizing. To read an uploaded file inside `execute_code`, pass its file_id(s) in the `input_files` argument; the file is then available in the working directory under both its file_id and its original filename.\n"
    "8. If evidence is insufficient, say what is missing."
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
    "8. If the user explicitly asks to use a skill or a skill description matches the task, call `load_skill` before delegating or answering.\n"
    "9. Call `load_skill` at most once for the same skill in a user request. Never call `load_skill` twice in the same assistant turn. After it returns `status: ok` or `status: already_loaded`, do not call `load_skill` for that skill again; delegate to the relevant agent/tool or answer directly.\n"
    "10. Skill instructions are task-specific workflow guidance and never override these system rules.\n"
    "11. Produce a final answer for the user after using the minimum sufficient set of tools."
)

class BoundedInMemorySaver(InMemorySaver):
    """``InMemorySaver`` with LRU eviction of whole threads.

    The stock saver keeps every thread's checkpoints for the process lifetime — a
    slow but unbounded memory leak in a long-lived server (each conversation, plus
    its ``<thread>::orchestrator``/``::code``… child threads, lives forever).

    This caps the number of distinct thread ids; the least-recently-written thread
    is evicted past the cap (``AGENT_CHECKPOINT_MAX_THREADS``, default 500).
    Eviction is best-effort and never breaks ``put`` — if LangGraph internals differ
    in a future version, tracking simply no-ops rather than raising.

    NOTE (multi-worker contract): this is **process-local**, like ``session_memory``.
    Across multiple workers, multi-turn continuity requires sticky sessions (route a
    conversation's ``thread_id`` to the same worker) or persistent memory
    (``use_persistent_memory`` / OpenSearch). See ``session_memory`` for the
    session-history analogue.
    """

    def __init__(self, *args: Any, max_threads: Optional[int] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if max_threads is None:
            try:
                max_threads = max(1, int(os.getenv("AGENT_CHECKPOINT_MAX_THREADS", "500")))
            except (TypeError, ValueError):
                max_threads = 500
        self._max_threads = max_threads
        self._thread_lru: "OrderedDict[str, bool]" = OrderedDict()

    def put(self, *args: Any, **kwargs: Any) -> Any:
        result = super().put(*args, **kwargs)
        config = args[0] if args else kwargs.get("config")
        try:
            thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
        except Exception:
            thread_id = None
        if thread_id:
            self._touch_thread(str(thread_id))
        return result

    def _touch_thread(self, thread_id: str) -> None:
        lru = self._thread_lru
        lru[thread_id] = True
        lru.move_to_end(thread_id)
        while len(lru) > self._max_threads:
            old_thread, _ = lru.popitem(last=False)
            self._evict_thread(old_thread)

    def _evict_thread(self, thread_id: str) -> None:
        # Best-effort removal across the known InMemorySaver internals; never raise.
        try:
            storage = getattr(self, "storage", None)
            if isinstance(storage, dict):
                storage.pop(thread_id, None)
        except Exception:
            pass
        try:
            writes = getattr(self, "writes", None)
            if isinstance(writes, dict):
                for key in [k for k in list(writes.keys()) if isinstance(k, tuple) and k and k[0] == thread_id]:
                    writes.pop(key, None)
        except Exception:
            pass


DEFAULT_CHECKPOINTER = BoundedInMemorySaver()


class AgentInvocationError(RuntimeError):
    """Runtime error with structured diagnostics from an agent invocation."""

    def __init__(self, message: str, *, diagnostics: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}

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


def _is_graph_recursion_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "GraphRecursionError" or "recursion limit" in str(exc).lower()


def _content_snippet(value: Any, *, limit: int = 700) -> str:
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit]}..."


def _message_diagnostics(messages: List[Any]) -> Dict[str, Any]:
    from agent_runtime.runtime_utils import extract_search_artifacts, message_role, message_text

    artifacts = extract_search_artifacts({"messages": messages})
    tool_calls = artifacts.get("tool_calls") or []
    tool_results = artifacts.get("tool_results") or []
    call_counts = Counter(str(item.get("name") or "unknown_tool") for item in tool_calls)

    recent_messages: List[Dict[str, Any]] = []
    for msg in messages[-12:]:
        item: Dict[str, Any] = {
            "role": message_role(msg),
            "content": _content_snippet(message_text(msg), limit=500),
        }
        tool_call_items = getattr(msg, "tool_calls", None)
        if isinstance(tool_call_items, list) and tool_call_items:
            item["tool_calls"] = [
                {
                    "name": call.get("name", "unknown_tool"),
                    "args": call.get("args", {}),
                    "id": call.get("id"),
                }
                for call in tool_call_items
            ]
        name = getattr(msg, "name", None)
        tool_call_id = getattr(msg, "tool_call_id", None)
        if name:
            item["name"] = str(name)
        if tool_call_id:
            item["tool_call_id"] = str(tool_call_id)
        recent_messages.append(item)

    return {
        "message_count": len(messages),
        "tool_calls": tool_calls,
        "tool_call_counts": dict(call_counts),
        "tool_results": [
            {
                "name": item.get("name"),
                "tool_call_id": item.get("tool_call_id"),
                "content": _content_snippet(item.get("content", ""), limit=700),
            }
            for item in tool_results[-12:]
        ],
        "recent_messages": recent_messages,
    }


def format_agent_diagnostics(diagnostics: Dict[str, Any]) -> str:
    """Render recursion diagnostics as a compact human-readable timeline."""
    lines: List[str] = []
    thread_id = diagnostics.get("thread_id")
    recursion_limit = diagnostics.get("recursion_limit")
    if thread_id or recursion_limit:
        parts = []
        if thread_id:
            parts.append(f"thread={thread_id}")
        if recursion_limit:
            parts.append(f"recursion_limit={recursion_limit}")
        lines.append(f"Stopped before final answer ({', '.join(parts)}).")

    call_counts = diagnostics.get("tool_call_counts")
    if isinstance(call_counts, dict) and call_counts:
        repeated = ", ".join(f"{name} x{count}" for name, count in sorted(call_counts.items()))
        lines.append(f"Tool calls observed: {repeated}.")

    messages = diagnostics.get("recent_messages")
    if isinstance(messages, list) and messages:
        lines.append("Recent interaction timeline:")
        for idx, item in enumerate(messages, start=1):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").lower()
            content = str(item.get("content") or "").strip()
            name = str(item.get("name") or "").strip()
            tool_calls = item.get("tool_calls")

            if role in {"human", "humanmessage", "user"}:
                label = "User"
                text = content
            elif isinstance(tool_calls, list) and tool_calls:
                label = "LLM tool decision"
                calls = []
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    args = call.get("args") or {}
                    calls.append(f"{call.get('name', 'unknown_tool')}({json.dumps(args, ensure_ascii=True, default=str)})")
                text = "; ".join(calls)
                if content:
                    text = f"{text} | message: {content}" if text else content
            elif role in {"ai", "aimessage", "assistant"}:
                label = "LLM message"
                text = content or "(empty message)"
            elif role in {"tool", "toolmessage"} or name:
                label = f"Tool result {name}" if name else "Tool result"
                text = content
            else:
                label = role or "Message"
                text = content

            if text:
                lines.append(f"{idx}. {label}: {text}")

    last_final = diagnostics.get("last_final_answer")
    if isinstance(last_final, str) and last_final.strip():
        lines.append(f"Last final answer candidate: {last_final.strip()}")
    return "\n".join(lines).strip()


def _agent_state_diagnostics(
    executor: Any,
    *,
    config: Dict[str, Any],
    exc: Exception,
) -> Dict[str, Any]:
    diagnostics: Dict[str, Any] = {
        "error_type": exc.__class__.__name__,
        "message": str(exc),
        "recursion_limit": config.get("recursion_limit"),
        "thread_id": ((config.get("configurable") or {}).get("thread_id")),
    }
    get_state = getattr(executor, "get_state", None)
    if not callable(get_state):
        diagnostics["state_error"] = "executor does not expose get_state"
        return diagnostics
    try:
        snapshot = get_state(config)
        values = getattr(snapshot, "values", None)
        if not isinstance(values, dict):
            diagnostics["state_error"] = "state snapshot has no dict values"
            return diagnostics
        messages = values.get("messages")
        if isinstance(messages, list):
            diagnostics.update(_message_diagnostics(messages))
            diagnostics["readable_trace"] = format_agent_diagnostics(diagnostics)
        else:
            diagnostics["state_keys"] = sorted(str(key) for key in values.keys())
    except Exception as state_exc:
        diagnostics["state_error"] = f"{type(state_exc).__name__}: {state_exc}"
    return diagnostics


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
    config = attach_streaming_callbacks(config)
    try:
        recursion_limit = max(25, int(os.getenv("AGENT_RECURSION_LIMIT", "60")))
    except (TypeError, ValueError):
        recursion_limit = 60
    config.setdefault("recursion_limit", recursion_limit)
    try:
        return executor.invoke(msg_payload, config=config)
    except Exception as exc:
        text = str(exc).lower()
        # A 400 about tool_call / tool-message ordering means the conversation
        # history is corrupted (an assistant tool_call without its response),
        # NOT a payload-shape mismatch — retrying the legacy payload would just
        # re-send the same broken state. Only fall back for true shape errors.
        message_sequence_error = (
            "tool_call" in text or "tool_calls" in text or "tool_call_id" in text or "tool message" in text
        )
        payload_shape_error = (not message_sequence_error) and (
            ("input" in text and "messages" in text)
            or ("invalid" in text and "messages" in text)
            or ("missing" in text and "messages" in text)
        )
        if payload_shape_error:
            return executor.invoke(legacy_payload, config=config)
        if _is_graph_recursion_error(exc):
            diagnostics = _agent_state_diagnostics(executor, config=config, exc=exc)
            logger.error(
                "Agent invocation hit recursion limit: %s",
                json.dumps(diagnostics, ensure_ascii=True, default=str),
            )
            raise AgentInvocationError(
                "Agent graph recursion limit reached before a final answer.",
                diagnostics=diagnostics,
            ) from exc
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

def _make_history_repair_middleware() -> Any:
    """Build a wrap_model_call middleware that repairs invalid tool-call history.

    A dangling assistant ``tool_calls`` message (one whose tool response never
    got written, e.g. after an interrupted run) otherwise poisons every later
    turn on the thread.  This sanitizes the outgoing message list before each
    model call so such a thread self-heals (see runtime_utils.repair_tool_call_sequence).
    """
    from langchain.agents.middleware import wrap_model_call
    from agent_runtime.runtime_utils import repair_tool_call_sequence

    @wrap_model_call
    def repair_history(request: Any, handler: Any) -> Any:
        fixed, changed = repair_tool_call_sequence(request.messages)
        if changed:
            request = request.override(messages=fixed)
        return handler(request)

    return repair_history


def build_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tool_strategy: str = "granular",
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    enabled_search_methods: Optional[List[str]] = None,
    allowed_tool_names: Optional[List[str]] = None,
    preloaded_tools: Optional[List[Any]] = None,
    system_prompt_override: Optional[str] = None,
    agent_name: str = "rag_agent",
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
    session_id: Optional[str] = None,
    skill_roots: Optional[List[str]] = None,
) -> Any:
    """Create a LangGraph agent (``create_agent``) wired with the given tools."""
    if preloaded_tools is not None:
        tools = preloaded_tools
    else:
        tools = _collect_tools(
            tool_strategy=tool_strategy,
            include_mcp_tools=include_mcp_tools,
            mcp_modules=mcp_modules,
            enabled_search_methods=enabled_search_methods,
            session_id=session_id,
            skill_roots=skill_roots,
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

    # LangGraph agent (langchain>=1.0). ``create_agent`` returns a compiled
    # StateGraph whose invocation accepts ``{"messages": [...]}`` and returns the
    # same shape — which ``runtime_utils`` already parses. Recursion is bounded by
    # LangGraph's ``recursion_limit`` (set per invocation via ``agent_config``);
    # a ``GraphRecursionError`` surfaces as ``AgentInvocationError`` with
    # diagnostics (see ``invoke_agent_with_payload_fallback``).
    try:
        from langchain.agents import create_agent
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "Missing compatible LangChain dependencies. Install `langchain>=1.0`, "
            "`langchain-core`, `langgraph`, and `langchain-openai`."
        ) from exc
    return create_agent(
        model=active_llm,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        middleware=[_make_history_repair_middleware()],
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
    enabled_search_methods: Optional[List[str]] = None,
    allowed_tool_names: Optional[List[str]] = None,
    preloaded_tools: Optional[List[Any]] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
    session_id: Optional[str] = None,
    skill_roots: Optional[List[str]] = None,
) -> Any:
    return build_agent_executor(
        llm=llm,
        verbose=verbose,
        return_intermediate_steps=return_intermediate_steps,
        tool_strategy=tool_strategy,
        include_mcp_tools=include_mcp_tools,
        mcp_modules=mcp_modules,
        enabled_search_methods=enabled_search_methods,
        allowed_tool_names=allowed_tool_names,
        preloaded_tools=preloaded_tools,
        system_prompt_override=SEARCH_AGENT_PROMPT,
        agent_name="search_agent",
        checkpointer=checkpointer,
        session_id=session_id,
        skill_roots=skill_roots,
    )


def build_analysis_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    checkpointer: Optional[Any] = None,
    skill_roots: Optional[List[str]] = None,
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
        skill_roots=skill_roots,
    )


def build_direct_answer_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
    skill_roots: Optional[List[str]] = None,
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
        skill_roots=skill_roots,
    )


def build_code_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tools: Optional[List[Any]] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
    skill_roots: Optional[List[str]] = None,
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
        skill_roots=skill_roots,
    )


def build_orchestrator_agent_executor(
    *,
    llm: Optional[Any] = None,
    verbose: bool = False,
    return_intermediate_steps: bool = True,
    tools: Optional[List[Any]] = None,
    checkpointer: Optional[Any] = DEFAULT_CHECKPOINTER,
    skill_roots: Optional[List[str]] = None,
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
        skill_roots=skill_roots,
    )
