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
import re
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
DEFAULT_INSTALL_TIMEOUT = int(os.getenv("AGENT_CODE_EXEC_INSTALL_TIMEOUT", "300"))
DEFAULT_MEMORY = os.getenv("AGENT_CODE_EXEC_MEMORY", "512m")
# The deps-install phase needs more headroom than execution: building/installing
# the scientific stack (numpy/pandas/scipy) under the 512m exec limit was getting
# OOM-killed (exit 137). Give install its own, larger budget.
DEFAULT_INSTALL_MEMORY = os.getenv("AGENT_CODE_EXEC_INSTALL_MEMORY", "1g")
DEFAULT_CPUS = os.getenv("AGENT_CODE_EXEC_CPUS", "1.0")
DEFAULT_PIDS = os.getenv("AGENT_CODE_EXEC_PIDS", "256")
MAX_OUTPUT_CHARS = 20_000
MAX_ARTIFACTS = 20
MAX_DEPS = 50
# Deps install under this dir inside the work dir; added to PYTHONPATH for the run.
DEPS_DIRNAME = ".deps"
# pip's scratch space during install — kept on the host-backed work dir (real disk)
# rather than the small in-container tmpfs, so installing big wheels (numpy/matplotlib)
# doesn't run out of space.
PIPTMP_DIRNAME = ".piptmp"
# When set, per-run sandbox work dirs are created under this path instead of the
# system temp dir. This is REQUIRED for Docker-out-of-Docker (agent runs in a
# container, shelling out to the host daemon): the path must exist at the SAME
# absolute location on the host (a shared bind mount), so the `docker run -v <work>:/work`
# the agent issues resolves to a real host directory the daemon can mount.
WORK_ROOT_ENV = "AGENT_CODE_EXEC_WORK_ROOT"

# A conservative pip requirement spec: name[extras]version-specifiers. No flags,
# no whitespace, no shell/path characters — deps are passed as argv (never a shell).
_DEP_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?"
    r"([<>=!~]=?[A-Za-z0-9._*+!-]+(,[<>=!~]=?[A-Za-z0-9._*+!-]+)*)?$"
)


# Heavy frameworks that take minutes to install and are rarely needed for the
# geospatial/data tasks this sandbox serves. Rejected unless an explicit
# AGENT_CODE_EXEC_PIP_ALLOW opts them in — this stops an ungrounded request (e.g.
# "forecast with an LSTM") from burning the whole install budget before the agent
# can recover. Tune via AGENT_CODE_EXEC_PIP_DENY (comma-separated, replaces this set).
_DEFAULT_PIP_DENY = {
    "tensorflow", "tensorflow-gpu", "tensorflow-cpu", "torch", "torchvision",
    "torchaudio", "jax", "jaxlib", "transformers", "keras", "mxnet", "paddlepaddle",
}


def _pip_denylist() -> set:
    raw = os.getenv("AGENT_CODE_EXEC_PIP_DENY")
    if raw is None:
        return _DEFAULT_PIP_DENY
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _sanitize_deps(dependencies: Optional[List[Any]]) -> Tuple[List[str], List[str]]:
    """Return (allowed, rejected) pip specs. Rejects flags/odd specs; honors an explicit
    allowlist (AGENT_CODE_EXEC_PIP_ALLOW), else a denylist of heavy frameworks."""
    if not dependencies:
        return [], []
    allow = {x.strip().lower() for x in (os.getenv("AGENT_CODE_EXEC_PIP_ALLOW") or "").split(",") if x.strip()}
    deny = _pip_denylist()
    allowed: List[str] = []
    rejected: List[str] = []
    for raw in dependencies:
        spec = str(raw).strip()
        if not spec or spec.startswith("-") or any(c.isspace() for c in spec) or not _DEP_RE.match(spec):
            rejected.append(spec)
            continue
        name = re.split(r"[<>=!~\[]", spec, maxsplit=1)[0].strip().lower()
        if allow:
            if name not in allow:
                rejected.append(spec)
                continue
        elif name in deny:
            rejected.append(spec)
            continue
        allowed.append(spec)
    return allowed[:MAX_DEPS], rejected


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
    code: str = ""   # the executed source (also saved as a downloadable artifact)
    installed: List[str] = field(default_factory=list)  # pip deps installed before the run

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
            "code": self.code,
            "installed": self.installed,
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
        if str(rel) in exclude or (rel.parts and rel.parts[0] in {"__pycache__", DEPS_DIRNAME, PIPTMP_DIRNAME}):
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


