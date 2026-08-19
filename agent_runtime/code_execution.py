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
# Per-run sandbox budget. 512m was too small for the real work this agent is asked to do:
# a city-scale incident CSV (~130k rows) through pandas + geopandas, a KDE/hexbin heatmap, or
# a spatial join all exceed it and die as an OOM kill with no useful stderr. 4g is generous
# for that class of job while still bounding a runaway loop far below a real server's RAM —
# raise AGENT_CODE_EXEC_MEMORY on a big host (a 60 GB box can comfortably afford 8g-16g).
DEFAULT_MEMORY = os.getenv("AGENT_CODE_EXEC_MEMORY", "4g")
# The deps-install phase needs headroom of its own: building/installing the scientific stack
# (numpy/pandas/scipy/geopandas) was getting OOM-killed (exit 137) under the old exec limit.
DEFAULT_INSTALL_MEMORY = os.getenv("AGENT_CODE_EXEC_INSTALL_MEMORY", "2g")
# 1 CPU serializes pandas/geopandas work that is trivially parallel; 2 keeps a single run
# responsive without letting one job monopolize a shared host.
DEFAULT_CPUS = os.getenv("AGENT_CODE_EXEC_CPUS", "2.0")
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


# The sandbox image ships no third-party packages, so code that imports pandas dies with
# ModuleNotFoundError unless `dependencies` was passed. Observed in every data task: the model
# omits it, burns a container run, reads the traceback, then retries the byte-identical code
# with dependencies set. Infer the obvious ones from the source instead of charging the user a
# failed run for a detail the code already states.
_IMPORT_TO_PIP = {
    "pandas": "pandas", "geopandas": "geopandas", "numpy": "numpy", "shapely": "shapely",
    "matplotlib": "matplotlib", "scipy": "scipy", "sklearn": "scikit-learn", "pyproj": "pyproj",
    "fiona": "fiona", "rasterio": "rasterio", "seaborn": "seaborn", "statsmodels": "statsmodels",
    "pyarrow": "pyarrow", "networkx": "networkx", "folium": "folium", "mapclassify": "mapclassify",
    "requests": "requests", "bs4": "beautifulsoup4", "PIL": "pillow", "openpyxl": "openpyxl",
}


def _infer_deps(code: str, declared: List[str]) -> List[str]:
    """pip names for third-party modules the code imports but nobody declared."""
    imported = set(re.findall(r"^[ \t]*(?:import|from)[ \t]+([A-Za-z_][\w.]*)", str(code or ""), re.M))
    tops = {name.split(".")[0] for name in imported}
    have = {re.split(r"[<>=!~\[]", d)[0].strip().lower() for d in declared}
    return [pip for mod, pip in _IMPORT_TO_PIP.items() if mod in tops and pip.lower() not in have]


def is_code_exec_enabled() -> bool:
    """Whether code execution is enabled. **On by default**; set ``AGENT_CODE_EXEC`` to a falsy
    value (0/false/no/off) to disable the sandboxed ``execute_code`` tool."""
    return (os.getenv("AGENT_CODE_EXEC", "1") or "").strip().lower() not in {"0", "false", "no", "off"}


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


# A container killed by a signal exits with a NEGATIVE code and usually writes nothing to
# stderr. Reported bare, that reads to the model as "it just failed", and it then invents a
# cause (observed: "failed due to dependency issues") and gives up instead of retrying. Naming
# the signal — and what usually causes it here — lets the agent choose a real next step.
_SIGNAL_DIAGNOSIS = {
    9: ("SIGKILL", "the sandbox hit its memory limit (or was killed). Reduce the data held in "
                   "memory — read in chunks, downsample, or write results incrementally."),
    11: ("SIGSEGV", "the sandbox process crashed. This is usually a native-library or memory "
                    "fault, not your logic; retry once, and if it repeats use smaller inputs or "
                    "avoid the heavy native dependency (a pure-stdlib or pandas-only version "
                    "often works)."),
    6: ("SIGABRT", "a native library aborted the process. Try a simpler approach or fewer "
                   "third-party dependencies."),
    15: ("SIGTERM", "the sandbox was terminated (time or resource limit)."),
}


def _diagnose_abnormal_exit(exit_code: Optional[int], stderr: str, error: Optional[str]) -> Optional[str]:
    """Explain a signal-killed run so the caller gets a cause, not a silent failure.

    Two conventions reach us: a negative code when the docker CLI itself is signalled, and
    128+N when the CONTAINER is signalled (docker's own convention — an OOM kill is 137).
    Both used to surface as a bare non-zero exit with empty stderr.
    """
    if error or not isinstance(exit_code, int) or exit_code == 0:
        return None
    if exit_code < 0:
        signo = -exit_code
    elif 128 < exit_code < 160:
        signo = exit_code - 128
    else:
        return None  # an ordinary non-zero exit: the traceback in stderr is the explanation
    name, hint = _SIGNAL_DIAGNOSIS.get(signo, (f"signal {signo}", "the sandbox terminated abnormally."))
    detail = f"code execution was killed by {name} (exit {exit_code}); {hint}"
    if not (stderr or "").strip():
        detail += " No stderr was produced, so nothing was written and no output files exist."
    return detail


