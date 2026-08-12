"""Finding and reading extracted methods: kb_method_search / get_method_contract.

The library emitter writes callable slices and the sandbox mounts them, but until these tools
existed the agent had no way to learn a unit was there — 40 extracted, 0 discoverable.

Two properties carry the weight:

* an import line returned here must be the one that works inside the sandbox, and
* an ambiguous bare name must refuse rather than resolve. The registry admits collisions
  (three symbols on the real corpus are defined by two elements each); a tool that quietly
  returned one of them would hand the agent a confidently wrong import.

Also asserted: both names are in ``RAG_COMPONENT_TOOL_NAMES``. That set is a hard filter in
``tool_policy``, so a tool missing from it is registered, documented, and unreachable for
every intent — a failure that no test of the tool function itself can catch.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime import method_library as ml


@pytest.fixture()
def registry():
    return {
        "ke_crime.load_crime_points": {
            "module": "iguide_methods.ke_crime.v_ab12cd34",
            "library_symbol": "load_crime_points",
            "qualified_name": "load_crime_points",
            "element_package": "ke_crime",
            "signature": "def load_crime_points(path, crs='EPSG:4326')",
            "doc_summary": "Read Chicago crime CSV into a GeoDataFrame of points.",
            "params": [{"name": "path", "kind": "positional"}],
            "returns": "GeoDataFrame",
            "invariants": [{"name": "requires_projected_crs"}],
            "requirements": {"pip": ["geopandas"]},
            "slice_sha": "ab12cd34",
            "provenance": {"element_id": "cca9b545", "element_title": "Chicago Crime Heatmap"},
        },
        "load_crime_points": {
            "module": "iguide_methods.ke_crime.v_ab12cd34",
            "library_symbol": "load_crime_points",
            "alias_for": "ke_crime.load_crime_points",
            "signature": "def load_crime_points(path, crs='EPSG:4326')",
            "doc_summary": "Read Chicago crime CSV into a GeoDataFrame of points.",
            "requirements": {"pip": ["geopandas"]},
            "slice_sha": "ab12cd34",
            "provenance": {"element_id": "cca9b545"},
        },
        "ke_flood.compute_accessibility": {
            "module": "iguide_methods.ke_flood.v_ff00ff00",
            "library_symbol": "compute_accessibility",
            "element_package": "ke_flood",
            "signature": "def compute_accessibility(demand, supply)",
            "doc_summary": "Two-step floating catchment accessibility.",
            "requirements": {"pip": []},
            "slice_sha": "ff00ff00",
            "provenance": {"element_id": "aaa11122", "element_title": "Spatial Accessibility"},
        },
        "get_url": {
            "ambiguous": True,
            "library_symbol": "get_url",
            "candidates": ["ke_alpha.get_url", "ke_beta.get_url"],
            "doc_summary": "'get_url' is defined by more than one element; "
                           "import it by its qualified name.",
        },
        "ke_alpha.get_url": {
            "module": "iguide_methods.ke_alpha.v_1", "library_symbol": "get_url",
            "element_package": "ke_alpha", "signature": "def get_url(x)",
            "doc_summary": "alpha url", "slice_sha": "1", "provenance": {"element_id": "alpha"},
        },
        "ke_beta.get_url": {
            "module": "iguide_methods.ke_beta.v_2", "library_symbol": "get_url",
            "element_package": "ke_beta", "signature": "def get_url(y)",
            "doc_summary": "beta url", "slice_sha": "2", "provenance": {"element_id": "beta"},
        },
    }


# ------------------------------------------------------------------ search

def test_a_topic_word_finds_the_method(registry):
    hits = ml.search_methods("chicago crime points", registry=registry)
    assert hits and hits[0]["symbol"] == "ke_crime.load_crime_points"


def test_a_snake_case_symbol_is_findable_by_its_parts(registry):
    """'load_crime_points' matched as one opaque token would make the obvious query fail."""
    assert ml.search_methods("crime", registry=registry)[0]["symbol"] == "ke_crime.load_crime_points"
    assert ml.search_methods("load points", registry=registry)[0]["symbol"] == "ke_crime.load_crime_points"


def test_search_returns_a_usable_import_line(registry):
    hit = ml.search_methods("accessibility", registry=registry)[0]
    assert hit["import_line"] == (
        "from iguide_methods.ke_flood.v_ff00ff00 import compute_accessibility")


def test_the_import_line_is_pinned_to_the_slice_sha(registry):
    """A re-ingest must not silently change the code a recorded artifact re-imports."""
    hit = ml.search_methods("accessibility", registry=registry)[0]
    assert "v_ff00ff00" in hit["import_line"]


def test_bare_aliases_do_not_duplicate_their_qualified_entry(registry):
    hits = ml.search_methods("crime", registry=registry)
    assert [h["symbol"] for h in hits].count("ke_crime.load_crime_points") == 1
    assert "load_crime_points" not in [h["symbol"] for h in hits]


def test_an_ambiguous_name_surfaces_as_ambiguous_not_as_one_winner(registry):
    hits = ml.search_methods("get_url", registry=registry)
    amb = [h for h in hits if h.get("ambiguous")]
    assert amb and sorted(amb[0]["candidates"]) == ["ke_alpha.get_url", "ke_beta.get_url"]


def test_no_match_returns_empty_rather_than_a_weak_guess(registry):
    assert ml.search_methods("quantum chromodynamics", registry=registry) == []


def test_search_respects_its_limit(registry):
    assert len(ml.search_methods("url crime accessibility", registry=registry, limit=1)) == 1


# ------------------------------------------------------------------ contracts

def test_a_contract_carries_invariants_and_requirements(registry):
    c = ml.get_contract("ke_crime.load_crime_points", registry=registry)
    assert c["signature"].startswith("def load_crime_points")
    assert c["invariants"] == [{"name": "requires_projected_crs"}]
    assert c["requirements"]["pip"] == ["geopandas"]
    assert c["provenance"]["element_id"] == "cca9b545"


def test_a_bare_name_resolves_through_its_alias(registry):
    c = ml.get_contract("load_crime_points", registry=registry)
    assert c["import_line"] == "from iguide_methods.ke_crime.v_ab12cd34 import load_crime_points"
    assert c["invariants"] == [{"name": "requires_projected_crs"}]


def test_an_ambiguous_contract_refuses_and_lists_candidates(registry):
    c = ml.get_contract("get_url", registry=registry)
    assert c.get("ambiguous") is True
    assert c["candidates"] == ["ke_alpha.get_url", "ke_beta.get_url"]
    assert "import_line" not in c, "an ambiguous name must not produce an import line"


def test_an_unqualified_name_with_one_owner_still_resolves(registry):
    c = ml.get_contract("compute_accessibility", registry=registry)
    assert c["module"] == "iguide_methods.ke_flood.v_ff00ff00"


def test_an_unknown_symbol_reports_what_is_available(registry):
    c = ml.get_contract("no_such_method", registry=registry)
    assert "error" in c and c["available"] >= 3


def test_an_empty_symbol_is_an_error_not_a_lookup(registry):
    assert "error" in ml.get_contract("", registry=registry)


# ------------------------------------------------------------------ empty library

def test_an_absent_library_reads_as_empty_not_a_crash(monkeypatch):
    monkeypatch.setattr(ml, "registry_path", lambda: None)
    assert ml.load_registry() == {}
    assert ml.search_methods("anything") == []


def test_an_empty_library_says_so_instead_of_implying_none_exists(monkeypatch):
    """"No results" and "nothing ingested" are different claims; the model cannot tell them
    apart from an empty list, and reports the stronger one."""
    monkeypatch.setattr(ml, "load_registry", lambda: {})
    monkeypatch.setattr(ml, "library_root", lambda: None)
    from agent_runtime.langchain_granular_tools import kb_method_search_tool

    payload = json.loads(kb_method_search_tool("crime"))
    assert payload["results"] == []
    assert "not evidence" in payload["note"]


def test_a_populated_library_with_no_match_says_how_many_units_exist(monkeypatch, registry):
    monkeypatch.setattr(ml, "load_registry", lambda: registry)
    monkeypatch.setattr(ml, "library_root", lambda: "/tmp/lib")
    from agent_runtime.langchain_granular_tools import kb_method_search_tool

    payload = json.loads(kb_method_search_tool("quantum chromodynamics"))
    assert payload["results"] == []
    assert "units" in payload["note"] and payload["library"]["units"] >= 3


# ------------------------------------------------------------------ wiring

def test_both_tools_are_in_the_component_filter():
    """tool_policy filters against this set; a name absent from it is stripped for EVERY intent."""
    from agent_runtime.graph_state import RAG_COMPONENT_TOOL_NAMES

    assert {"kb_method_search", "get_method_contract"} <= RAG_COMPONENT_TOOL_NAMES


def test_both_tools_are_registered_with_the_agent():
    from agent_runtime.langchain_granular_tools import make_langchain_granular_tools

    names = {getattr(t, "name", "") for t in make_langchain_granular_tools()}
    assert {"kb_method_search", "get_method_contract"} <= names


@pytest.mark.parametrize("intent", ["code_task", "general_discovery", "hybrid"])
def test_the_tools_survive_the_policy_filter(intent):
    """code_task is the one that matters — that peer writes the analysis code."""
    from agent_runtime.langchain_granular_tools import make_langchain_granular_tools
    from agent_runtime.tool_policy import select_allowed_tools

    names = [getattr(t, "name", "") for t in make_langchain_granular_tools()]
    kept = set(select_allowed_tools(intent, names))
    assert {"kb_method_search", "get_method_contract"} <= kept


def test_analysis_intent_drops_them_and_that_is_the_documented_behavior():
    """``analysis_task`` selects ANALYSIS_TOOL_NAMES only — no retrieval component tool
    survives it, ``agent_kb_search`` included. Pinned so that if M5 folds the analyze peer
    into code, this expectation is revisited deliberately rather than discovered in a run."""
    from agent_runtime.langchain_granular_tools import make_langchain_granular_tools
    from agent_runtime.tool_policy import select_allowed_tools

    names = [getattr(t, "name", "") for t in make_langchain_granular_tools()]
    kept = set(select_allowed_tools("analysis_task", names))
    assert "kb_method_search" not in kept
    assert "agent_kb_search" not in kept


def test_the_tool_returns_json_the_model_can_parse(monkeypatch, registry):
    monkeypatch.setattr(ml, "load_registry", lambda: registry)
    monkeypatch.setattr(ml, "library_root", lambda: "/tmp/lib")
    from agent_runtime.langchain_granular_tools import (get_method_contract_tool,
                                                        kb_method_search_tool)

    hits = json.loads(kb_method_search_tool("crime", limit=3))["results"]
    assert hits[0]["symbol"] == "ke_crime.load_crime_points"
    contract = json.loads(get_method_contract_tool(hits[0]["symbol"]))
    assert contract["import_line"] == hits[0]["import_line"]
