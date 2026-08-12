"""Callability analysis: can an extracted function be called on its own?

The problem: a function lifted from notebook cell 12 that reads ``gdf`` defined in cell 4
imports fine and then fails at call time, or worse uses a stale global. That is why
``notebook_extractor`` promotes at most one whole-notebook entry point today rather than
per-function units.

The distinction that matters is not "reads a global" but *what kind of binding* the global is:
imports, sibling defs and literal consts can all be copied into a slice; a value produced by
executing something (``gpd.read_file(...)``) cannot.

Pure ast/symtable — no cluster, no LLM, no execution.
"""

from __future__ import annotations

import ast

import pytest

from extractors.analysis import analyze_module, analyze_unit, iter_units, module_scope
from extractors.analysis.signatures import contract_params, params_of, signature_of
from extractors.contracts import CALLABLE, NEEDS_GLOBALS, UNPARSEABLE

CANONICAL = '''
import geopandas as gpd
from pathlib import Path
THRESH = 0.5
LABELS = ["a", "b"]
gdf_missing = gpd.read_file("x.shp")

def helper(x):
    return x * THRESH

def good(path, k=3):
    """Self-contained."""
    return helper(gpd.read_file(path).head(k))

def bad(k):
    return gdf_missing.head(k)

def depends_on_bad(k):
    return bad(k) + 1

def unbound(k):
    return mystery_name + k
'''


# --------------------------------------------------------------- module scope

def test_module_bindings_are_classified_by_kind():
    tree = ast.parse(CANONICAL)
    scope = module_scope(tree, CANONICAL)
    assert set(scope.imports) == {"gpd", "Path"}
    assert set(scope.consts) == {"THRESH", "LABELS"}, "a literal list must be a const"
    assert set(scope.runtime) == {"gdf_missing"}, "a call result must be runtime, not const"
    assert set(scope.defs) >= {"helper", "good", "bad"}


def test_a_computed_value_is_never_treated_as_a_const():
    """This is the whole distinction: inlining a const is safe, inlining a call is not."""
    src = "import geopandas as gpd\nA = 1 + 2\nB = gpd.read_file('x')\n"
    scope = module_scope(ast.parse(src), src)
    assert "A" in scope.consts
    assert "B" in scope.runtime


# --------------------------------------------------------------- verdicts

def test_const_and_import_reads_stay_callable():
    verdicts, _, _ = analyze_module(CANONICAL)
    assert verdicts["helper"].verdict == CALLABLE      # reads THRESH (a const)
    assert verdicts["good"].verdict == CALLABLE        # reads gpd (import) + helper (def)
    assert "THRESH" in verdicts["helper"].requires_consts
    assert "gpd" in verdicts["good"].requires_imports
    assert "helper" in verdicts["good"].requires_units


def test_a_runtime_global_blocks_and_is_named():
    verdicts, _, _ = analyze_module(CANONICAL)
    c = verdicts["bad"]
    assert c.verdict == NEEDS_GLOBALS
    assert c.blocked_by == ["gdf_missing"]
    assert "gdf_missing" in c.reason, "the reason must name the offending binding"


def test_an_unbound_name_blocks():
    verdicts, _, _ = analyze_module(CANONICAL)
    c = verdicts["unbound"]
    assert c.verdict == NEEDS_GLOBALS
    assert "mystery_name" in c.blocked_by


def test_transitive_demotion():
    """Copying a unit into a slice is only safe if everything it calls is safe too."""
    verdicts, _, _ = analyze_module(CANONICAL)
    c = verdicts["depends_on_bad"]
    assert c.verdict == NEEDS_GLOBALS
    assert "bad" in c.reason


def test_headline_metric_and_blocked_by_histogram():
    _, _, summary = analyze_module(CANONICAL)
    assert summary["total"] == 5
    assert summary["callable"] == 2                     # helper, good
    assert set(summary["needs_globals"]) == {"bad", "depends_on_bad", "unbound"}
    assert summary["blocked_by"]["gdf_missing"] == 1


# --------------------------------------------------------------- robustness

def test_a_syntax_error_degrades_instead_of_raising():
    """One bad extracted module must not fail a whole ingest."""
    verdicts, _, summary = analyze_module("def broken(:\n  pass")
    assert verdicts == {}
    assert summary["unparseable"] is True
    assert summary["callable"] == 0


