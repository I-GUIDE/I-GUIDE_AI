"""Sandboxed `claude` (Claude Code) backend for the code peer (``AGENT_CODE_PEER=claude``).

Sibling of :mod:`agent_runtime.opencode_peer`, and deliberately its twin: one
**fresh hardened container per run** (read-only rootfs, dropped capabilities,
no-new-privileges, cpu/mem/pid limits, ``/work`` the only writable mount), the
agentic CLI iterating internally — write code, run it, read the error, retry —
and everything it leaves in the work dir persisted to the agent file store as
downloadable artifacts, exactly like ``execute_code`` output.

Two differences from the opencode backend, both forced by what the CLI talks to:

* **Auth is Anthropic's, not the deployment's OpenAI-compatible endpoint.** There
  is no provider config file to generate. Either credential works and they bill
  differently: ``CLAUDE_CODE_OAUTH_TOKEN`` from ``claude setup-token``
  authenticates as a **Claude subscription** (requests count against that
  person's plan), while ``ANTHROPIC_API_KEY`` is metered API billing.
  ``ANTHROPIC_BASE_URL`` is honoured for a gateway. Whichever is used travels to
  the container by NAME only, so it never appears in the argv or in the work dir
  that gets persisted as artifacts.
* **The model is Anthropic's**, named by alias (``sonnet``/``opus``) or full id,
  independent of ``VLLM_*``/``OPENAI_*``. A deployment can therefore run its
  answers on one provider and its code peer on another.

The flags here are the ones this CLI actually has — checked against the version
the image installs rather than assumed. ``--max-turns`` does NOT exist in 2.1.x
and an unknown flag makes the CLI exit non-zero, so the agentic loop is bounded
by the run timeout and the container, not by a turn count. ``--bare`` skips
hooks, LSP, plugin sync, auto-memory, keychain reads and CLAUDE.md discovery,
all of which a throwaway container wants — but it also pins auth to
``ANTHROPIC_API_KEY`` and never reads OAuth, so it is used ONLY on the API-key
path. On the subscription path the CLAUDE.md half of that protection is
restored by :func:`neutralize_instruction_files`.

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
# node:22-slim ships a `node` user here, and the work dir is chmod 0777, so this
# uid can write everything the run needs. See sandbox_user() for why not root.
DEFAULT_SANDBOX_USER = "1000:1000"

# Credentials travel via these container env vars, passed by NAME so the value
# never lands in the argv (visible in `docker ps` / `ps`) or on the persisted
# work dir. Two kinds, and the CLI treats them very differently — see _auth_of.
_API_KEY_ENV = "ANTHROPIC_API_KEY"
_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

_ACCEPTED_FLAG_VALUES = {"claude", "claude-code", "claude_code"}

# What the picker offers. ALIASES, not pinned ids: the CLI resolves each to the
# current model of that family, so the list does not rot when an id is retired.
# `haiku` was verified against the installed CLI (an unknown alias is refused
# locally, before any API call), the others are named in its own --help.
SELECTABLE_MODELS = ("sonnet", "opus", "haiku", "fable")

# Files a Claude Code session picks up from the working directory as INSTRUCTIONS
# rather than as data. Staged conversation files are user uploads, so one named
# CLAUDE.md would be read as a brief by an agent running with tool permissions
# skipped and network access. --bare disables that discovery; subscription auth
# cannot use --bare, so the staging guard below closes the same door either way.
_INSTRUCTION_FILENAMES = {"claude.md", "claude.local.md", ".claude"}


def selects_claude(value: Optional[str]) -> bool:
    """Does this AGENT_CODE_PEER value name this backend?

    A pure predicate so the env default and a per-request override are decided by
    the SAME accepted-value set — two copies of it would drift, and the drift
    would show up as a request silently getting a different peer than it asked
    for."""
    return (value or "").strip().lower() in _ACCEPTED_FLAG_VALUES


def is_claude_peer_enabled() -> bool:
    """Whether the deployment default selects Claude Code for the code peer."""
    return selects_claude(os.getenv(CODE_PEER_ENV))


def resolve_claude_settings(model: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Model / credential / base URL for the Claude Code peer.

    Two credentials are accepted, and they are not interchangeable:

    * ``CLAUDE_CODE_OAUTH_TOKEN`` — a long-lived token from ``claude
      setup-token``, which authenticates as a **Claude subscription**. Requests
      count against that person's plan limits, not a metered API balance.
    * ``ANTHROPIC_API_KEY`` — a metered API key.

    The subscription token wins when both are set: you have to run
    ``setup-token`` on purpose, so its presence is the more deliberate signal,
    and silently billing an API key while a token sits unused is the kind of
    surprise that shows up on an invoice.

    Deliberately independent of the ``VLLM_*``/``OPENAI_*`` chain that answers
    the user: this peer talks to Anthropic, so borrowing the deployment's
    OpenAI key would send a key to a host that cannot use it and fail with an
    authentication error naming the wrong provider.
    """
    token = (os.getenv("AGENT_CLAUDE_OAUTH_TOKEN") or os.getenv(_OAUTH_TOKEN_ENV) or "").strip()
    key = (os.getenv("AGENT_CLAUDE_API_KEY") or os.getenv(_API_KEY_ENV) or "").strip()
    base = (os.getenv("AGENT_CLAUDE_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL") or "").strip()
    # A per-request model beats the deployment default. An unknown one is rejected by
    # the CLI LOCALLY — duration_api_ms 0, cost 0 — so a bad pick costs nothing but an
    # error, which is why this is not validated against a hardcoded list here.
    model = (model or os.getenv("AGENT_CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL).strip()
    credential, auth = (token, "subscription") if token else ((key, "api_key") if key else (None, None))
    return {
        "model": model or DEFAULT_CLAUDE_MODEL,
        "auth": auth,
        "credential_env": _OAUTH_TOKEN_ENV if auth == "subscription" else _API_KEY_ENV,
        "credential": credential or None,
        "base_url": base or None,
    }


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _timeout_seconds() -> int:
    return max(30, _int_env("AGENT_CLAUDE_TIMEOUT", DEFAULT_CLAUDE_TIMEOUT))


def sandbox_user() -> str:
    """The uid:gid the sandbox runs as — never root.

    ``_host_user()`` reports the AGENT process's uid, and under Docker-out-of-Docker
    that process runs as root: the compose service needs root to reach the mounted
    Docker socket. Inheriting it makes the sandbox root, and Claude Code then
    refuses outright — *"--dangerously-skip-permissions cannot be used with
    root/sudo privileges for security reasons"* — which surfaces as exit 1 with an
    empty answer and nothing pointing at the cause.

    So root is replaced with an unprivileged uid. The work dir is chmod 0777
    before the run, so any uid can write there, and the agent (root) can read back
    whatever it wrote. Override with ``AGENT_CLAUDE_USER`` if a deployment needs a
    specific one.
    """
    override = (os.getenv("AGENT_CLAUDE_USER") or "").strip()
    if override:
        return override
    user = _host_user()
    if user and not user.startswith("0:"):
        return user
    return DEFAULT_SANDBOX_USER


def build_docker_argv(work: Path, name: str, model: str, prompt: str,
                      base_url: Optional[str] = None,
                      credential_env: str = _API_KEY_ENV) -> List[str]:
    """``docker run`` argv for one Claude Code run.

    Hardened like the execute_code sandbox but WITH network — the CLI is useless
    without the Anthropic API. ``HOME=/work`` so all CLI state lands in the
    throwaway work dir (dot-dirs are excluded from artifact persistence).

    ``--bare`` is used ONLY with an API key. Its own help says that under it
    "Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper via --settings
    (OAuth and keychain are never read)" — so passing it alongside a
    subscription token would ignore the credential and fail as if none had been
    supplied. That is a silent misconfiguration, not an error message, which is
    why the flag is conditional rather than always-on.
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
        # (set by run_claude), so the credential never appears in the argv.
        "--env", credential_env,
        "-v", f"{work}:/work:rw",
    ]
    if base_url:
        argv += ["--env", f"ANTHROPIC_BASE_URL={base_url}"]
    network = (os.getenv("AGENT_CLAUDE_NETWORK") or "").strip()
    if network:
        argv += ["--network", network]
    argv += ["--user", sandbox_user()]
    image = os.getenv("AGENT_CLAUDE_IMAGE", DEFAULT_CLAUDE_IMAGE)
    argv += [
        image, "claude",
        "--print",                          # non-interactive: answer and exit
        "--output-format", "json",          # an envelope with result + cost, not bare text
    ]
    if credential_env == _API_KEY_ENV:
        argv.append("--bare")               # no hooks/LSP/plugins/keychain/CLAUDE.md
    argv += [
        "--dangerously-skip-permissions",   # no TTY to approve tool use; see module docstring
        "--model", model,
        prompt,
    ]
    return argv


def neutralize_instruction_files(work: Path) -> List[str]:
    """Rename anything in the work dir the CLI would read as INSTRUCTIONS.

    Staged conversation files are user uploads. A file called ``CLAUDE.md`` is
    not data to a Claude Code session — it is a brief, loaded automatically, for
    an agent that runs here with tool permissions skipped and network access.
    ``--bare`` turns that discovery off, but subscription auth cannot use
    ``--bare``, so the door has to be closed here as well as there.

    Renamed rather than deleted: the user uploaded it, so it stays available as
    data and as a downloadable artifact under a name that is not a directive.
    Returns the names that were moved.
    """
    moved: List[str] = []
    try:
        entries = list(work.iterdir())
    except OSError:
        return moved
    for entry in entries:
        if entry.name.lower() not in _INSTRUCTION_FILENAMES:
            continue
        target = entry.with_name(f"uploaded_{entry.name}")
        try:
            entry.rename(target)
            moved.append(entry.name)
        except OSError:
            continue
    return moved


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
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """One sandboxed ``claude --print`` run; returns a JSON-serializable result dict."""
    settings = resolve_claude_settings(model)
    model = str(settings["model"])
    if not settings["credential"]:
        return {
            "ok": False, "exit_code": None, "answer": "", "stderr": "", "timed_out": False,
            "error": (f"The Claude Code peer needs a credential: either {_OAUTH_TOKEN_ENV} "
                      f"(from `claude setup-token`, authenticating as a Claude subscription) "
                      f"or {_API_KEY_ENV} (metered API billing). Neither is set, and neither "
                      "is shared with the OpenAI-compatible endpoint that answers the user."),
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
        renamed = neutralize_instruction_files(work)
        try:
            os.chmod(work, 0o777)  # non-root container user must write here
        except OSError:
            pass
        name = f"agentcc_{uuid.uuid4().hex[:12]}"
        credential_env = str(settings["credential_env"])
        argv = build_docker_argv(work, name, model, prompt, settings["base_url"], credential_env)
        env = {**os.environ, credential_env: str(settings["credential"])}
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
        excluded = set(staging["staged"]) | {f"uploaded_{n}" for n in renamed}
        artifacts = _persist_artifacts(work, excluded)
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
            # Which account paid, in the record rather than inferred from a bill.
            "auth": settings["auth"],
        }
        if renamed:
            result["renamed_instruction_files"] = renamed
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
    model: Optional[str] = None,
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
    call_args = {"model": resolve_claude_settings(model)["model"], "prompt_chars": len(prompt)}
    emit_trace_event("tool_call", {"name": "claude_run", "args": call_args}, node="code")
    result = run_claude(prompt, input_file_ids=input_file_ids, model=model)
    emit_trace_event(
        "tool_result",
        {
            "name": "claude_run",
            "content": {k: result.get(k) for k in
                        ("ok", "exit_code", "timed_out", "error", "artifacts", "backend",
                         "model", "auth", "num_turns", "total_cost_usd")},
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
    "neutralize_instruction_files",
    "parse_cli_output",
    "run_claude",
    "run_claude_code_peer",
]
