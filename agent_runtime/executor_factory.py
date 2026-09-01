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

# Agent personas live in dedicated prompt modules. The shared SearchAgent/CodeAgent personas
# and the generic default come from agent_runtime.prompts; importing them here also keeps
# existing `from agent_runtime.executor_factory import SEARCH_AGENT_PROMPT/CODE_AGENT_PROMPT`
# callers working. Legacy personas (ANALYSIS_AGENT_PROMPT / ORCHESTRATOR_AGENT_PROMPT) live in
# agent_runtime.legacy.prompts; the supervisor's live in agent_runtime.supervisor.prompts.
from agent_runtime.prompts import (  # noqa: E402  (kept here for back-compat re-export)
    CODE_AGENT_PROMPT,
    DEFAULT_AGENT_PROMPT,
    SEARCH_AGENT_PROMPT,
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


# Deliberately NO max_tokens. qwen3.6:27b is a reasoning model: it spends its first tokens on
# `reasoning_content` and only then writes `content`, so a tight ceiling returns
# finish_reason="length" with content=None — an EMPTY answer that reads as a model failure
# rather than a truncation. Measured: max_tokens=20 produced no content at all (all 20 spent
# thinking); 800 answered in 41; unset completes normally at 57. Since the endpoint imposes no
# small default of its own, any ceiling invented here could only truncate a long answer.


def _anvilgpt_settings() -> Optional[Dict[str, Any]]:
    """AnvilGPT (Purdue RCAC, Open WebUI) config, or None when it is not configured.

    Selected by AGENT_LLM_PROVIDER=anvilgpt, so setting the variables alone never silently
    moves every request onto a different model.
    """
    if (os.getenv("AGENT_LLM_PROVIDER") or "").strip().lower() != "anvilgpt":
        return None
    key = os.getenv("ANVILGPT_KEY")
    if not key:
        raise RuntimeError(
            "AGENT_LLM_PROVIDER=anvilgpt but ANVILGPT_KEY is unset. Create a key at "
            "https://anvilgpt.rcac.purdue.edu (avatar -> Settings -> Account -> API Keys)."
        )
    # Its chat path is /api/chat/completions, so the OpenAI-compatible base is /api — which
    # normalize_openai_base_url already produces by stripping the /chat/completions suffix.
    base_url = normalize_openai_base_url(
        os.getenv("ANVILGPT_URL") or "https://anvilgpt.rcac.purdue.edu/api/chat/completions")
    return {
        "api_key": key,
        "base_url": base_url,
        # Open WebUI names models like "qwen3.6:27b" — NOT the HuggingFace "Qwen/Qwen3.6-27B"
        # form a vLLM server uses. Ask /api/models for the exact id; a wrong one 404s.
        "model": os.getenv("ANVILGPT_MODEL") or "qwen3.6:27b",
    }


def build_default_llm() -> Any:
    """Build a ``ChatOpenAI`` instance from environment variables.

    Priority: AGENT_LLM_PROVIDER=anvilgpt → VLLM_* → OPENAI_* → defaults.
    """
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:
        raise RuntimeError(
            "Missing dependency `langchain-openai`. Install it to use the default LLM builder."
        ) from exc

    anvil = _anvilgpt_settings()
    if anvil:
        return ChatOpenAI(temperature=0.0, **anvil)

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


# --- explicit per-request provider/model selection ------------------------------------
# The UI can offer a model picker, so a turn needs to be able to say which model it wants
# without changing process-wide env. Absent both, the DEFAULT IS OPENAI gpt-4o: it is what
# the deployment has been validated against, and a reasoning model's latency profile is
# quite different (see the qwen3.6:27b notes above).
DEFAULT_PROVIDER = "openai"
DEFAULT_OPENAI_MODEL = "gpt-4o-2024-11-20"

# Offered in the picker. AnvilGPT's list is fetched live because it changes and a stale
# hardcoded id 404s; these are the fallback if the fetch fails.
_ANVIL_FALLBACK_MODELS = ("qwen3.6:27b", "qwen3:32b", "qwen3-coder:30b", "qwen3-vl:32b")

# This agent ALWAYS binds function tools, and on /v1/chat/completions the legal
# reasoning_effort values depend on the model — a prefix rule got it wrong in both
# directions and produced hard 400s mid-turn. The table below is what the API actually
# answered when probed with tools attached (one row per model, three probes each, repeated):
#
#   gpt-4o*, gpt-4.1      any effort -> "Unrecognized request argument supplied:
#                         reasoning_effort".  Must not be sent at all.
#   gpt-5.6-*             NO effort -> "Function tools with reasoning_effort are not supported
#                         ... use /v1/responses or set reasoning_effort to 'none'"; 'none' ->
#                         ok; 'low' -> same rejection. So 'none' is REQUIRED, not optional.
#   gpt-5.5, gpt-5.4,     omitted or 'none' -> ok; any real level -> the same "Function tools
#   gpt-5.4-mini          with reasoning_effort are not supported" rejection.
#   gpt-5.2               omitted, 'none', or any level -> ok. The only model that can both
#                         call tools and actually think harder on request.
#   o4-mini               any level -> ok; 'none' -> "does not support 'none' with this model.
#                         Supported values are: 'low', 'medium', 'high', 'xhigh'".
#
# Two ids that were offered cannot serve a tool-using agent at all and are no longer listed:
# gpt-5.5-pro ("This is not a chat model") and gpt-5.3-chat-latest ("has been deprecated").
_EFFORT_NONE: tuple = ()
_EFFORT_ONLY_NONE = ("none",)
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh")
_TOOL_EFFORT: Dict[str, Dict[str, Any]] = {
    "gpt-4o-2024-11-20": {"options": _EFFORT_NONE, "required": None},
    "gpt-4o-mini": {"options": _EFFORT_NONE, "required": None},
    "gpt-4.1-2025-04-14": {"options": _EFFORT_NONE, "required": None},
    "gpt-5.6-luna": {"options": _EFFORT_ONLY_NONE, "required": "none"},
    "gpt-5.6-sol": {"options": _EFFORT_ONLY_NONE, "required": "none"},
    "gpt-5.6-terra": {"options": _EFFORT_ONLY_NONE, "required": "none"},
    "gpt-5.5": {"options": _EFFORT_ONLY_NONE, "required": None},
    "gpt-5.4": {"options": _EFFORT_ONLY_NONE, "required": None},
    "gpt-5.4-mini": {"options": _EFFORT_ONLY_NONE, "required": None},
    "gpt-5.2": {"options": ("none", *_EFFORT_LEVELS), "required": None},
    "o4-mini-2025-04-16": {"options": _EFFORT_LEVELS, "required": None},
}
_OPENAI_MODELS = tuple(_TOOL_EFFORT)

# Every value the API named across those probes, for validating an incoming request.
REASONING_EFFORTS = ("none", *_EFFORT_LEVELS)


def effort_options(model: Optional[str]) -> List[str]:
    """reasoning_effort values this model accepts WITH function tools attached.

    Empty means the argument must not be sent. An unknown id also returns empty: omitting
    the parameter is what every tool-capable model here tolerates, so a model added upstream
    degrades to working-without-effort rather than to a 400.
    """
    return list((_TOOL_EFFORT.get((model or "").strip()) or {}).get("options") or ())


def required_effort(model: Optional[str]) -> Optional[str]:
    """The value this model REQUIRES when tools are attached, if any (gpt-5.6-*)."""
    return (_TOOL_EFFORT.get((model or "").strip()) or {}).get("required")


def supports_reasoning_effort(model: Optional[str]) -> bool:
    """Whether `model` takes a reasoning_effort argument at all (tools attached)."""
    return bool(effort_options(model))


def resolve_effort(model: Optional[str], effort: Optional[str]) -> Optional[str]:
    """The value to actually send for (model, requested effort).

    Coerces rather than raising: the request is already in flight with tools bound, and a
    rejected argument fails the whole turn. The caller logs what it sent.
    """
    want = (effort or "").strip().lower() or None
    allowed = effort_options(model)
    forced = required_effort(model)
    if forced:
        return forced                      # gpt-5.6-*: tools are refused without it
    if not allowed or not want:
        return None
    return want if want in allowed else None


def build_llm(provider: Optional[str] = None, model: Optional[str] = None,
              reasoning_effort: Optional[str] = None) -> Any:
    """Build a chat model for an EXPLICIT provider/model, falling back to the default.

    ``provider=None and model=None`` reproduces :func:`build_default_llm` exactly, so an
    unspecified request behaves as it always has.
    """
    prov = (provider or "").strip().lower()
    effort = (reasoning_effort or "").strip().lower() or None
    if effort and effort not in REASONING_EFFORTS:
        raise ValueError(
            f"reasoning_effort={reasoning_effort!r} is not one of {', '.join(REASONING_EFFORTS)}")
    if not prov and not model and not effort:
        return build_default_llm()
    if not prov:
        # A bare model name: infer the provider from the shape rather than guessing wrong.
        # Open WebUI ids look like "qwen3.6:27b"; OpenAI's never contain a colon.
        prov = ("anthropic" if str(model).startswith("claude-")
                else "anvilgpt" if ":" in str(model) else DEFAULT_PROVIDER)

    from langchain_openai import ChatOpenAI

    if prov == "anvilgpt":
        key = os.getenv("ANVILGPT_KEY")
        if not key:
            raise ValueError(
                "provider='anvilgpt' needs ANVILGPT_KEY. Create one at "
                "https://anvilgpt.rcac.purdue.edu (avatar -> Settings -> Account -> API Keys).")
        base_url = normalize_openai_base_url(
            os.getenv("ANVILGPT_URL") or "https://anvilgpt.rcac.purdue.edu/api/chat/completions")
        # No max_tokens: qwen3.x reasons before it answers, so a ceiling truncates the thinking
        # and returns an EMPTY content rather than a short answer.
        return ChatOpenAI(model=model or os.getenv("ANVILGPT_MODEL") or "qwen3.6:27b",
                          api_key=key, base_url=base_url, temperature=0.0)
    if prov == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
        if not key and not token:
            raise ValueError(
                "provider='anthropic' needs ANTHROPIC_API_KEY, or CLAUDE_CODE_OAUTH_TOKEN "
                "from `claude setup-token` (a subscription credential scoped user:inference, "
                "which this path sends as a Bearer token).")
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ValueError(
                "provider='anthropic' needs the langchain-anthropic package "
                "(pip install langchain-anthropic)") from exc
        # No max_tokens ceiling for the same reason AnvilGPT gets none — except Anthropic
        # REQUIRES the field, so it is set high rather than left off.
        if effort:
            logger.info("reasoning_effort %r is an OpenAI parameter; not sent to Anthropic",
                        effort)
        # No temperature. The Claude 5 family REFUSES it — "`temperature` is deprecated for
        # this model", HTTP 400, which fails the turn rather than being ignored — while
        # older ids still accept it. Sending nothing works on both, and this provider is
        # here to be selected freely across the list.
        llm = ChatAnthropic(model=model or os.getenv("ANTHROPIC_MODEL")
                            or _ANTHROPIC_DEFAULT_MODEL,
                            api_key=key or "unused-when-bearer",
                            max_tokens=8192,
                            default_headers=_OAUTH_BETA_HEADER if not key else None)
        if not key:
            _use_bearer_client(llm, token)
        return llm
    if prov in ("openai", "default"):
        key = os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("provider='openai' needs OPENAI_KEY.")
        kwargs: Dict[str, Any] = {
            "model": model or os.getenv("OPENAI_CHAT_MODEL") or os.getenv("OPENAI_MODEL")
            or DEFAULT_OPENAI_MODEL,
            "api_key": key, "temperature": 0.0,
        }
        base_url = normalize_openai_base_url(os.getenv("OPENAI_BASE_URL"))
        if base_url:
            kwargs["base_url"] = base_url
        # Send only what this model accepts alongside tools, and send what it REQUIRES even
        # when the caller asked for nothing: a UI that leaves the control set while switching
        # models, or that leaves it empty for gpt-5.6, must not turn into a 400.
        sending = resolve_effort(kwargs["model"], effort)
        if sending:
            kwargs["reasoning_effort"] = sending
        if effort and sending != effort:
            logger.info("reasoning_effort %r not usable with tools on %s; sent %r",
                        effort, kwargs["model"], sending)
        return ChatOpenAI(**kwargs)
    raise ValueError(
        f"unknown provider {provider!r}; expected 'openai', 'anthropic' or 'anvilgpt'")


# Fallback ids when Anthropic's own /v1/models cannot be reached (no key, or the call
# failed). Real ids, not the CLI's `sonnet`/`opus` aliases: the Messages API takes ids,
# and the aliases only exist inside Claude Code.
# The subscription credential is a Bearer token, not an api key, and the API only accepts it
# with this beta header — verified live: the same token sent as x-api-key returns 401
# "invalid x-api-key", and sent as Authorization + this header returns 200.
_OAUTH_BETA_HEADER = {"anthropic-beta": "oauth-2025-04-20"}


def _use_bearer_client(llm: Any, token: str) -> None:
    """Point a ChatAnthropic at SDK clients built with ``auth_token``.

    ChatAnthropic exposes ``anthropic_api_key`` and ``default_headers`` but no auth_token,
    and the SDK sends ``x-api-key`` whenever it has one — which the API prefers over the
    Authorization header and rejects. Handing it clients constructed with ``auth_token``
    omits x-api-key entirely, which is the only combination that authenticates.

    Swapping the private clients is deliberate and pinned by a test: if a future
    langchain-anthropic renames them, that test fails loudly instead of every Claude turn
    failing with an auth error that names the wrong credential.
    """
    import anthropic

    for attr, factory in (("_client", anthropic.Anthropic),
                          ("_async_client", anthropic.AsyncAnthropic)):
        if not hasattr(llm, attr):
            raise ValueError(
                f"this langchain-anthropic exposes no {attr}; a subscription token cannot be "
                "attached. Set ANTHROPIC_API_KEY instead, or pin langchain-anthropic.")
        setattr(llm, attr, factory(auth_token=token, default_headers=_OAUTH_BETA_HEADER))


_ANTHROPIC_FALLBACK_MODELS = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-haiku-4-5-20251001",
)
# The default when a request names the provider but no model.
_ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"


