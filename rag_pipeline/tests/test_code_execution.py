"""Tests for sandboxed code execution.

The local subprocess backend is exercised for real (host python). The Docker
backend is checked at the argv level (no Docker needed) + via the selector.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.code_execution import (
    DisabledExecutor,
    DockerCodeExecutor,
    ExecResult,
    LocalSubprocessExecutor,
    get_code_executor,
    is_code_exec_enabled,
)


# --- flag ------------------------------------------------------------------

def test_is_code_exec_enabled(monkeypatch):
    monkeypatch.delenv("AGENT_CODE_EXEC", raising=False)
    assert is_code_exec_enabled() is False
    monkeypatch.setenv("AGENT_CODE_EXEC", "1")
    assert is_code_exec_enabled() is True


# --- local backend (real execution) ---------------------------------------

def test_local_executor_runs_and_captures_stdout():
    r = LocalSubprocessExecutor().execute("print('hello-sandbox')")
    assert r.exit_code == 0 and r.ok
    assert "hello-sandbox" in r.stdout
    assert r.backend == "local-unsafe"


def test_local_executor_reports_errors():
    r = LocalSubprocessExecutor().execute("raise ValueError('boom')")
    assert r.exit_code not in (0, None) and not r.ok
    assert "boom" in r.stderr


def test_local_executor_timeout():
    r = LocalSubprocessExecutor().execute("import time; time.sleep(3)", timeout=1)
    assert r.timed_out is True and not r.ok


def test_local_executor_persists_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    r = LocalSubprocessExecutor().execute("open('result.txt', 'w').write('data')")
    assert r.ok
    assert "result.txt" in [a["filename"] for a in r.artifacts]


def test_executed_code_is_returned_and_downloadable(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    src = "print('hello-with-code')"
    r = LocalSubprocessExecutor().execute(src)
    # inline
    assert r.code == src
    assert r.to_dict()["code"] == src
    # downloadable source artifact
    source_arts = [a for a in r.artifacts if a.get("kind") == "source"]
    assert source_arts and source_arts[0]["filename"] == "executed_code.py"
    assert source_arts[0].get("download_url")


def test_unsupported_language():
    r = LocalSubprocessExecutor().execute("echo hi", language="bash")
    assert r.error and "unsupported" in r.error.lower()


# --- docker backend (argv hardening) --------------------------------------

def test_docker_argv_is_hardened():
    argv = DockerCodeExecutor(image="img:test", memory="256m", cpus="0.5").build_argv(
        Path("/tmp/work"), "agentexec_x"
    )
    s = " ".join(argv)
    assert "docker run" in s and "--rm" in argv
    assert "--network none" in s          # no network during execution
    assert "--read-only" in argv          # read-only rootfs
    assert "--cap-drop ALL" in s          # dropped caps
    assert "no-new-privileges" in s       # no privilege escalation
    assert "--memory 256m" in s and "--cpus 0.5" in s and "--pids-limit" in s
    assert "-v" in argv                   # only the work-dir bind mount
    assert "PYTHONPATH=/work/.deps" in s  # installed deps importable
    assert argv[-3:] == ["img:test", "python", "/work/script.py"]


def test_docker_install_argv():
    argv = DockerCodeExecutor(image="img:test").build_install_argv(
        Path("/tmp/work"), ["numpy", "pandas==2.2"], "agentexec_pip_x"
    )
    s = " ".join(argv)
    assert "pip install --no-cache-dir --target /work/.deps" in s
    assert "--network none" not in s             # install phase HAS network
    assert "TMPDIR=/work/.piptmp" in s           # pip scratch on real disk, not tmpfs
    assert argv[-2:] == ["numpy", "pandas==2.2"]


def test_sanitize_deps_accepts_valid_and_rejects_unsafe(monkeypatch):
    monkeypatch.delenv("AGENT_CODE_EXEC_PIP_ALLOW", raising=False)
    from agent_runtime.code_execution import _sanitize_deps

    allowed, rejected = _sanitize_deps(
        ["numpy", "pandas==2.2.0", "scikit-learn>=1.0", "fiona[s3]",
         "-rrequirements.txt", "evil; rm -rf /", "two words", "--index-url=x"]
    )
    assert allowed == ["numpy", "pandas==2.2.0", "scikit-learn>=1.0", "fiona[s3]"]
    assert "-rrequirements.txt" in rejected and "--index-url=x" in rejected
    assert "evil; rm -rf /" in rejected and "two words" in rejected


def test_sanitize_deps_allowlist(monkeypatch):
    from agent_runtime.code_execution import _sanitize_deps

    monkeypatch.setenv("AGENT_CODE_EXEC_PIP_ALLOW", "numpy,pandas")
    allowed, rejected = _sanitize_deps(["numpy", "pandas==2.2", "requests"])
    assert allowed == ["numpy", "pandas==2.2"]
    assert "requests" in rejected


# --- executor selection ----------------------------------------------------

def test_selector_local(monkeypatch):
    monkeypatch.setenv("AGENT_CODE_EXEC_BACKEND", "local")
    assert isinstance(get_code_executor(), LocalSubprocessExecutor)


def test_selector_docker_present(monkeypatch):
    import agent_runtime.code_execution as ce
    monkeypatch.setenv("AGENT_CODE_EXEC_BACKEND", "docker")
    monkeypatch.setattr(ce, "_docker_available", lambda: True)
    assert isinstance(ce.get_code_executor(), DockerCodeExecutor)


def test_selector_docker_missing_is_disabled(monkeypatch):
    import agent_runtime.code_execution as ce
    monkeypatch.setenv("AGENT_CODE_EXEC_BACKEND", "docker")
    monkeypatch.setattr(ce, "_docker_available", lambda: False)
    ex = ce.get_code_executor()
    assert isinstance(ex, DisabledExecutor)
    # never runs code — returns an error result
    assert ex.execute("print(1)").error


# --- the execute_code tool -------------------------------------------------

def test_execute_code_tool_with_stub_executor():
    from agent_runtime.langchain_exec_tools import make_code_execution_tools

    captured = {}

    class _Stub:
        def execute(self, code, language="python", timeout=None, dependencies=None):
            captured["dependencies"] = dependencies
            return ExecResult(exit_code=0, stdout="captured-out", stderr="", backend="stub")

    tools = make_code_execution_tools(executor=_Stub())
    assert tools[0].name == "execute_code"
    out = json.loads(tools[0].invoke({"code": "import numpy", "dependencies": ["numpy"]}))
    assert out["ok"] is True
    assert out["stdout"] == "captured-out"
    assert out["exit_code"] == 0
    assert captured["dependencies"] == ["numpy"]  # dependencies threaded to the executor


# --- per-request code_exec flag wires the tool into the code peer ----------

def test_code_exec_flag_controls_tool_wiring(monkeypatch):
    import agent_runtime.executor_factory as ef
    import agent_runtime.supervisor_graph as sg

    captured = {}

    def fake_build(**kwargs):
        captured["tools"] = [getattr(t, "name", "") for t in (kwargs.get("preloaded_tools") or [])]
        return object()

    monkeypatch.setattr(ef, "build_agent_executor", fake_build)
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})

    # Per-request ON -> execute_code wired even though env is unset.
    monkeypatch.delenv("AGENT_CODE_EXEC", raising=False)
    sg.default_code_fn(code_exec=True)("write code", [], {"thread_id": None})
    assert "execute_code" in captured["tools"]

    # Per-request OFF -> not wired.
    sg.default_code_fn(code_exec=False)("write code", [], {"thread_id": None})
    assert "execute_code" not in captured["tools"]