def test_analyze_unit_reports_a_missing_name():
    c = analyze_unit(CANONICAL, "nope")
    assert c.verdict == UNPARSEABLE


def test_closure_variables_are_not_globals():
    """symtable reports closure vars as free, not global — the main reason for using it."""
    src = "def outer():\n    v = 1\n    def inner():\n        return v\n    return inner()\n"
    verdicts, _, _ = analyze_module(src)
    assert verdicts["outer"].verdict == CALLABLE, "a closure was misread as a hidden global"


def test_comprehension_scope_is_handled():
    src = "ITEMS = [1, 2]\ndef f():\n    return [x * 2 for x in ITEMS]\n"
    verdicts, _, _ = analyze_module(src)
    assert verdicts["f"].verdict == CALLABLE


def test_builtins_are_not_blockers():
    src = "def f(xs):\n    return len(sorted(xs))\n"
    verdicts, _, _ = analyze_module(src)
    assert verdicts["f"].verdict == CALLABLE
    assert verdicts["f"].blocked_by == []


def test_public_methods_are_units_but_private_ones_are_not():
    src = ("class Loader:\n"
           "    def load(self, p):\n        return p\n"
           "    def _hidden(self):\n        return 1\n")
    names = [n for n, _ in iter_units(ast.parse(src))]
    assert "Loader.load" in names
    assert "Loader._hidden" not in names


def test_a_module_level_write_is_impurity_not_a_blocker():
    src = "COUNT = 0\ndef bump():\n    global COUNT\n    COUNT += 1\n    return COUNT\n"
    verdicts, _, _ = analyze_module(src)
    c = verdicts["bump"]
    assert "COUNT" in c.global_writes
    assert c.verdict == CALLABLE, "a write is contained in the slice; it must not block"


# --------------------------------------------------------------- signatures

def test_signature_keeps_everything_the_old_impl_dropped():
    src = "def f(p, /, a: int = 3, *args: str, k: float = 1.0, **kw) -> 'gpd.GeoDataFrame': pass"
    sig = signature_of(ast.parse(src).body[0])
    for fragment in ("p", "/", "a: int=3", "*args: str", "k: float=1.0", "**kw",
                     "gpd.GeoDataFrame"):
        assert fragment in sig, f"{fragment!r} missing from {sig!r}"


def test_params_cover_all_five_categories():
    src = "def f(p, /, a, b=1, *args, c, d=2, **kw): pass"
    kinds = {x.name: x.kind for x in params_of(ast.parse(src).body[0])}
    assert kinds["p"] == "positional_only"
    assert kinds["a"] == "positional_or_keyword"
    assert kinds["args"] == "var_positional"
    assert kinds["c"] == "keyword_only"
    assert kinds["kw"] == "var_keyword"


def test_required_vs_optional_is_recorded():
    src = "def f(a, b=1, *, c, d=2): pass"
    req = {x.name: x.required for x in params_of(ast.parse(src).body[0])}
    assert req == {"a": True, "b": False, "c": True, "d": False}


def test_async_functions_are_supported():
    src = "async def fetch(url: str) -> dict: pass"
    assert signature_of(ast.parse(src).body[0]).startswith("async def fetch(url: str)")


# --------------------------------------------------------------- CRS / unit inference

def test_a_metric_operation_implies_a_projected_crs():
    """The degrees-vs-metres class: a wrong CRS yields a plausible number, not an error."""
    src = "def buf(gdf, radius_m=25000):\n    return gdf.buffer(radius_m)\n"
    node = ast.parse(src).body[0]
    params = {p.name: p for p in contract_params(node)}
    assert params["gdf"].inferred_type == "geodataframe"
    assert params["gdf"].crs_expectation == "projected"
    assert params["gdf"].declared_unit == "metres"
    assert "buffer" in params["gdf"].evidence, "an inference must record its evidence"


def test_no_metric_operation_means_no_crs_claim():
    """An undetermined expectation stays empty; a wrong guess is worse than none."""
    src = "def head(gdf, k=5):\n    return gdf.head(k)\n"
    params = {p.name: p for p in contract_params(ast.parse(src).body[0])}
    assert params["gdf"].crs_expectation == ""
    assert params["gdf"].declared_unit == ""


def test_annotation_beats_the_name_heuristic():
    src = "def f(df: int):\n    return df\n"
    p = contract_params(ast.parse(src).body[0])[0]
    assert p.inferred_type == "number", "a declared annotation must win over a name guess"