def _persist_source(code: str, *, filename: str = "executed_code.py") -> List[Dict[str, Any]]:
    """Save the executed source as a downloadable output artifact."""
    from agent_runtime.file_store import create_output_file

    try:
        rec = create_output_file(filename, code or "")
        return [{
            "file_id": rec["file_id"],
            "filename": rec["filename"],
            "download_url": rec.get("download_url"),
            "size_bytes": rec.get("size_bytes"),
            "kind": "source",
        }]
    except Exception:
        return []


def _stage_inputs(work: Path, input_files: Optional[List[Dict[str, str]]]) -> Tuple[List[str], List[Dict[str, str]]]:
    """Copy requested input files into *work* so the sandboxed code can read them.

    Each spec is ``{"source": <host_path>, "dest": <relative_name>}``.  ``dest`` must
    be a plain filename (no path separators / traversal) that stays inside *work* —
    *work* is the only writable mount, so files placed here appear at ``/work/<dest>``
    inside the container (the run's working directory).

    Returns ``(staged_dest_names, errors)``.
    """
    staged: List[str] = []
    errors: List[Dict[str, str]] = []
    work_resolved = work.resolve()
    for spec in input_files or []:
        source = str((spec or {}).get("source") or "").strip()
        dest = str((spec or {}).get("dest") or "").strip()
        if not source or not dest:
            continue
        if "/" in dest or "\\" in dest or dest in {".", ".."}:
            errors.append({"dest": dest, "error": "invalid destination name"})
            continue
        target = (work / dest).resolve()
        if target.parent != work_resolved:
            errors.append({"dest": dest, "error": "destination escapes work dir"})
            continue
        src = Path(source).expanduser()
        if not src.is_file():
            errors.append({"source": source, "error": "source file not found"})
            continue
        try:
            shutil.copyfile(src, target)
            staged.append(dest)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append({"source": source, "dest": dest, "error": f"{type(exc).__name__}: {exc}"})
    return staged, errors


def _work_root() -> Optional[str]:
    """Directory under which per-run work dirs are created (None = system temp).

    Set via ``AGENT_CODE_EXEC_WORK_ROOT`` for Docker-out-of-Docker so the work dir
    sits on a host-shared path the host daemon can bind-mount.
    """
    root = (os.getenv(WORK_ROOT_ENV) or "").strip()
    if not root:
        return None
    try:
        os.makedirs(root, exist_ok=True)
        return root
    except OSError:
        return None


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
                env: Optional[Dict[str, str]] = None,
                dependencies: Optional[List[Any]] = None,
                input_files: Optional[List[Dict[str, str]]] = None) -> ExecResult:
        if (language or "python").lower() != "python":
            return ExecResult(exit_code=None, error=f"unsupported language: {language}",
                              backend=self.backend, code=(code or ""))
        timeout = int(timeout or DEFAULT_TIMEOUT)
        deps, rejected = _sanitize_deps(dependencies)
        work = Path(tempfile.mkdtemp(prefix="agentexec_", dir=_work_root()))
        try:
            (work / "script.py").write_text(code or "", encoding="utf-8")
            # Stage uploaded/input files into the work dir so the code can read them.
            staged, stage_errors = _stage_inputs(work, input_files)
            try:
                os.chmod(work, 0o777)  # let a non-root container user write outputs
            except OSError:
                pass
            exit_code, stdout, stderr, timed_out, error = self._run(work, timeout, deps)
            # Output files the run produced, plus the executed source itself (downloadable).
            # Staged input files are excluded so uploads aren't re-persisted as outputs.
            artifacts = [*_persist_source(code or ""), *_persist_artifacts(work, {"script.py", *staged})]
            if rejected:
                stderr = (str(stderr or "") + f"\n[ignored unsafe dependencies: {rejected}]").strip()
            if stage_errors:
                stderr = (str(stderr or "") + f"\n[input file staging errors: {stage_errors}]").strip()
            return ExecResult(exit_code, _clip(stdout), _clip(stderr), timed_out, error,
                              artifacts, self.backend, code=(code or ""), installed=deps)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, work: Path, timeout: int,
             dependencies: Optional[List[str]] = None) -> Tuple[Optional[int], str, str, bool, Optional[str]]:
        raise NotImplementedError


