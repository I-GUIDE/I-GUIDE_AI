"""Persistent per-session sandbox workspace, tiers, and incremental artifact persistence.

The hard ceiling these tests exist for: `execute()` used to `mkdtemp()` and then
`shutil.rmtree(work)` in a `finally`, so the workspace never outlived a single call. Step 2
could not read step 1's output, which made multi-step workflows impossible to express
regardless of how capable the model was.

Two properties are inseparable and are therefore tested together:

* the workspace survives between calls when a session_id is given, and
* artifacts persist INCREMENTALLY.

Without the second, the first is actively harmful: `_persist_artifacts` walks the tree in
sorted-path order and stops at MAX_ARTIFACTS (20), so step 1's leftovers would consume the
budget and step 5's real output would silently never be persisted.

These run on the LocalSubprocessExecutor (dev-only, not a sandbox) because the Docker
backend needs a daemon. The workspace logic under test lives in the shared base class.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime import code_execution as ce


@pytest.fixture()
def local_exec(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CODE_EXEC_WORK_ROOT", str(tmp_path / "workroot"))
    (tmp_path / "workroot").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path / "store"))
    monkeypatch.delenv("AGENT_CODE_EXEC_ALLOW_HEAVY", raising=False)
    monkeypatch.delenv("AGENT_CODE_EXEC_DEFAULT_TIER", raising=False)
    monkeypatch.setattr(ce, "DEFAULT_TIER", "standard")
    return ce.LocalSubprocessExecutor()


# --------------------------------------------------------------- the ceiling itself

def test_state_survives_across_calls_with_a_session(local_exec):
    """THE regression: three sequential calls where call k reads call k-1's file."""
    sid = "session-abc"
    r1 = local_exec.execute("open('step1.txt','w').write('one')", session_id=sid)
    assert r1.ok, r1.stderr

    r2 = local_exec.execute(
        "print(open('step1.txt').read()); open('step2.txt','w').write('two')", session_id=sid)
    assert r2.ok, r2.stderr
    assert "one" in r2.stdout, "step 2 could not see step 1's file"

    r3 = local_exec.execute(
        "print(open('step1.txt').read() + open('step2.txt').read())", session_id=sid)
    assert r3.ok, r3.stderr
    assert "onetwo" in r3.stdout, "step 3 could not see steps 1 and 2"


def test_sessionless_runs_stay_ephemeral(local_exec):
    """Old behaviour preserved: without a session, unrelated turns must not share state."""
    local_exec.execute("open('leak.txt','w').write('x')")
    r = local_exec.execute("import os; print('leak.txt exists:', os.path.exists('leak.txt'))")
    assert "leak.txt exists: False" in r.stdout


def test_sessions_are_isolated_from_each_other(local_exec):
    local_exec.execute("open('mine.txt','w').write('a')", session_id="alice")
    r = local_exec.execute("import os; print('mine:', os.path.exists('mine.txt'))", session_id="bob")
    assert "mine: False" in r.stdout


# --------------------------------------------------------------- path safety

@pytest.mark.parametrize("raw", ["../../etc/passwd", "a/b/c", "..\\..\\x", "  ../  "])
def test_session_id_never_yields_a_separator(raw):
    """A session id arrives from the request; it must stay ONE path segment."""
    safe = ce._safe_session_id(raw)
    assert "/" not in safe and "\\" not in safe


@pytest.mark.parametrize("raw", ["..", ".", "...", "  ..  ", "./", "../"])
def test_path_special_session_ids_are_rejected(raw):
    """`..` survived the allowlist regex (dots are permitted), so <root>/.. WAS the work
    root — and sweep_workspaces would eventually rmtree it. Found by this test, not by
    review; the same flaw still exists in qgis_headless_tools._safe_session_id."""
    safe = ce._safe_session_id(raw)
    assert set(safe) - {"."}, f"{raw!r} sanitised to the path-special {safe!r}"
    assert safe not in {".", "..", "..."}


def test_session_dir_stays_under_the_sessions_root(local_exec, tmp_path):
    d = ce.session_work_dir("../../escape")
    root = (tmp_path / "workroot" / ce.SESSIONS_DIRNAME).resolve()
    assert str(d.resolve()).startswith(str(root)), f"{d} escaped {root}"


