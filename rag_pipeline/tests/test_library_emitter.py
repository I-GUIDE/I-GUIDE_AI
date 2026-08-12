"""The method library: extracted units written as an importable Python package.

The agent composes extracted methods in Python rather than by chaining tool calls, so the
units have to land as real importable modules, not as more tool schemas. One tool per unit
would put every ingested function into every prompt — a 24-tool peer already costs ~3,900
tokens of schema.

Two properties get the most attention here:

* ``get(symbol)`` really imports and returns a callable (a registry that only *describes*
  units is not a library), and
* symbol collisions are admitted rather than silently resolved. Measured on the real corpus,
  keying the registry by bare name turned 40 modules into 37 entries, because three function
  names are each defined by two different notebooks and the later ingest overwrote the
  earlier. A resolver returning "whichever element was ingested last" is worse than one that
  refuses.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from extractors.base import (EMIT_LIBRARY, EMIT_OPENSEARCH, KIND_METHOD_UNIT,
                             AssetRecord)
from extractors.emitters import library_emitter
from extractors.manifest import UnifiedManifest


def _unit_asset(symbol, element_id, source, *, sha, title="Demo", targets=(EMIT_OPENSEARCH, EMIT_LIBRARY)):
    return AssetRecord(
        asset_id=f"{element_id}::unit::{symbol}",
        kind=KIND_METHOD_UNIT,
        resource_type="MethodUnit",
        doc_id=f"{element_id}::unit::{symbol}",
        emit_targets=list(targets),
        title=f"{symbol} — {title}",
        contents=f"def {symbol}(...)",
        unit={"qualified_name": symbol, "library_symbol": symbol, "slice_sha": sha,
              "signature": f"def {symbol}(x)", "doc_summary": f"{symbol} summary",
              "params": [], "returns": "", "invariants": [],
              "requirements": {"pip": ["six"]},
              "provenance": {"element_id": element_id, "commit_sha": "abc"}},
        slice_source=source,
    )


def _manifest(*assets):
    m = UnifiedManifest(repo_id="r", source_url="u", cloned_at="now")
    from extractors.base import ExtractionResult
    m.add_result("notebook", ExtractionResult(assets=list(assets)))
    return m


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_FILE_STORAGE_ROOT", str(tmp_path / "store"))
    return tmp_path / "root"


def _import_in_subprocess(pkg_parent: Path, code: str):
    return subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(pkg_parent)!r})\n{code}"],
        capture_output=True, text=True, timeout=120)


# --------------------------------------------------------------- it is a real package

def test_a_unit_becomes_an_importable_callable(lib):
    src = "def double(x):\n    return x * 2\n"
    out = library_emitter.emit(_manifest(_unit_asset("double", "elem1", src, sha="aaa111")),
                               root=lib)
    assert out["units"] == 1 and len(out["written"]) == 1

    r = _import_in_subprocess(lib, "import iguide_methods as M; print(M.get('double')(21))")
    assert r.returncode == 0, r.stderr[-500:]
    assert r.stdout.strip() == "42", f"get() did not return a working callable: {r.stdout!r}"


def test_describe_returns_the_contract(lib):
    src = "def f(x):\n    return x\n"
    library_emitter.emit(_manifest(_unit_asset("f", "elem1", src, sha="bbb222")), root=lib)
    r = _import_in_subprocess(lib, (
        "import iguide_methods as M, json;"
        "d = M.describe('f');"
        "print(json.dumps({'sig': d['signature'], 'sha': d['slice_sha'],"
        " 'elem': d['provenance']['element_id']}))"))
    assert r.returncode == 0, r.stderr[-400:]
    d = json.loads(r.stdout)
    assert d["sig"] == "def f(x)"
    assert d["sha"] == "bbb222"
    assert d["elem"] == "elem1"


def test_the_package_init_has_no_import_side_effects(lib):
    library_emitter.emit(_manifest(_unit_asset("f", "e", "def f():\n    return 1\n", sha="c1")),
                         root=lib)
    r = _import_in_subprocess(lib, "import iguide_methods; print('clean')")
    assert r.returncode == 0 and "clean" in r.stdout


# --------------------------------------------------------------- collisions

def test_colliding_bare_names_are_ambiguous_not_overwritten(lib):
    """The measured bug: 40 modules produced 37 registry entries."""
    a = _unit_asset("load", "elemA", "def load():\n    return 'A'\n", sha="a1")
    b = _unit_asset("load", "elemB", "def load():\n    return 'B'\n", sha="b1")
    out = library_emitter.emit(_manifest(a, b), root=lib)
    assert len(out["written"]) == 2, "a unit was dropped"

    r = _import_in_subprocess(lib, (
        "import iguide_methods as M;"
        "d = M.describe('load');"
        "print('AMBIGUOUS' if d.get('ambiguous') else 'RESOLVED', len(d.get('candidates') or []))"))
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.split()[0] == "AMBIGUOUS"
    assert r.stdout.split()[1] == "2"


def test_an_ambiguous_bare_name_raises_rather_than_guessing(lib):
    a = _unit_asset("load", "elemA", "def load():\n    return 'A'\n", sha="a1")
    b = _unit_asset("load", "elemB", "def load():\n    return 'B'\n", sha="b1")
    library_emitter.emit(_manifest(a, b), root=lib)
    r = _import_in_subprocess(lib, (
        "import iguide_methods as M\n"
        "try:\n    M.get('load'); print('RESOLVED')\n"
        "except KeyError as e:\n    print('RAISED')"))
    assert r.stdout.strip() == "RAISED", "an ambiguous name silently resolved"


def test_qualified_names_stay_reachable_when_ambiguous(lib):
    a = _unit_asset("load", "elemA", "def load():\n    return 'A'\n", sha="a1", title="Alpha")
    b = _unit_asset("load", "elemB", "def load():\n    return 'B'\n", sha="b1", title="Beta")
    library_emitter.emit(_manifest(a, b), root=lib)
    r = _import_in_subprocess(lib, (
        "import iguide_methods as M;"
        "qs = [s for s in M.symbols() if s.endswith('.load')];"
        "print(len(qs), sorted(M.get(q)() for q in qs))"))
    assert r.returncode == 0, r.stderr[-400:]
    n, vals = r.stdout.split(maxsplit=1)
    assert n == "2"
    assert "'A'" in vals and "'B'" in vals, "both colliding units must remain callable"


# --------------------------------------------------------------- versioning + targets

def test_reingesting_unchanged_source_is_a_no_op(lib):
    a = _unit_asset("f", "e", "def f():\n    return 1\n", sha="same")
    library_emitter.emit(_manifest(a), root=lib)
    mod = next((lib / "iguide_methods").rglob("v_same.py"))
    before = (mod.read_text(), mod.stat().st_mtime_ns)
    library_emitter.emit(_manifest(a), root=lib)
    assert mod.read_text() == before[0]
    assert mod.stat().st_mtime_ns == before[1], "an unchanged slice was rewritten"


def test_an_edit_mints_a_new_module_and_keeps_the_old(lib):
    library_emitter.emit(_manifest(_unit_asset("f", "e", "def f():\n    return 1\n", sha="v1")),
                         root=lib)
    library_emitter.emit(_manifest(_unit_asset("f", "e", "def f():\n    return 2\n", sha="v2")),
                         root=lib)
    versions = sorted(p.name for p in (lib / "iguide_methods").rglob("v_*.py"))
    assert versions == ["v_v1.py", "v_v2.py"], "an old version was destroyed"


def test_units_without_the_library_target_are_ignored(lib):
    a = _unit_asset("f", "e", "def f():\n    return 1\n", sha="x", targets=(EMIT_OPENSEARCH,))
    out = library_emitter.emit(_manifest(a), root=lib)
    assert out["units"] == 0 and out["written"] == []


def test_a_unit_without_source_is_skipped_not_written(lib):
    a = _unit_asset("f", "e", "", sha="x")
    out = library_emitter.emit(_manifest(a), root=lib)
    assert out["written"] == []
    assert out["skipped"] and "missing" in out["skipped"][0]["reason"]


def test_requirements_are_written_per_element(lib):
    library_emitter.emit(_manifest(_unit_asset("f", "e", "def f():\n    return 1\n", sha="x")),
                         root=lib)
    req = next((lib / "iguide_methods").rglob("requirements.txt"))
    assert "six" in req.read_text()


# --------------------------------------------------------------- naming safety

@pytest.mark.parametrize("title", ["../../escape", "Weird Title! **", "", "123"])
def test_element_package_names_are_safe_identifiers(title):
    name = library_emitter.element_package("abc12345", title)
    assert "/" not in name and ".." not in name
    assert name.replace("_", "a").isalnum()