def list_available_models(*, timeout: float = 6.0) -> Dict[str, Any]:
    """Models offerable in a picker, per provider, with the default marked.

    AnvilGPT is queried live: its catalogue changes, and offering an id it no longer serves
    produces a 404 at request time instead of an honest "unavailable" in the UI.
    """
    # The default the PICKER shows must be the default the agent would actually use, and this
    # dict used to hardcode OpenAI. With AGENT_LLM_PROVIDER=anvilgpt — a supported setting —
    # the label read "Agent default (gpt-4o-2024-11-20)" while every unqualified request ran
    # gpt-oss:120b. active_llm_description() is the one function that resolves the real
    # default, so read it rather than restating its logic and drifting from it.
    active = active_llm_description()
    out: Dict[str, Any] = {
        "default": {"provider": active.get("provider") or DEFAULT_PROVIDER,
                    "model": active.get("model") or os.getenv("OPENAI_CHAT_MODEL")
                    or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL},
        "providers": [],
    }
    openai_models = list(dict.fromkeys(
        [os.getenv("OPENAI_CHAT_MODEL") or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
         *_OPENAI_MODELS]))
    out["reasoning_efforts"] = list(REASONING_EFFORTS)
    out["providers"].append({
        "provider": "openai", "label": "OpenAI",
        "configured": bool(os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")),
        "models": [m for m in openai_models if m],
        # Which of them accept reasoning_effort, so the picker can show the control
        # conditionally instead of offering a setting that 400s.
        "reasoning_models": [m for m in openai_models if m and supports_reasoning_effort(m)],
        # The legal values PER MODEL. A single global list is what let the UI offer 'high' on
        # a model that refuses any real level once tools are attached; the picker can now
        # offer exactly what the API accepts, and nothing else.
        "effort_options": {m: effort_options(m) for m in openai_models
                           if m and effort_options(m)},
        # Models that refuse tools unless this exact value is sent, so the UI can show the
        # control as fixed rather than as a choice the user appears to have.
        "effort_required": {m: required_effort(m) for m in openai_models
                            if m and required_effort(m)},
    })

    anvil: Dict[str, Any] = {"provider": "anvilgpt", "label": "AnvilGPT (Purdue RCAC)",
                             "configured": bool(os.getenv("ANVILGPT_KEY")), "models": []}
    if anvil["configured"]:
        base = normalize_openai_base_url(
            os.getenv("ANVILGPT_URL") or "https://anvilgpt.rcac.purdue.edu/api/chat/completions")
        try:
            import requests

            resp = requests.get(f"{base}/models",
                                headers={"Authorization": f"Bearer {os.getenv('ANVILGPT_KEY')}"},
                                timeout=timeout)
            resp.raise_for_status()
            ids = [m.get("id") for m in (resp.json().get("data") or []) if m.get("id")]
            anvil["models"] = sorted(ids)
        except Exception as exc:
            logger.info("AnvilGPT model list unavailable (%s); offering known ids", exc)
            anvil["models"] = list(_ANVIL_FALLBACK_MODELS)
            anvil["stale"] = True
    out["providers"].append(anvil)

    # Anthropic, queried live for the same reason AnvilGPT is: a hardcoded id that has been
    # retired 404s at request time instead of being absent from the picker.
    # `configured` is credential presence, deliberately, and NOT a liveness probe. A probe was
    # tried and removed: on a subscription credential the limits are PER MODEL — measured the
    # same minute, claude-haiku-4-5 answered with tool calls while claude-sonnet-5 and
    # claude-opus-5 both returned 429 — so any verdict cached for a page load is wrong for
    # some model within minutes, in both directions. Credential presence does not flap; the
    # per-request error carries the API's own sentence, which is the only current truth.
    api_key = os.getenv("ANTHROPIC_API_KEY")
    token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    anthropic: Dict[str, Any] = {
        "provider": "anthropic", "label": "Anthropic (Claude)",
        "configured": bool(api_key or token), "models": [],
    }
    if token and not api_key:
        # Set expectations rather than gate: this route works (verified, with tool calls) but
        # is rate-limited per model, and third-party use draws from extra usage rather than
        # the plan's included quota.
        anthropic["caveat"] = ("subscription token: rate-limited per model, and third-party "
                               "use draws from extra usage rather than plan limits")
    if anthropic["configured"]:
        try:
            import requests

            headers = {"anthropic-version": "2023-06-01"}
            if api_key:
                headers["x-api-key"] = api_key
            else:
                headers["Authorization"] = f"Bearer {token}"
                headers.update(_OAUTH_BETA_HEADER)
            resp = requests.get("https://api.anthropic.com/v1/models",
                                headers=headers, timeout=timeout)
            resp.raise_for_status()
            ids = [m.get("id") for m in (resp.json().get("data") or []) if m.get("id")]
            anthropic["models"] = ids or list(_ANTHROPIC_FALLBACK_MODELS)
        except Exception as exc:
            logger.info("Anthropic model list unavailable (%s); offering known ids", exc)
            anthropic["models"] = list(_ANTHROPIC_FALLBACK_MODELS)
            anthropic["stale"] = True
    else:
        # Listed but disabled, with the reason the API itself gave. Absent would read as
        # "this deployment cannot speak Claude"; "configured" would advertise an option that
        # fails on the first turn. Neither is what is true.
        anthropic["models"] = list(_ANTHROPIC_FALLBACK_MODELS)
        anthropic["needs"] = "ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN"
    out["providers"].append(anthropic)
    return out


def active_llm_description() -> Dict[str, Any]:
    """Which provider/model a run would actually use — for logs and smoke tests.

    A silent fallback to OpenAI looks exactly like success, so make the choice inspectable
    rather than inferring it from whether a call worked.
    """
    anvil = _anvilgpt_settings()
    if anvil:
        return {"provider": "anvilgpt", "model": anvil["model"],
                "base_url": anvil["base_url"], "max_tokens": "unset (server default)"}
    if os.getenv("VLLM_MODEL") or os.getenv("VLLM_PROXY"):
        return {"provider": "vllm",
                "model": os.getenv("VLLM_MODEL"),
                "base_url": normalize_openai_base_url(os.getenv("VLLM_PROXY"))}
    return {"provider": "openai",
            "model": os.getenv("OPENAI_CHAT_MODEL") or os.getenv("OPENAI_MODEL"),
            "base_url": normalize_openai_base_url(os.getenv("OPENAI_BASE_URL"))}


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


# ---------------------------------------------------------------------------
# Context budget
# ---------------------------------------------------------------------------
# Nothing measured the outgoing payload before this. A peer thread accumulates every tool
# result for the life of a conversation — BoundedInMemorySaver caps the number of THREADS, not
# the messages inside one — and the deployed default (gpt-oss:120b, 65,536) has no fallback
# configured, so the overflow surfaces as a raw provider 400 in the user's chat:
#   "Input length (66,275) exceeds model's maximum context length (65,536)"
# Measured on one clay turn: 199,605. The trim is a floor under that, not a strategy.
#
# Budget in TOKENS, approximated. The model-based counter is not an option: ChatOpenAI's
# get_num_tokens_from_messages raises NotImplementedError for the AnvilGPT model ids this
# deployment defaults to, so asking for exact counts breaks the default provider outright.
CONTEXT_BUDGET_ENV = "AGENT_CONTEXT_BUDGET_TOKENS"
DEFAULT_CONTEXT_BUDGET = 48_000


def _context_budget() -> int:
    """Token ceiling for one model call, or 0 to disable trimming."""
    raw = (os.getenv(CONTEXT_BUDGET_ENV) or "").strip()
    if not raw:
        return DEFAULT_CONTEXT_BUDGET
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_BUDGET


def _make_context_budget_middleware() -> Any:
    """Keep the outgoing message list under a token budget.

    Non-destructive, like the history repairer beside it: it rewrites the OUTGOING request via
    request.override(), never the checkpointed state, so a trimmed call cannot corrupt the
    thread it was trimmed from.

    What it keeps, in priority order: every system message, the last human message, and then
    as much of the recent tail as fits. Dropping from the OLDEST end is deliberate — a
    follow-up is nearly always about recent work, and the turn ledger
    (supervisor/graph.py) re-injects the older facts in a form that costs ~130 tokens
    instead of the thousands the raw tool results cost.
    """
    from langchain.agents.middleware import wrap_model_call
    from langchain_core.messages import SystemMessage
    from langchain_core.messages.utils import count_tokens_approximately

    budget = _context_budget()

    @wrap_model_call
    def budget_context(request: Any, handler: Any) -> Any:
        if budget <= 0:
            return handler(request)
        messages = list(getattr(request, "messages", None) or [])
        if not messages:
            return handler(request)
        try:
            total = count_tokens_approximately(messages)
        except Exception:  # noqa: BLE001 - counting must never break a call
            return handler(request)
        if total <= budget:
            return handler(request)

        pinned = [m for m in messages if isinstance(m, SystemMessage)]
        rest = [m for m in messages if not isinstance(m, SystemMessage)]
        # The current question is the one message whose loss makes the call pointless.
        tail = rest[-1:] if rest else []
        body = rest[:-1] if rest else []

        try:
            used = count_tokens_approximately(pinned + tail)
        except Exception:  # noqa: BLE001
            return handler(request)
        kept: List[Any] = []
        for msg in reversed(body):
            try:
                size = count_tokens_approximately([msg])
            except Exception:  # noqa: BLE001
                break
            if used + size > budget:
                break
            kept.append(msg)
            used += size
        kept.reverse()

        trimmed = [*pinned, *kept, *tail]
        # A tool result whose call was dropped is a 400 on most providers; the repair
        # middleware beside this one exists for exactly that, so hand it a coherent list.
        try:
            from agent_runtime.runtime_utils import repair_tool_call_sequence

            trimmed, _ = repair_tool_call_sequence(trimmed)
        except Exception:  # noqa: BLE001
            pass
        logger.info(
            "context budget: trimmed %d message(s), ~%d -> ~%d tokens (budget %d)",
            len(messages) - len(trimmed), total, used, budget,
        )
        return handler(request.override(messages=trimmed))

    return budget_context


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

    system_prompt = system_prompt_override or DEFAULT_AGENT_PROMPT

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
        # Order matters: repair first so the budgeter sees a coherent list, then budget.
        middleware=[_make_history_repair_middleware(), _make_context_budget_middleware()],
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