def test_empty_session_id_is_treated_as_sessionless(local_exec):
    """A blank id must not silently become the shared 'default' workspace."""
    local_exec.execute("open('blank.txt','w').write('x')", session_id="   ")
    r = local_exec.execute("import os; print('blank:', os.path.exists('blank.txt'))", session_id="   ")
    assert "blank: False" in r.stdout


# --------------------------------------------------------------- incremental artifacts

def test_only_new_or_changed_files_are_persisted(local_exec):
    sid = "artifacts-1"
    r1 = local_exec.execute("open('a.txt','w').write('1')", session_id=sid)
    names1 = {a["filename"] for a in r1.artifacts}
    assert "a.txt" in names1

    # a.txt untouched, b.txt new -> only b.txt should be persisted
    r2 = local_exec.execute("open('b.txt','w').write('2')", session_id=sid)
    names2 = {a["filename"] for a in r2.artifacts if a.get("kind") != "source"}
    assert "b.txt" in names2
    assert "a.txt" not in names2, "an unchanged file was re-persisted"


def test_a_changed_file_is_persisted_again(local_exec):
    sid = "artifacts-2"
    local_exec.execute("open('c.txt','w').write('first')", session_id=sid)
    r2 = local_exec.execute("open('c.txt','w').write('second-and-longer')", session_id=sid)
    names = {a["filename"] for a in r2.artifacts if a.get("kind") != "source"}
    assert "c.txt" in names, "a modified file was not re-persisted"


def test_late_output_is_not_crowded_out_by_early_leftovers(local_exec):
    """The MAX_ARTIFACTS interaction that makes a persistent workspace unusable without this.

    Fill the workspace with more than MAX_ARTIFACTS files, then produce ONE new file whose
    name sorts last. Without incremental persistence the walk would spend its budget on the
    leftovers and never reach it.
    """
    sid = "artifacts-3"
    local_exec.execute(
        f"[open(f'early_{{i:03d}}.txt','w').write('x') for i in range({ce.MAX_ARTIFACTS + 5})]",
        session_id=sid)
    r = local_exec.execute("open('zzz_final_output.txt','w').write('the real answer')", session_id=sid)
    names = {a["filename"] for a in r.artifacts if a.get("kind") != "source"}
    assert "zzz_final_output.txt" in names, (
        "the run's actual output was crowded out by earlier files")


def test_the_index_file_is_never_itself_an_artifact(local_exec):
    r = local_exec.execute("open('x.txt','w').write('1')", session_id="artifacts-4")
    assert all(a["filename"] != ce.ARTIFACT_INDEX_FILENAME for a in r.artifacts)


# --------------------------------------------------------------- tiers

def test_default_tier_is_not_the_quick_budget():
    """512m/1cpu/60s is a quick-tool budget; real analysis defaults to standard."""
    name, limits = ce.resolve_tier(None)
    assert name == "standard"
    assert limits["timeout"] == "300" and limits["memory"] == "2g"


def test_named_tiers_resolve():
    assert ce.resolve_tier("quick")[1]["memory"] == "512m"
    assert ce.resolve_tier("standard")[1]["memory"] == "2g"


def test_heavy_is_gated(monkeypatch):
    monkeypatch.delenv("AGENT_CODE_EXEC_ALLOW_HEAVY", raising=False)
    assert ce.resolve_tier("heavy")[0] == "standard", "heavy must not be available by default"
    monkeypatch.setenv("AGENT_CODE_EXEC_ALLOW_HEAVY", "1")
    name, limits = ce.resolve_tier("heavy")
    assert name == "heavy" and limits["memory"] == "6g"


def test_unknown_tier_falls_back_rather_than_raising():
    assert ce.resolve_tier("enormous")[0] == "standard"


def test_explicit_timeout_overrides_the_tier(local_exec):
    r = local_exec.execute("import time; time.sleep(2)", session_id="tier-1", timeout=1)
    assert r.timed_out, "an explicit timeout must win over the tier's"


