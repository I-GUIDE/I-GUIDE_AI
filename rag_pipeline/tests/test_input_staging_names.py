"""Two inputs must never claim one name in the sandbox work dir.

`_build_staging` stages each file under its file_id AND its filename, and deduped only by
SOURCE. Two different files sharing a filename therefore emitted the same dest: the second copy
overwrote the first, `available_as` went on advertising both, and the tool description steers
the model straight at the colliding name — so the peer analysed the wrong dataset under the
right name. When neither file had a file_id it was worse: one file in /work and the other
unreachable under any name.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import agent_runtime.langchain_exec_tools as lex


def _resolver(monkeypatch, mapping):
    """Stub resolution so these test the NAMING, not the allowed-root policy."""
    def _resolve(ref):
        return mapping[str(ref)]
    monkeypatch.setattr(lex, "_resolve_input_file", _resolve)


def test_two_files_sharing_a_filename_do_not_collide(monkeypatch):
    _resolver(monkeypatch, {
        "a": (Path("/store/file_aaa__data.csv"), {"file_id": "file_aaa", "filename": "data.csv", "size_bytes": 10}),
        "b": (Path("/store/file_bbb__data.csv"), {"file_id": "file_bbb", "filename": "data.csv", "size_bytes": 10}),
    })
    staging, staged, errors, _ = lex._build_staging(["a", "b"])
    assert not errors
    dests = [s["dest"] for s in staging]
    assert len(dests) == len(set(dests)), f"a name is claimed twice: {dests}"


def test_available_as_reports_only_the_names_the_file_really_has(monkeypatch):
    """The model is told to open these, so an alias it does not own is a lie that sends it to
    another dataset."""
    _resolver(monkeypatch, {
        "a": (Path("/store/file_aaa__data.csv"), {"file_id": "file_aaa", "filename": "data.csv", "size_bytes": 10}),
        "b": (Path("/store/file_bbb__data.csv"), {"file_id": "file_bbb", "filename": "data.csv", "size_bytes": 10}),
    })
    staging, staged, _errors, _ = lex._build_staging(["a", "b"])
    dests = {s["dest"] for s in staging}
    for entry in staged:
        for name in entry["available_as"]:
            assert name in dests, f"{name} advertised but never staged"
    # the first claimant keeps the plain name; the second is honest about not having it
    assert "data.csv" in staged[0]["available_as"]
    assert "data.csv" not in staged[1]["available_as"]


def test_the_first_claimant_keeps_the_plain_name(monkeypatch):
    """Stability matters: the common single-file case must be unchanged."""
    _resolver(monkeypatch, {
        "a": (Path("/store/file_aaa__data.csv"), {"file_id": "file_aaa", "filename": "data.csv", "size_bytes": 10}),
    })
    _staging, staged, _errors, _ = lex._build_staging(["a"])
    assert staged[0]["available_as"] == ["file_aaa", "data.csv"]


def test_a_file_with_no_id_still_gets_a_reachable_name(monkeypatch):
    """Two local paths sharing a basename: neither has a file_id, so the loser used to end up
    with no name at all and was unreachable."""
    _resolver(monkeypatch, {
        "acs/data.csv": (Path("/local/acs/data.csv"), None),
        "tiger/data.csv": (Path("/local/tiger/data.csv"), None),
    })
    staging, staged, errors, _ = lex._build_staging(["acs/data.csv", "tiger/data.csv"])
    assert not errors
    dests = [s["dest"] for s in staging]
    assert len(dests) == 2 and len(set(dests)) == 2, dests
    assert all(e["available_as"] for e in staged), "every input must be openable under some name"
    assert staged[0]["available_as"] == ["data.csv"]
    assert staged[1]["available_as"] == ["data_2.csv"]


def test_the_same_source_twice_is_still_staged_once(monkeypatch):
    """The pre-existing source dedupe must survive: an id and its filename are one file."""
    _resolver(monkeypatch, {
        "file_aaa": (Path("/store/file_aaa__data.csv"), {"file_id": "file_aaa", "filename": "data.csv", "size_bytes": 10}),
        "data.csv": (Path("/store/file_aaa__data.csv"), {"file_id": "file_aaa", "filename": "data.csv", "size_bytes": 10}),
    })
    _staging, staged, _errors, _ = lex._build_staging(["file_aaa", "data.csv"])
    assert len(staged) == 1


def test_free_dest_keeps_the_extension():
    claimed = {"data.csv": "a"}
    assert lex._free_dest("data.csv", claimed) == "data_2.csv"
    assert lex._free_dest("data.csv", {"data.csv": "a", "data_2.csv": "b"}) == "data_3.csv"
    assert lex._free_dest("noextension", {"noextension": "a"}) == "noextension_2"
    assert lex._free_dest("archive.tar.gz", {"archive.tar.gz": "a"}) == "archive.tar_2.gz"
