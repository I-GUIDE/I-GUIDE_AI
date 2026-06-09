"""Sandboxed code execution for the agents (container-per-run).

Lets the code / analysis agents **run and debug** code in an isolated sandbox.
The default backend launches a **fresh, hardened container per run**:

  docker run --rm --network none --read-only --cap-drop ALL
    --security-opt no-new-privileges --memory … --cpus … --pids-limit …
    --tmpfs /tmp --user <host-uid> -v <workdir>:/work:rw <image> python /work/script.py

i.e. no network, read-only root filesystem, dropped capabilities, no privilege
escalation, CPU/memory/pid limits, a wall-clock timeout, and the *only* writable
location is a throwaway work dir whose files are persisted as output artifacts.

A ``local`` subprocess backend exists for development only — it is **NOT a
sandbox** (it runs on the host) and must be opted into explicitly.

Gated by ``AGENT_CODE_EXEC`` (off by default). Treat all executed code as
untrusted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_IMAGE = os.getenv("AGENT_CODE_EXEC_IMAGE", "python:3.11-slim")
DEFAULT_TIMEOUT = int(os.getenv("AGENT_CODE_EXEC_TIMEOUT", "60"))
DEFAULT_MEMORY = os.getenv("AGENT_CODE_EXEC_MEMORY", "512m")
DEFAULT_CPUS = os.getenv("AGENT_CODE_EXEC_CPUS", "1.0")
DEFAULT_PIDS = os.getenv("AGENT_CODE_EXEC_PIDS", "256")
MAX_OUTPUT_CHARS = 20_000
MAX_ARTIFACTS = 20


def is_code_exec_enabled() -> bool:
    """Whether code execution is enabled (``AGENT_CODE_EXEC`` truthy)."""
    return (os.getenv("AGENT_CODE_EXEC") or "").strip().lower() in {"1", "true", "yes", "on"}


def _clip(text: Any, limit: int = MAX_OUTPUT_CHARS) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else (s[:limit] + f"\n…[truncated {len(s) - limit} chars]")


@dataclass
class ExecResult:
    exit_code: Optional[int]
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    backend: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.exit_code == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "error": self.error,
            "artifacts": self.artifacts,
            "backend": self.backend,
        }


def _persist_artifacts(work: Path, exclude: set) -> List[Dict[str, Any]]:
    """Persist files the run created in *work* to the agent file store."""
    from agent_runtime.file_store import create_output_file_from_path

    artifacts: List[Dict[str, Any]] = []
    for path in sorted(work.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(work)
        if str(rel) in exclude or (rel.parts and rel.parts[0] == "__pycache__"):
            continue
        if len(artifacts) >= MAX_ARTIFACTS:
            break
        try:
            rec = create_output_file_from_path(path, filename=path.name)
            artifacts.append(
                {
                    "file_id": rec["file_id"],
                    "filename": rec["filename"],
                    "download_url": rec.get("download_url"),
                    "size_bytes": rec.get("size_bytes"),
                }
            )
        except Exception:
            continue
    return artifacts


def _host_user() -> Optional[str]:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if callable(getuid) and callable(getgid):
        return f"{getuid()}:{getgid()}"
    return None


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

class CodeExecutor:
    """Base: write code to a throwaway dir, run it, persist artifacts, clean up."""

    backend = "base"

    def execute(self, code: str, *, language: str = "python", timeout: Optional[int] = None,
                env: Optional[Dict[str, str]] = None) -> ExecResult:
        if (language or "python").lower() != "python":
            return ExecResult(exit_code=None, error=f"unsupported language: {language}", backend=self.backend)
        timeout = int(timeout or DEFAULT_TIMEOUT)
        work = Path(tempfile.mkdtemp(prefix="agentexec_"))
        try:
            (work / "script.py").write_text(code or "", encoding="utf-8")
            try:
                os.chmod(work, 0o777)  # let a non-root container user write outputs
            except OSError:
                pass
            exit_code, stdout, stderr, timed_out, error = self._run(work, timeout)
            artifacts = _persist_artifacts(work, {"script.py"})
            return ExecResult(exit_code, _clip(stdout), _clip(stderr), timed_out, error, artifacts, self.backend)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, work: Path, timeout: int) -> Tuple[Optional[int], str, str, bool, Optional[str]]:
        raise NotImplementedError


class DockerCodeExecutor(CodeExecutor):
    """Container-per-run sandbox (the production backend)."""

    backend = "docker"

    def __init__(self, *, image: str = DEFAULT_IMAGE, memory: str = DEFAULT_MEMORY,
                 cpus: str = DEFAULT_CPUS, pids: str = DEFAULT_PIDS) -> None:
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids = pids

    def build_argv(self, work: Path, name: str) -> List[str]:
        argv = [
            "docker", "run", "--rm", "--init", "--name", name,
            "--network", "none",            # no network
            "--read-only",                  # read-only root fs
            "--cap-drop", "ALL",            # drop all capabilities
            "--security-opt", "no-new-privileges",
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", self.pids,
            "--workdir", "/work",
            "--tmpfs", "/tmp:rw,size=64m,exec",
            "--env", "HOME=/tmp",
            "-v", f"{work}:/work:rw",       # only writable mount
        ]
        user = _host_user()
        if user:
            argv += ["--user", user]
        argv += [self.image, "python", "/work/script.py"]
        return argv

    def _run(self, work: Path, timeout: int):
        name = f"agentexec_{uuid.uuid4().hex[:12]}"
        argv = self.build_argv(work, name)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 5)
            return proc.returncode, proc.stdout, proc.stderr, False, None
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "kill", name], capture_output=True)
            return None, (exc.stdout or ""), (exc.stderr or ""), True, "execution timed out"
        except FileNotFoundError:
            return None, "", "", False, "docker executable not found"
        except Exception as exc:  # pragma: no cover - defensive
            return None, "", "", False, f"{type(exc).__name__}: {exc}"


class LocalSubprocessExecutor(CodeExecutor):
    """DEV ONLY — runs on the host, NOT a sandbox. Opt-in via AGENT_CODE_EXEC_BACKEND=local."""

    backend = "local-unsafe"

    def _run(self, work: Path, timeout: int):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(work)}
        try:
            proc = subprocess.run(
                [sys.executable or "python", "script.py"],
                cwd=str(work), env=env, capture_output=True, text=True, timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr, False, None
        except subprocess.TimeoutExpired as exc:
            return None, (exc.stdout or ""), (exc.stderr or ""), True, "execution timed out"
        except Exception as exc:  # pragma: no cover - defensive
            return None, "", "", False, f"{type(exc).__name__}: {exc}"


class DisabledExecutor(CodeExecutor):
    """Returned when the requested backend is unavailable; never runs code."""

    backend = "disabled"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _run(self, work: Path, timeout: int):
        return None, "", "", False, self._reason


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def get_code_executor() -> CodeExecutor:
    """Pick an executor from env.

    ``AGENT_CODE_EXEC_BACKEND``: ``docker`` (default) | ``local`` (UNSAFE, dev).
    Docker is *not* silently downgraded to local — if docker is unavailable a
    DisabledExecutor is returned so untrusted code is never run unsandboxed.
    """
    backend = (os.getenv("AGENT_CODE_EXEC_BACKEND") or "docker").strip().lower()
    if backend == "local":
        return LocalSubprocessExecutor()
    if backend == "docker":
        if _docker_available():
            return DockerCodeExecutor()
        return DisabledExecutor(
            "docker backend unavailable (docker not found). Set AGENT_CODE_EXEC_BACKEND=local "
            "to run on the host for development (UNSAFE — not a sandbox)."
        )
    return DisabledExecutor(f"unknown AGENT_CODE_EXEC_BACKEND: {backend}")


__all__ = [
    "ExecResult",
    "CodeExecutor",
    "DockerCodeExecutor",
    "LocalSubprocessExecutor",
    "DisabledExecutor",
    "get_code_executor",
    "is_code_exec_enabled",
]
