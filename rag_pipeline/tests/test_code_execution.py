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


def test_install_phase_gets_larger_memory_than_exec():
    """P1-5: deps-install gets its own (larger) memory budget to avoid OOM (exit 137)."""
    ex = DockerCodeExecutor(image="img:test", memory="256m", install_memory="2g")
    install = " ".join(ex.build_install_argv(Path("/tmp/work"), ["numpy"], "n"))
    execute = " ".join(ex.build_argv(Path("/tmp/work"), "n"))
    assert "--memory 2g" in install     # install uses the larger budget
    assert "--memory 256m" in execute   # exec keeps the tighter limit
    assert "--memory 2g" not in execute


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
        def execute(self, code, language="python", timeout=None, dependencies=None, input_files=None):
            captured["dependencies"] = dependencies
            captured["input_files"] = input_files
            return ExecResult(exit_code=0, stdout="captured-out", stderr="", backend="stub")

    tools = make_code_execution_tools(executor=_Stub())
    assert tools[0].name == "execute_code"
    out = json.loads(tools[0].invoke({"code": "import numpy", "dependencies": ["numpy"]}))
    assert out["ok"] is True
    assert out["stdout"] == "captured-out"
    assert out["exit_code"] == 0
    assert captured["dependencies"] == ["numpy"]  # dependencies threaded to the executor
    assert captured["input_files"] == []  # no uploads requested -> empty staging list


# --- uploaded-file staging into the sandbox --------------------------------

def test_stage_inputs_copies_and_rejects_traversal(tmp_path):
    from agent_runtime.code_execution import _stage_inputs

    src = tmp_path / "src.txt"
    src.write_text("payload")
    work = tmp_path / "work"
    work.mkdir()

    staged, errors = _stage_inputs(work, [
        {"source": str(src), "dest": "ok.txt"},
        {"source": str(src), "dest": "../escape.txt"},          # traversal
        {"source": str(src), "dest": "sub/nested.txt"},         # separator
        {"source": str(tmp_path / "missing"), "dest": "m.txt"},  # missing source
    ])
    assert staged == ["ok.txt"]
    assert (work / "ok.txt").read_text() == "payload"
    assert not (work / "escape.txt").exists()
    reasons = {e.get("error") for e in errors}
    assert "invalid destination name" in reasons
    assert "source file not found" in reasons


