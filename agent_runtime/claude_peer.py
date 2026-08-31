"""Sandboxed `claude` (Claude Code) backend for the code peer (``AGENT_CODE_PEER=claude``).

Sibling of :mod:`agent_runtime.opencode_peer`, and deliberately its twin: one
**fresh hardened container per run** (read-only rootfs, dropped capabilities,
no-new-privileges, cpu/mem/pid limits, ``/work`` the only writable mount), the
agentic CLI iterating internally — write code, run it, read the error, retry —
and everything it leaves in the work dir persisted to the agent file store as
downloadable artifacts, exactly like ``execute_code`` output.

Two differences from the opencode backend, both forced by what the CLI talks to:

* **Auth is Anthropic's, not the deployment's OpenAI-compatible endpoint.** There
  is no provider config file to generate: Claude Code reads ``ANTHROPIC_API_KEY``
  (and honours ``ANTHROPIC_BASE_URL`` for a gateway). The key travels to the
  container by NAME only, so it never appears in the argv or in the work dir
  that gets persisted as artifacts.
* **The model is Anthropic's**, named by alias (``sonnet``/``opus``) or full id,
  independent of ``VLLM_*``/``OPENAI_*``. A deployment can therefore run its
  answers on one provider and its code peer on another.

The flags here are the ones this CLI actually has — checked against the version
the image installs rather than assumed. ``--max-turns`` does NOT exist in 2.1.x
and an unknown flag makes the CLI exit non-zero, so the agentic loop is bounded
by the run timeout and the container, not by a turn count. ``--bare`` skips
hooks, LSP, plugin sync, auto-memory, keychain reads and CLAUDE.md discovery,
and pins auth to ``ANTHROPIC_API_KEY`` — all of which a throwaway container
wants, and one of which (keychain) it cannot have.

``--dangerously-skip-permissions`` is required: there is no TTY to approve tool
use, so without it every run stalls on the first permission prompt. Its own help
recommends it "only for sandboxes with no internet access", and this sandbox HAS
network — it must reach the Anthropic API. The container is the mitigation, not
the flag: read-only root, no capabilities, non-root user, throwaway work dir.
That is the same trade the opencode peer already makes.

The image must have Claude Code installed — see ``Dockerfile.claude`` at the
repo root; override the name via ``AGENT_CLAUDE_IMAGE``. Under
Docker-out-of-Docker the work dir must live on the host-shared bind mount
(``AGENT_CODE_EXEC_WORK_ROOT``), same as the execute_code sandbox.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_runtime.code_execution import _clip, _host_user, _work_root
from agent_runtime.opencode_peer import (
    CODE_PEER_ENV,
    _build_peer_prompt,
    _persist_artifacts,
    _stage_conversation_files,
    _strip_ansi,
)

DEFAULT_CLAUDE_IMAGE = "agent-claude:latest"
# Agentic write->run->debug loops are much slower than a single execute_code run.
DEFAULT_CLAUDE_TIMEOUT = 600
DEFAULT_CLAUDE_MEMORY = "2g"
DEFAULT_CLAUDE_CPUS = "2.0"
DEFAULT_CLAUDE_PIDS = "1024"
# An alias, not a pinned id: the CLI resolves it to the current model, so the
# sandbox does not silently pin itself to a retired one.
DEFAULT_CLAUDE_MODEL = "sonnet"

# The key travels via this container env var, passed by NAME so its value never
# lands in the argv (visible in `docker ps` / `ps`) or on the persisted work dir.
_API_KEY_ENV = "ANTHROPIC_API_KEY"

_ACCEPTED_FLAG_VALUES = {"claude", "claude-code", "claude_code"}


def is_claude_peer_enabled() -> bool:
    """Whether the code peer should run Claude Code instead of the LangChain agent."""
    return (os.getenv(CODE_PEER_ENV) or "").strip().lower() in _ACCEPTED_FLAG_VALUES


def resolve_claude_settings() -> Dict[str, Optional[str]]:
    """Model / API key / base URL for the Claude Code peer.

    Deliberately independent of the ``VLLM_*``/``OPENAI_*`` chain that answers
    the user: this peer talks to Anthropic, so borrowing the deployment's
    OpenAI key would send a key to a host that cannot use it and fail with an
    authentication error naming the wrong provider.
    """
    key = (os.getenv("AGENT_CLAUDE_API_KEY") or os.getenv(_API_KEY_ENV) or "").strip()
    base = (os.getenv("AGENT_CLAUDE_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL") or "").strip()
    model = (os.getenv("AGENT_CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL).strip()
    return {"model": model or DEFAULT_CLAUDE_MODEL, "api_key": key or None, "base_url": base or None}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _timeout_seconds() -> int:
    return max(30, _int_env("AGENT_CLAUDE_TIMEOUT", DEFAULT_CLAUDE_TIMEOUT))


def build_docker_argv(work: Path, name: str, model: str, prompt: str,
                      base_url: Optional[str] = None) -> List[str]:
    """``docker run`` argv for one Claude Code run.

    Hardened like the execute_code sandbox but WITH network — the CLI is useless
    without the Anthropic API. ``HOME=/work`` so all CLI state lands in the
    throwaway work dir (dot-dirs are excluded from artifact persistence).
    """
    argv = [
        "docker", "run", "--rm", "--init", "--name", name,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--memory", os.getenv("AGENT_CLAUDE_MEMORY", DEFAULT_CLAUDE_MEMORY),
        "--cpus", os.getenv("AGENT_CLAUDE_CPUS", DEFAULT_CLAUDE_CPUS),
        "--pids-limit", os.getenv("AGENT_CLAUDE_PIDS", DEFAULT_CLAUDE_PIDS),
        "--workdir", "/work",
        "--tmpfs", "/tmp:rw,size=256m,exec",
        "--env", "HOME=/work",
        "--env", "CLAUDE_CONFIG_DIR=/work/.claude",
        # Nothing about a throwaway sandbox should phone home or self-update
        # mid-run: an autoupdate would change the CLI under a running analysis.
        "--env", "DISABLE_AUTOUPDATER=1",
        "--env", "DISABLE_TELEMETRY=1",
        "--env", "DISABLE_ERROR_REPORTING=1",
        "--env", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
        # Name-only form: docker copies the value from the client process env
        # (set by run_claude), so the key never appears in the argv.
        "--env", _API_KEY_ENV,
        "-v", f"{work}:/work:rw",
    ]
    if base_url:
        argv += ["--env", f"ANTHROPIC_BASE_URL={base_url}"]
    network = (os.getenv("AGENT_CLAUDE_NETWORK") or "").strip()
    if network:
        argv += ["--network", network]
    user = _host_user()
    if user:
        argv += ["--user", user]
    image = os.getenv("AGENT_CLAUDE_IMAGE", DEFAULT_CLAUDE_IMAGE)
    argv += [
        image, "claude",
        "--print",                          # non-interactive: answer and exit
        "--output-format", "json",          # an envelope with result + cost, not bare text
        "--bare",                           # no hooks/LSP/plugins/keychain/CLAUDE.md
        "--dangerously-skip-permissions",   # no TTY to approve tool use; see module docstring
        "--model", model,
        prompt,
    ]
    return argv


def parse_cli_output(stdout: str) -> Dict[str, Any]:
    """Pull the answer out of ``--output-format json``, tolerating anything else.

    The envelope carries what a caller wants to report — the text, whether the
    run errored, what it cost, how many turns it took. If it is not JSON (an
    early crash, a usage message), the raw text is still the best answer
    available, so this never raises and never returns nothing.
    """
    text = _strip_ansi(stdout or "").strip()
    if not text:
        return {"answer": "", "envelope": None}
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return {"answer": text, "envelope": None}
    if isinstance(obj, list):                      # stream-json, if someone overrides it
        obj = next((x for x in reversed(obj) if isinstance(x, dict) and "result" in x), None)
        if obj is None:
            return {"answer": text, "envelope": None}
    if not isinstance(obj, dict):
        return {"answer": text, "envelope": None}
    answer = obj.get("result")
    if not isinstance(answer, str):
        answer = text
    return {
        "answer": answer,
        "envelope": {k: obj.get(k) for k in
                     ("is_error", "subtype", "num_turns", "total_cost_usd", "duration_ms",
                      "session_id") if k in obj},
    }


def run_claude(
    prompt: str,
    *,
    input_file_ids: Optional[List[str]] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """One sandboxed ``claude --print`` run; returns a JSON-serializable result dict."""
    settings = resolve_claude_settings()
    model = str(settings["model"])
    if not settings["api_key"]:
        return {
            "ok": False, "exit_code": None, "answer": "", "stderr": "", "timed_out": False,
            "error": (f"{_API_KEY_ENV} (or AGENT_CLAUDE_API_KEY) is required for the Claude Code "
                      "peer. Set it in the deployment env; it is not shared with the "
                      "OpenAI-compatible endpoint that answers the user."),
            "artifacts": [], "backend": "claude-docker", "model": model,
        }
    timeout = int(timeout or _timeout_seconds())
    try:
        work = Path(tempfile.mkdtemp(prefix="agentcc_", dir=_work_root()))
    except OSError as exc:
        return {
            "ok": False, "exit_code": None, "answer": "", "stderr": "", "timed_out": False,
            "error": (f"claude work dir unavailable: {exc}. "
                      "Check AGENT_CODE_EXEC_WORK_ROOT and its bind mount in the deployment."),
            "artifacts": [], "backend": "claude-docker", "model": model,
        }
    try:
        staging = _stage_conversation_files(work, input_file_ids)
        try:
            os.chmod(work, 0o777)  # non-root container user must write here
        except OSError:
            pass
        name = f"agentcc_{uuid.uuid4().hex[:12]}"
        argv = build_docker_argv(work, name, model, prompt, settings["base_url"])
        env = {**os.environ, _API_KEY_ENV: str(settings["api_key"])}
        exit_code: Optional[int] = None
        stdout, stderr, timed_out, error = "", "", False, None
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout + 5)
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "kill", name], capture_output=True)
            stdout, stderr = str(exc.stdout or ""), str(exc.stderr or "")
            timed_out, error = True, f"claude run timed out after {timeout}s"
        except FileNotFoundError:
            error = "docker executable not found"
        except Exception as exc:  # pragma: no cover - defensive
            error = f"{type(exc).__name__}: {exc}"

        parsed = parse_cli_output(stdout)
        envelope = parsed["envelope"] or {}
        artifacts = _persist_artifacts(work, set(staging["staged"]))
        result: Dict[str, Any] = {
            # The CLI can exit 0 and still report is_error in the envelope, so both count.
            "ok": (error is None and not timed_out and exit_code == 0
                   and not envelope.get("is_error")),
            "exit_code": exit_code,
            "answer": _clip(parsed["answer"]),
            "stderr": _clip(_strip_ansi(stderr).strip()),
            "timed_out": timed_out,
            "error": error,
            "artifacts": artifacts,
            "backend": "claude-docker",
            "model": model,
        }
        # What the run cost is part of the record, not a detail: this peer spends
        # on a different account from the one answering the user.
        for key in ("num_turns", "total_cost_usd", "duration_ms", "session_id"):
            if envelope.get(key) is not None:
                result[key] = envelope[key]
        if staging["staged_info"]:
            result["input_files"] = staging["staged_info"]
        if staging["errors"]:
            result["input_file_errors"] = staging["errors"]
        if staging["skipped"]:
            result["input_files_skipped"] = staging["skipped"]
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_claude_code_peer(
    query: str,
    evidence: Optional[List[Any]] = None,
    state: Optional[Dict[str, Any]] = None,
    input_file_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Code-peer adapter: the same flat shape as ``default_code_fn`` and the
    opencode peer, so synthesis and the trace pipeline stay agnostic to which
    backend produced the result."""
    from agent_runtime.streaming_trace import emit_trace_event

    staged_names: List[str] = []
    refs = [str(x).strip() for x in (input_file_ids or []) if str(x).strip()]
    if refs:
        try:
            from agent_runtime.langchain_exec_tools import _build_staging

            _, staged_info, _, _ = _build_staging(refs)
            for info in staged_info:
                staged_names.extend(info.get("available_as") or [])
        except Exception:
            staged_names = list(refs)
    prompt = _build_peer_prompt(
        query, evidence, (state or {}).get("analysis_results"),
        staged_names=staged_names or None,
    )
    call_args = {"model": resolve_claude_settings()["model"], "prompt_chars": len(prompt)}
    emit_trace_event("tool_call", {"name": "claude_run", "args": call_args}, node="code")
    result = run_claude(prompt, input_file_ids=input_file_ids)
    emit_trace_event(
        "tool_result",
        {
            "name": "claude_run",
            "content": {k: result.get(k) for k in
                        ("ok", "exit_code", "timed_out", "error", "artifacts", "backend",
                         "model", "num_turns", "total_cost_usd")},
        },
        node="code",
    )
    answer = result.get("answer") or ""
    if not result.get("ok"):
        failure = result.get("error") or f"claude exited with code {result.get('exit_code')}"
        detail = str(result.get("stderr") or "")[-2000:]
        answer = "\n\n".join(
            x for x in (f"Claude Code peer failed: {failure}", detail, answer) if x
        )
    return {
        "answer": answer,
        "tool_calls": [{"name": "claude_run", "args": call_args}],
        "tool_results": [{"name": "claude_run", "content": result}],
    }


__all__ = [
    "CODE_PEER_ENV",
    "is_claude_peer_enabled",
    "resolve_claude_settings",
    "build_docker_argv",
    "parse_cli_output",
    "run_claude",
    "run_claude_code_peer",
]
