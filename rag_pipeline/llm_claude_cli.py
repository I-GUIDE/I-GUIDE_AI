"""`claude` CLI backend for :func:`rag_pipeline.llm_utils.call_llm`.

DEVELOPMENT AND EXPERIMENTS ONLY.
---------------------------------
This exists so extraction batches, eval sweeps and local experiments do not bill an API.
The publication extractor alone runs over ~180 elements, and the retrieval/rerank/audit
paths call ``call_llm`` on every turn, so the recurring cost of the beta work sits here
rather than in the agent peers.

It must NOT back a deployed server. Anthropic's consumer terms restrict access "through
automated or non-human means" to API-key access, and separately forbid making the account
available to others — serving platform users through a personal subscription does both.
Deployed configuration uses self-hosted vLLM or a Console API key; ``check_not_deployed()``
is the assertion that keeps that honest in CI.

Model choice
------------
``CLAUDE_CLI_MODEL`` (default ``sonnet``) — the cost/performance balance for this
workload. Structured extraction into a fixed JSON shape may hold on ``haiku``; reserve
``opus`` for the tail that comes back unparseable. Every call records the model it used via
:func:`last_model` so a result is attributable — a recall figure produced under ``opus`` is
not comparable to one from a self-hosted 7B.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sonnet"
DEFAULT_TIMEOUT = 300

_last_model: Optional[str] = None


class ClaudeCliUnavailable(RuntimeError):
    """The `claude` executable is missing or not usable."""


def is_selected() -> bool:
    return str(os.getenv("LLM_PROVIDER") or "").strip().lower() in {"claude-cli", "claude_cli", "claude"}


def model() -> str:
    return str(os.getenv("CLAUDE_CLI_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def last_model() -> Optional[str]:
    """Model used by the most recent call, for recording in extraction/eval records."""
    return _last_model


def _timeout() -> int:
    try:
        return max(10, int(os.getenv("CLAUDE_CLI_TIMEOUT", str(DEFAULT_TIMEOUT))))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def check_not_deployed() -> None:
    """Raise when this backend is selected in something that looks like a deployment.

    Called from the provider dispatch, so a deployed server configured this way fails at
    the first LLM call with a clear reason instead of silently billing a personal
    subscription for user traffic.
    """
    if not is_selected():
        return
    marker = next((v for v in ("AGENT_DEPLOYED", "KUBERNETES_SERVICE_HOST", "ECS_CONTAINER_METADATA_URI")
                   if os.getenv(v)), None)
    if marker:
        raise RuntimeError(
            f"LLM_PROVIDER=claude-cli is a development-only backend but {marker} is set, "
            "which indicates a deployed environment. Anthropic's consumer terms restrict "
            "automated access to API-key access and forbid serving other users through a "
            "personal subscription. Use LLM_PROVIDER=vllm or openai in deployment."
        )


def available() -> bool:
    return shutil.which("claude") is not None


def use_bare() -> bool:
    """Whether to pass ``--bare``.

    ``--bare`` is what makes a run reproducible: it skips hooks, plugins, auto-memory and
    CLAUDE.md auto-discovery, so this repo's own instructions are not silently prepended to
    every extraction prompt (which would skew results AND make them depend on the checkout).

    But its help text is explicit that under ``--bare`` "Anthropic auth is strictly
    ANTHROPIC_API_KEY or apiKeyHelper ... OAuth and keychain are never read". So --bare and
    subscription auth are mutually exclusive, and the default has to follow the credential
    that is actually present:

      ANTHROPIC_API_KEY set   -> --bare  (reproducible; also the terms-compliant path for
                                          automated use)
      otherwise               -> no --bare (falls back to the interactive login, which is
                                          appropriate for a developer running batches by
                                          hand, at the cost of inheriting project context)

    Override with ``CLAUDE_CLI_BARE=0|1`` when you need to be explicit.
    """
    override = str(os.getenv("CLAUDE_CLI_BARE") or "").strip().lower()
    if override in {"0", "false", "no", "off"}:
        return False
    if override in {"1", "true", "yes", "on"}:
        return True
    return bool(str(os.getenv("ANTHROPIC_API_KEY") or "").strip())


_AUTH_HINT = (
    "claude CLI is not authenticated (or its token has expired). Options, best first:\n"
    "  (a) SUBSCRIPTION, long-lived — run `claude setup-token` once, then export the token it\n"
    "      prints as CLAUDE_CODE_OAUTH_TOKEN. Survives across sessions, so batch runs do not\n"
    "      keep dying on an expired access token. Requires CLAUDE_CLI_BARE=0 (--bare never\n"
    "      reads OAuth). Check state with `claude auth status`.\n"
    "  (b) SUBSCRIPTION, interactive — run `claude` and `/login`. Same constraint, but the\n"
    "      access token expires and has to be refreshed by hand.\n"
    "  (c) API KEY — export ANTHROPIC_API_KEY=... Works with --bare, so runs are reproducible\n"
    "      (no CLAUDE.md or hooks injected), and it is the path Anthropic's terms require for\n"
    "      automated use. Costs API credit.\n"
    "Or set LLM_PROVIDER=vllm|openai to bypass this backend entirely."
)


def _is_auth_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(s in lowered for s in (
        "not logged in", "please run /login", "oauth access token has expired",
        "failed to authenticate", "401",
    ))


def _build_argv(exe: str, prompt: str, mdl: str) -> list:
    argv = [exe, "-p", prompt, "--output-format", "json", "--model", mdl]
    if use_bare():
        argv.append("--bare")
    budget = str(os.getenv("CLAUDE_CLI_MAX_BUDGET_USD") or "").strip()
    if budget:
        argv += ["--max-budget-usd", budget]
    return argv


def call(prompt: str) -> str:
    """Run one non-interactive `claude -p` turn and return its text.

    ``--output-format json`` is used rather than plain text because the wrapper object
    carries ``is_error`` / ``subtype`` / ``result`` even on a failed run — and the CLI exits
    non-zero *while still emitting that JSON*, so the reason must be parsed before the exit
    code is judged. Reading the exit code first turns "not logged in" into an unreadable
    dump of usage counters.
    """
    global _last_model

    exe = shutil.which("claude")
    if not exe:
        raise ClaudeCliUnavailable(
            "LLM_PROVIDER=claude-cli but the `claude` executable is not on PATH. "
            "Install Claude Code, or set LLM_PROVIDER=vllm|openai."
        )

    mdl = model()
    try:
        proc = subprocess.run(_build_argv(exe, prompt, mdl), capture_output=True,
                              text=True, timeout=_timeout())
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"claude CLI timed out after {_timeout()}s (model={mdl})") from exc

    raw = (proc.stdout or "").strip()
    payload = None
    if raw:
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None

    # Parse BEFORE judging returncode: a failed run still returns a structured reason.
    if isinstance(payload, dict):
        detail = str(payload.get("result") or "")
        if payload.get("is_error") or proc.returncode != 0:
            if _is_auth_failure(detail):
                raise ClaudeCliUnavailable(f"{detail.strip()}\n\n{_AUTH_HINT}")
            raise RuntimeError(
                f"claude CLI error (model={mdl}, subtype={payload.get('subtype')}, "
                f"exit={proc.returncode}): {detail[:300]}"
            )
        if isinstance(payload.get("result"), str):
            _last_model = mdl
            return payload["result"]

    if proc.returncode != 0:
        tail = (proc.stderr or raw or "").strip()[-400:]
        if _is_auth_failure(tail):
            raise ClaudeCliUnavailable(f"{tail}\n\n{_AUTH_HINT}")
        raise RuntimeError(f"claude CLI exited {proc.returncode} (model={mdl}): {tail}")

    if not raw:
        raise RuntimeError(f"claude CLI produced no output (model={mdl})")
    logger.warning("claude CLI output had no string `result`; using it verbatim.")
    _last_model = mdl
    return raw


def preflight() -> dict:
    """One-shot diagnosis, so an auth problem is a single command away from an answer.

        python -m rag_pipeline.llm_claude_cli
    """
    info = {
        "executable": shutil.which("claude"),
        "model": model(),
        "bare": use_bare(),
        "anthropic_api_key_set": bool(str(os.getenv("ANTHROPIC_API_KEY") or "").strip()),
        "oauth_token_set": bool(str(os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()),
        "ok": False,
        "detail": "",
    }
    if not info["executable"]:
        info["detail"] = "claude not on PATH"
        return info
    try:
        info["detail"] = call("Reply with exactly: OK").strip()[:80]
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001 — reporting the failure IS the purpose
        info["detail"] = f"{type(exc).__name__}: {exc}"
    return info


if __name__ == "__main__":  # pragma: no cover
    import pprint
    os.environ.setdefault("LLM_PROVIDER", "claude-cli")
    pprint.pprint(preflight())