class DockerCodeExecutor(CodeExecutor):
    """Container-per-run sandbox (the production backend)."""

    backend = "docker"

    def __init__(self, *, image: str = DEFAULT_IMAGE, memory: str = DEFAULT_MEMORY,
                 cpus: str = DEFAULT_CPUS, pids: str = DEFAULT_PIDS,
                 install_memory: str = DEFAULT_INSTALL_MEMORY) -> None:
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids = pids
        self.install_memory = install_memory

    def build_argv(self, work: Path, name: str) -> List[str]:
        """Execution phase: NO network, read-only rootfs, deps importable via PYTHONPATH."""
        argv = [
            "docker", "run", "--rm", "--init", "--name", name,
            "--network", "none",            # no network during execution
            "--read-only",                  # read-only root fs
            "--cap-drop", "ALL",            # drop all capabilities
            "--security-opt", "no-new-privileges",
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", self.pids,
            "--workdir", "/work",
            "--tmpfs", "/tmp:rw,size=64m,exec",
            "--env", "HOME=/tmp",
            "--env", f"PYTHONPATH=/work/{DEPS_DIRNAME}",
            "-v", f"{work}:/work:rw",       # only writable mount
        ]
        user = _host_user()
        if user:
            argv += ["--user", user]
        argv += [self.image, "python", "/work/script.py"]
        return argv

    def build_install_argv(self, work: Path, deps: List[str], name: str) -> List[str]:
        """Install phase: network ON, pip install into the shared work dir (/work/.deps)."""
        argv = [
            "docker", "run", "--rm", "--init", "--name", name,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", self.install_memory,   # larger budget than exec (avoids OOM)
            "--cpus", self.cpus,
            "--pids-limit", self.pids,
            "--workdir", "/work",
            "--tmpfs", "/tmp:rw,size=128m,exec",
            "--env", "HOME=/tmp",
            # pip scratch on the host-backed work dir (real disk), not the tmpfs.
            "--env", f"TMPDIR=/work/{PIPTMP_DIRNAME}",
            "-v", f"{work}:/work:rw",
        ]
        user = _host_user()
        if user:
            argv += ["--user", user]
        argv += [self.image, "pip", "install", "--no-cache-dir", "--target", f"/work/{DEPS_DIRNAME}", *deps]
        return argv

    def _run(self, work: Path, timeout: int, dependencies: Optional[List[str]] = None):
        # Phase 1 (deps): a separate container WITH network installs into /work/.deps.
        if dependencies:
            (work / PIPTMP_DIRNAME).mkdir(parents=True, exist_ok=True)  # pip TMPDIR (real disk)
            iname = f"agentexec_pip_{uuid.uuid4().hex[:12]}"
            try:
                inst = subprocess.run(self.build_install_argv(work, dependencies, iname),
                                      capture_output=True, text=True, timeout=DEFAULT_INSTALL_TIMEOUT + 5)
            except subprocess.TimeoutExpired as exc:
                subprocess.run(["docker", "kill", iname], capture_output=True)
                return None, (exc.stdout or ""), (exc.stderr or ""), True, "dependency install timed out"
            except FileNotFoundError:
                return None, "", "", False, "docker executable not found"
            if inst.returncode != 0:
                return None, inst.stdout, _clip(inst.stderr), False, "dependency install failed"
        # Phase 2 (exec): NO network.
        name = f"agentexec_{uuid.uuid4().hex[:12]}"
        try:
            proc = subprocess.run(self.build_argv(work, name), capture_output=True, text=True, timeout=timeout + 5)
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

    def _run(self, work: Path, timeout: int, dependencies: Optional[List[str]] = None):
        deps_dir = work / DEPS_DIRNAME
        if dependencies:
            try:
                inst = subprocess.run(
                    [sys.executable or "python", "-m", "pip", "install", "--no-cache-dir",
                     "--target", str(deps_dir), *dependencies],
                    capture_output=True, text=True, timeout=DEFAULT_INSTALL_TIMEOUT,
                )
            except subprocess.TimeoutExpired as exc:
                return None, (exc.stdout or ""), (exc.stderr or ""), True, "dependency install timed out"
            if inst.returncode != 0:
                return None, inst.stdout, _clip(inst.stderr), False, "dependency install failed"
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(work)}
        if dependencies:
            env["PYTHONPATH"] = str(deps_dir)
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

    def _run(self, work: Path, timeout: int, dependencies: Optional[List[str]] = None):
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
