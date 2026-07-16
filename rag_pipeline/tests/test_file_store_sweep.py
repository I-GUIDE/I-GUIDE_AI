"""Tests for the opt-in TTL sweep of the agent file store.

Each test points AGENT_FILE_STORAGE_ROOT at a tmp dir and ages files via os.utime.
No network, no real uploads.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from agent_runtime import file_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path))
    file_store._LAST_SWEEP["t"] = 0.0   # reset the throttle so maybe_sweep is deterministic
    return tmp_path


def _make(root, kind, file_id, name, *, age_days):
    sub = "uploads" if kind == "upload" else "outputs"
    d = root / sub
    d.mkdir(parents=True, exist_ok=True)
    data = d / f"{file_id}__{name}"
    data.write_text("x" * 10)
    meta_dir = root / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    mp = meta_dir / f"{file_id}.json"
    mp.write_text(json.dumps({"file_id": file_id, "filename": name, "kind": kind,
                              "relative_path": f"{sub}/{file_id}__{name}"}))
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(data, (old, old))
        os.utime(mp, (old, old))
    return data, mp


def test_sweep_disabled_by_default(store, monkeypatch):
    monkeypatch.delenv("AGENT_FILE_RETENTION_DAYS", raising=False)
    data, mp = _make(store, "upload", "file_a", "a.csv", age_days=999)
    res = file_store.sweep_expired_files()
    assert res["deleted"] == 0
    assert data.exists() and mp.exists()         # retention off -> nothing deleted


def test_sweep_deletes_old_keeps_fresh(store, monkeypatch):
    monkeypatch.setenv("AGENT_FILE_RETENTION_DAYS", "30")
    old_d, old_m = _make(store, "upload", "file_old", "old.csv", age_days=40)
    fresh_d, fresh_m = _make(store, "output", "file_new", "new.png", age_days=0)
    res = file_store.sweep_expired_files()
    assert not old_d.exists() and not old_m.exists()   # expired data + sidecar removed
    assert fresh_d.exists() and fresh_m.exists()        # fresh kept
    assert res["deleted"] == 2 and res["freed_bytes"] >= 10


def test_sweep_removes_orphan_metadata_but_not_present_data(store, monkeypatch):
    monkeypatch.setenv("AGENT_FILE_RETENTION_DAYS", "30")
    meta_dir = store / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    orphan = meta_dir / "file_orphan.json"          # metadata with no data file
    orphan.write_text("{}")
    old = time.time() - 40 * 86400
    os.utime(orphan, (old, old))
    data, mp = _make(store, "upload", "file_keep", "keep.csv", age_days=0)
    os.utime(mp, (old, old))                         # make ONLY the metadata old
    file_store.sweep_expired_files()
    assert not orphan.exists()                       # expired orphan metadata removed
    assert data.exists() and mp.exists()             # present data is never orphaned


def test_max_age_arg_enables_even_when_env_unset(store, monkeypatch):
    monkeypatch.delenv("AGENT_FILE_RETENTION_DAYS", raising=False)
    data, mp = _make(store, "upload", "file_x", "x.csv", age_days=10)
    res = file_store.sweep_expired_files(max_age_days=7)   # explicit arg overrides disabled env
    assert not data.exists() and not mp.exists()
    assert res["deleted"] == 2


def test_sweep_never_raises_on_empty_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path / "fresh"))
    monkeypatch.setenv("AGENT_FILE_RETENTION_DAYS", "1")
    res = file_store.sweep_expired_files()           # no uploads/outputs subdirs -> no-op
    assert res == {"scanned": 0, "deleted": 0, "freed_bytes": 0, "errors": 0}


def test_maybe_sweep_is_throttled_and_gated(store, monkeypatch):
    monkeypatch.setenv("AGENT_FILE_RETENTION_DAYS", "30")
    monkeypatch.setenv("AGENT_FILE_SWEEP_INTERVAL_SECONDS", "3600")
    old_d, _ = _make(store, "upload", "file_old", "old.csv", age_days=40)
    file_store._LAST_SWEEP["t"] = 0.0
    file_store.maybe_sweep_expired_files()           # first call sweeps
    assert not old_d.exists()
    old2_d, _ = _make(store, "upload", "file_old2", "old2.csv", age_days=40)
    file_store.maybe_sweep_expired_files()           # within interval -> throttled, no sweep
    assert old2_d.exists()


def test_maybe_sweep_noop_when_retention_disabled(store, monkeypatch):
    monkeypatch.delenv("AGENT_FILE_RETENTION_DAYS", raising=False)
    old_d, _ = _make(store, "upload", "file_old", "old.csv", age_days=999)
    file_store._LAST_SWEEP["t"] = 0.0
    file_store.maybe_sweep_expired_files()
    assert old_d.exists()                            # disabled -> never sweeps


# --- AGENT_PUBLIC_BASE_URL: absolute download URLs from the server side --------

def test_download_url_relative_by_default(store, monkeypatch):
    monkeypatch.delenv("AGENT_PUBLIC_BASE_URL", raising=False)
    rec = file_store.create_output_file("a.txt", "x")
    assert rec["download_url"] == f"/agent/files/{rec['file_id']}/download"


def test_public_base_url_absolutizes_emission_not_persistence(store, monkeypatch):
    monkeypatch.setenv("AGENT_PUBLIC_BASE_URL", "http://149.165.147.219:3500/")   # trailing slash ok
    rec = file_store.create_output_file("b.txt", "x")
    fid = rec["file_id"]
    assert rec["download_url"] == f"http://149.165.147.219:3500/agent/files/{fid}/download"
    # read boundary absolutizes too
    got = file_store.get_file_record(fid)
    assert got["download_url"].startswith("http://149.165.147.219:3500/agent/files/")
    # persisted metadata stays HOST-RELATIVE (origin-agnostic if the base changes later)
    raw = json.loads((store / "metadata" / f"{fid}.json").read_text())
    assert raw["download_url"] == f"/agent/files/{fid}/download"
    # invalid scheme -> ignored, falls back to relative
    monkeypatch.setenv("AGENT_PUBLIC_BASE_URL", "not-a-url")
    assert file_store.get_file_record(fid)["download_url"] == f"/agent/files/{fid}/download"