def _describe_code(code: str) -> Optional[str]:
    """A short slug for what a script does, read from the code itself.

    Several execute_code calls in one turn all produced ``executed_code.py``, so the
    download list showed the same name three or four times with no way to tell which run
    was which. Prefer the module docstring / first comment (what the author said it does),
    then the first function name; give up rather than invent something meaningless.
    """
    text = str(code or "")
    m = re.search(r'^\s*(?:"""|\'\'\')\s*(.+)', text) or re.search(r"^\s*#\s*(.+)", text, re.M)
    if not m:
        m = re.search(r"^\s*def\s+([A-Za-z_]\w*)", text, re.M)
    if not m:
        return None
    words = re.findall(r"[A-Za-z0-9]+", m.group(1).lower())[:5]
    slug = "_".join(words)[:48].strip("_")
    return slug or None


def _persist_source(code: str, *, label: Optional[str] = None,
                    filename: Optional[str] = None) -> List[Dict[str, Any]]:
    """Save the executed source as a downloadable output artifact.

    Named for what the script does (caller-supplied ``label``, else derived from the
    code) so repeated runs in one conversation are distinguishable.
    """
    from agent_runtime.file_store import create_output_file

    if not filename:
        stem = re.sub(r"[^A-Za-z0-9]+", "_", str(label or "")).strip("_").lower()[:48]
        stem = stem or _describe_code(code) or "executed_code"
        filename = f"{stem}.py"
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


def _session_workspace(session: Optional[str]) -> Optional[Path]:
    """The durable working directory for a conversation's code, or None.

    Each run gets a throwaway dir and an ``--rm`` container, so a follow-up run used to
    start from an empty directory: the file the previous step wrote was gone, and the only
    way to continue a piece of work was to re-upload it by file_id. The CONTAINER stays
    ephemeral (that is the sandbox), but the workspace persists per conversation, so
    "now add a heatmap of that GeoJSON" can build on what the last step produced.
    """
    key = str(session or "").strip()
    if not key:
        return None
    root = _work_root() or tempfile.gettempdir()
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:64]
    try:
        path = Path(root) / f"agentws_{safe}"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None


def session_workspace_listing(session: Optional[str], *, limit: int = 25) -> List[Dict[str, Any]]:
    """What earlier runs in this conversation left behind: ``[{name, size_bytes}]``.

    A durable workspace is only useful if the model knows what is in it. Without this,
    a steer like "now do a heatmap of that" makes the peer rebuild the dataset it already
    has on disk — or claim it cannot, because nothing told it the file is there.
    """
    ws = _session_workspace(session)
    if ws is None:
        return []
    items: List[Dict[str, Any]] = []
    for p in sorted(ws.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ws)
        if rel.parts and rel.parts[0] in {"__pycache__", DEPS_DIRNAME, PIPTMP_DIRNAME}:
            continue
        try:
            items.append({"name": str(rel), "size_bytes": p.stat().st_size})
        except OSError:
            continue
        if len(items) >= limit:
            break
    return items


def _stat_map(directory: Path) -> Dict[str, Tuple[int, int]]:
    """(size, mtime_ns) per relative path — used to tell new/changed files from carried ones."""
    out: Dict[str, Tuple[int, int]] = {}
    for p in directory.rglob("*"):
        if p.is_file():
            try:
                st = p.stat()
                out[str(p.relative_to(directory))] = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
    return out


def _copy_tree(src: Path, dst: Path, *, skip: Optional[set] = None) -> None:
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(src))
        if (skip and rel in skip) or rel.split(os.sep)[0] in {"__pycache__", DEPS_DIRNAME, PIPTMP_DIRNAME}:
            continue
        target = dst / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        except OSError:
            continue


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
                input_files: Optional[List[Dict[str, str]]] = None,
                label: Optional[str] = None,
                session: Optional[str] = None) -> ExecResult:
        if (language or "python").lower() != "python":
            return ExecResult(exit_code=None, error=f"unsupported language: {language}",
                              backend=self.backend, code=(code or ""))
        timeout = int(timeout or DEFAULT_TIMEOUT)
        deps, rejected = _sanitize_deps(dependencies)
        auto = _infer_deps(code, deps)
        if auto:
            deps = [*deps, *auto]
        try:
            work = Path(tempfile.mkdtemp(prefix="agentexec_", dir=_work_root()))
        except OSError as exc:
            # A missing/unwritable work root (e.g. the AGENT_CODE_EXEC_WORK_ROOT bind mount not
            # present in this deployment) must surface as a TOOL error the agent can report —
            # never crash the whole turn/stream.
            return ExecResult(
                exit_code=None,
                error=(f"code-execution work dir unavailable: {exc}. "
                       f"Check {WORK_ROOT_ENV} and its bind mount in the deployment."),
                backend=self.backend, code=(code or ""),
            )
        try:
            # Continue this conversation's work: bring forward what earlier runs left.
            workspace = _session_workspace(session)
            carried = {}
            if workspace:
                _copy_tree(workspace, work)
                carried = _stat_map(work)
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
            unchanged = {rel for rel, sig in _stat_map(work).items()
                         if carried.get(rel) == sig}  # carried in and untouched -> not an output
            artifacts = [*_persist_source(code or "", label=label),
                         *_persist_artifacts(work, {"script.py", *staged, *unchanged})]
            if workspace:
                _copy_tree(work, workspace, skip={"script.py", *staged})
            if rejected:
                stderr = (str(stderr or "") + f"\n[ignored unsafe dependencies: {rejected}]").strip()
            if auto:
                stderr = (str(stderr or "")
                          + f"\n[installed imports you did not declare: {auto}]").strip()
            if stage_errors:
                stderr = (str(stderr or "") + f"\n[input file staging errors: {stage_errors}]").strip()
            # Signal-killed runs carry no stderr; surface a cause so the agent can react.
            error = error or _diagnose_abnormal_exit(exit_code, stderr, error)
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