def test_execute_code_tool_resolves_and_stages_file_specs(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    from agent_runtime.file_store import create_output_file
    from agent_runtime.langchain_exec_tools import make_code_execution_tools

    rec = create_output_file("data.csv", "a,b\n1,2\n")
    fid = rec["file_id"]

    captured = {}

    class _Stub:
        def execute(self, code, language="python", timeout=None, dependencies=None, input_files=None):
            captured["input_files"] = input_files
            return ExecResult(exit_code=0, stdout="", stderr="", backend="stub")

    tools = make_code_execution_tools(executor=_Stub())
    out = json.loads(tools[0].invoke({"code": "print(1)", "input_files": [fid]}))

    # staged under BOTH the file_id and the original filename
    dests = {s["dest"] for s in captured["input_files"]}
    assert fid in dests and "data.csv" in dests
    assert all(s["source"].endswith("data.csv") for s in captured["input_files"])
    info = out["input_files"][0]
    assert info["file_id"] == fid and "data.csv" in info["available_as"] and fid in info["available_as"]


def test_execute_code_reads_uploaded_file_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    from agent_runtime.file_store import create_output_file
    from agent_runtime.langchain_exec_tools import make_code_execution_tools

    rec = create_output_file("data.csv", "a,b\n1,2\n3,4\n")
    fid = rec["file_id"]
    tools = make_code_execution_tools(executor=LocalSubprocessExecutor())

    # read by original filename
    out = json.loads(tools[0].invoke(
        {"code": "print(open('data.csv').read())", "input_files": [fid]}))
    assert out["ok"] is True and "a,b" in out["stdout"]

    # read by file_id (the name the model used in the failing trace)
    out2 = json.loads(tools[0].invoke(
        {"code": f"print(open('{fid}').read())", "input_files": [fid]}))
    assert out2["ok"] is True and "a,b" in out2["stdout"]


def test_staged_input_not_re_persisted_but_new_outputs_are(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    from agent_runtime.file_store import create_output_file
    from agent_runtime.langchain_exec_tools import make_code_execution_tools

    rec = create_output_file("in.csv", "a\n1\n")
    fid = rec["file_id"]
    tools = make_code_execution_tools(executor=LocalSubprocessExecutor())

    out = json.loads(tools[0].invoke(
        {"code": "open('out.txt','w').write(open('in.csv').read())", "input_files": [fid]}))
    arts = [a["filename"] for a in out["artifacts"]]
    assert "out.txt" in arts          # genuine output is persisted
    assert "executed_code.py" in arts  # source is persisted
    assert "in.csv" not in arts        # staged input is NOT re-persisted as an output


def test_execute_code_unknown_input_file_reports_error(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    from agent_runtime.langchain_exec_tools import make_code_execution_tools

    tools = make_code_execution_tools(executor=LocalSubprocessExecutor())
    out = json.loads(tools[0].invoke(
        {"code": "print('ok')", "input_files": ["file_does_not_exist"]}))
    assert out["input_file_errors"][0]["ref"] == "file_does_not_exist"
    assert out["ok"] is True  # the run itself still succeeds


# --- auto-staging conversation files (default_input_file_ids) --------------

def test_default_input_file_ids_are_auto_staged(monkeypatch, tmp_path):
    """Conversation-attached files are staged without the model naming them."""
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    from agent_runtime.file_store import create_output_file
    from agent_runtime.langchain_exec_tools import make_code_execution_tools

    rec = create_output_file("conv.csv", "a\n1\n")
    fid = rec["file_id"]
    tools = make_code_execution_tools(
        executor=LocalSubprocessExecutor(), default_input_file_ids=[fid])

    # model did NOT pass input_files — file is available anyway
    out = json.loads(tools[0].invoke({"code": "print(open('conv.csv').read())"}))
    assert out["ok"] is True and "a" in out["stdout"]
    assert out["input_files"][0]["file_id"] == fid


def test_default_and_explicit_union_deduped(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    from agent_runtime.file_store import create_output_file
    from agent_runtime.langchain_exec_tools import make_code_execution_tools

    a = create_output_file("a.csv", "x\n")["file_id"]
    b = create_output_file("b.csv", "y\n")["file_id"]

    captured = {}

    class _Stub:
        def execute(self, code, language="python", timeout=None, dependencies=None, input_files=None):
            captured["input_files"] = input_files
            return ExecResult(exit_code=0, stdout="", stderr="", backend="stub")

    tools = make_code_execution_tools(executor=_Stub(), default_input_file_ids=[a])
    out = json.loads(tools[0].invoke({"code": "print(1)", "input_files": [a, b]}))  # 'a' in both
    # each unique file staged once (deduped by source), under id + filename
    info_ids = [i["file_id"] for i in out["input_files"]]
    assert info_ids == [a, b]  # 'a' not duplicated
    dests = {s["dest"] for s in captured["input_files"]}
    assert {a, "a.csv", b, "b.csv"} <= dests


def test_input_file_count_cap_skips_extras(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_CODE_EXEC_MAX_INPUT_FILES", "1")
    from agent_runtime.file_store import create_output_file
    from agent_runtime.langchain_exec_tools import make_code_execution_tools

    a = create_output_file("a.csv", "x\n")["file_id"]
    b = create_output_file("b.csv", "y\n")["file_id"]

    class _Stub:
        def execute(self, code, language="python", timeout=None, dependencies=None, input_files=None):
            return ExecResult(exit_code=0, stdout="", stderr="", backend="stub")

    tools = make_code_execution_tools(executor=_Stub(), default_input_file_ids=[a, b])
    out = json.loads(tools[0].invoke({"code": "print(1)"}))
    assert len(out["input_files"]) == 1
    assert out["input_files_skipped"][0]["reason"] == "max input files exceeded"


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