def test_docker_argv_honours_tier_limits():
    ex = ce.DockerCodeExecutor()
    argv = ex.build_argv(Path("/tmp/w"), "n", limits={"memory": "6g", "cpus": "4.0"})
    assert argv[argv.index("--memory") + 1] == "6g"
    assert argv[argv.index("--cpus") + 1] == "4.0"
    # the hardening flags must survive a tier override
    for flag in ("--network", "--read-only", "--cap-drop", "--security-opt"):
        assert flag in argv


def test_docker_argv_falls_back_to_constructor_limits():
    ex = ce.DockerCodeExecutor(memory="333m", cpus="1.5")
    argv = ex.build_argv(Path("/tmp/w"), "n")
    assert argv[argv.index("--memory") + 1] == "333m"


# --------------------------------------------------------------- deps reuse + reclamation

def test_deps_marker_round_trip(local_exec):
    sid = "deps-1"
    work = ce.session_work_dir(sid)
    ce._record_deps(work, ["somepkg"])
    assert ce._deps_satisfied(work, ["somepkg"]) is True
    assert ce._deps_satisfied(work, ["somepkg", "other"]) is False, "a new dep must trigger install"


def test_a_session_installs_a_dependency_only_once(local_exec, monkeypatch, tmp_path):
    """The latency win of the persistent workspace: install geopandas once per session.

    Counted by spying on the install subprocess rather than by timing a real pip run — the
    host's anaconda pip cannot `--target` install at all here (it raises PermissionError
    scanning an unreadable sys.path entry), which is a pre-existing environment fault and
    would make a timing-based assertion measure the wrong thing.
    """
    installs = []
    real_run = ce.subprocess.run

    def counting_run(argv, **kwargs):
        if isinstance(argv, list) and "install" in argv:
            installs.append(list(argv))
            # Pretend the install succeeded and materialise the target dir.
            target = argv[argv.index("--target") + 1] if "--target" in argv else None
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)

            class _OK:
                returncode, stdout, stderr = 0, "", ""
            return _OK()
        return real_run(argv, **kwargs)

    monkeypatch.setattr(ce.subprocess, "run", counting_run)

    sid = "deps-once"
    for _ in range(3):
        local_exec.execute("print('hi')", session_id=sid, dependencies=["six"])
    assert len(installs) == 1, f"expected 1 install across 3 calls, got {len(installs)}"

    # And without a session every call must reinstall (no workspace to reuse).
    installs.clear()
    for _ in range(3):
        local_exec.execute("print('hi')", dependencies=["six"])
    assert len(installs) == 3, f"sessionless runs should each install, got {len(installs)}"


def test_a_new_dependency_still_triggers_an_install(local_exec, monkeypatch):
    installs = []
    real_run = ce.subprocess.run

    def counting_run(argv, **kwargs):
        if isinstance(argv, list) and "install" in argv:
            installs.append(list(argv))
            target = argv[argv.index("--target") + 1] if "--target" in argv else None
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)

            class _OK:
                returncode, stdout, stderr = 0, "", ""
            return _OK()
        return real_run(argv, **kwargs)

    monkeypatch.setattr(ce.subprocess, "run", counting_run)
    sid = "deps-grow"
    local_exec.execute("print(1)", session_id=sid, dependencies=["six"])
    local_exec.execute("print(2)", session_id=sid, dependencies=["six"])          # cached
    local_exec.execute("print(3)", session_id=sid, dependencies=["six", "attrs"])  # new dep
    assert len(installs) == 2, f"expected 2 installs (initial + new dep), got {len(installs)}"


def test_deps_marker_is_absent_for_a_fresh_workspace(local_exec):
    work = ce.session_work_dir("deps-2")
    assert ce._deps_satisfied(work, ["anything"]) is False


def test_sweep_removes_only_expired_workspaces(local_exec):
    fresh = ce.session_work_dir("fresh")
    stale = ce.session_work_dir("stale")
    import os as _os
    old = 1_000_000
    _os.utime(stale, (old, old))
    summary = ce.sweep_workspaces(ttl_hours=1)
    assert summary["removed"] >= 1
    assert fresh.exists(), "a fresh workspace was reclaimed"
    assert not stale.exists(), "an expired workspace survived"


def test_sweep_is_disabled_when_ttl_is_zero(local_exec):
    ce.session_work_dir("keepme")
    assert ce.sweep_workspaces(ttl_hours=0)["removed"] == 0
