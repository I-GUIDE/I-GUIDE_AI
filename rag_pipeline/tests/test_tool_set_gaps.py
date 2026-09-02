"""Tools registered somewhere but missing from a set that needs them.

This class of bug has bitten this repo repeatedly and is always silent: the peer simply does
not have the tool, the model falls back to something worse, and no error is raised. Two rounds
of tool-description tuning once failed to fix a tool that was not registered at all.
"""
from __future__ import annotations

import pytest


# --- skill tools reach a peer built from preloaded_tools -----------------------------------
#
# build_agent_executor accepted skill_roots but forwarded it ONLY into _collect_tools, which is
# skipped when preloaded_tools is supplied. default_analyze_fn passes both, so the analyze peer
# silently had neither list_available_skills nor load_skill. The code peer worked only because
# it calls make_skill_tools itself.

def _bound_tools(monkeypatch, **kwargs):
    seen = {}
    import langchain.agents as la

    def _spy(model=None, tools=None, **kw):
        seen["names"] = [str(getattr(t, "name", "")) for t in (tools or [])]
        return object()

    monkeypatch.setattr(la, "create_agent", _spy)
    from agent_runtime.executor_factory import build_agent_executor

    build_agent_executor(llm=object(), **kwargs)
    return seen["names"]


@pytest.fixture()
def a_tool():
    from langchain_core.tools import tool

    @tool
    def preloaded(x: str) -> str:
        """A preloaded tool."""
        return x

    return preloaded


def test_a_preloaded_peer_still_gets_the_skill_tools(monkeypatch, a_tool):
    names = _bound_tools(monkeypatch, preloaded_tools=[a_tool], skill_roots=None)
    assert "preloaded" in names
    assert "list_available_skills" in names and "load_skill" in names


def test_the_skill_tools_are_not_added_twice(monkeypatch, a_tool):
    """The code peer already calls make_skill_tools itself; the dedup makes this a no-op."""
    from agent_runtime.skills import make_skill_tools

    names = _bound_tools(monkeypatch,
                         preloaded_tools=[a_tool, *make_skill_tools(skill_roots=None)],
                         skill_roots=None)
    assert names.count("load_skill") == 1


# --- add_map_layer exists with nothing attached --------------------------------------------

def test_add_map_layer_is_bound_on_a_no_upload_turn():
    """"show me Champaign County on the map" needs it, and the peer had no way to deliver."""
    import inspect

    from agent_runtime.supervisor import graph as g

    for fn in (g.default_analyze_fn, g.default_code_fn):
        src = inspect.getsource(fn)
        assert 'if str(getattr(t, "name", "")) == "add_map_layer"' in src, fn.__name__
        assert "if not input_file_ids:" in src, fn.__name__


def test_only_add_map_layer_is_hoisted_not_the_whole_factory():
    """The other five need a vector file, and render_map_image is the static-PNG route the
    map observation exists to discourage — the whole factory costs ~1,479 tokens vs ~299."""
    from agent_runtime.langchain_geo_tools import make_langchain_geo_tools

    all_names = {str(getattr(t, "name", "")) for t in
                 make_langchain_geo_tools(default_input_file_ids=None)}
    assert "add_map_layer" in all_names
    assert {"inspect_vector", "render_map_image", "vector_to_geojson"} <= all_names


def test_the_corrective_map_retry_is_still_gated_on_an_upload():
    """Deliberately NOT un-gated: three changes already make it fire more readily and
    _WANTS_MAP_RE matches a bare "on the map", so an ordinary follow-up would be told the map
    got nothing and redundantly re-add a layer already on screen."""
    import inspect

    from agent_runtime.supervisor import graph as g

    src = inspect.getsource(g.default_analyze_fn)
    assert "if input_file_ids and wants_map and not on_map" in src


# --- enabledSearchMethods reaches the analyze peer -----------------------------------------

def test_the_analyze_peer_accepts_an_allowlist():
    import inspect

    from agent_runtime.supervisor import graph as g

    assert "enabled_search_methods" in inspect.signature(g.default_analyze_fn).parameters
    src = inspect.getsource(g.default_analyze_fn)
    assert "enabled_search_methods=enabled_search_methods" in src


def test_orchestration_passes_the_allowlist_to_both_arms():
    import inspect

    from agent_runtime.supervisor import orchestration

    src = inspect.getsource(orchestration)
    assert src.count("enabled_search_methods=cfg.enabled_search_methods") >= 2


# --- the capability answer covers what the peers actually bind -----------------------------

def test_the_capability_inventory_covers_the_embedding_surface():
    """It omitted BOTH rs-embed factories plus admin_boundary, QGIS and code execution — five
    registries every peer binds — so "what can you do" never mentioned satellite embeddings."""
    from agent_runtime.capabilities import collect_capability_inventory

    inv = collect_capability_inventory(include_mcp_tools=False)
    entries = inv if isinstance(inv, list) else inv.get("tools", [])
    names = {e["name"] for e in entries}
    for want in ("embed_region", "embed_zones", "fit_zone_model", "predict_for_region",
                 "admin_boundary", "execute_code"):
        assert want in names, f"{want} is bound by every peer but invisible to the user"


def test_a_broken_registry_does_not_take_the_inventory_down(monkeypatch):
    """Behaviour, not source shape: one unconstructible registry must not lose the others.

    This used to count `except Exception:` blocks in collect_capability_inventory — one per
    hand-written registry. Those are gone: registries are discovered by convention, so a hand
    list can no longer be wrong (it was wrong twice, and an audit right after fixing it found
    two more factories still missing). Isolation now lives in the discovery loop.
    """
    import agent_runtime.capabilities as cap

    def _boom(**kwargs):
        raise RuntimeError("this registry cannot be built")

    real = cap._discover_registry_factories
    monkeypatch.setattr(cap, "_discover_registry_factories",
                        lambda: [("make_broken_tools", _boom), *real()])

    inv = cap.collect_capability_inventory(include_mcp_tools=False)
    names = {t["name"] for t in inv["tools"]}
    assert len(names) > 40, "a broken registry must not empty the inventory"
    assert "embed_zones" in names


def test_a_new_registry_needs_no_edit_to_this_module():
    """The point of discovery: adding a make_*tools factory is enough."""
    from agent_runtime.capabilities import _discover_registry_factories

    found = {n for n, _ in _discover_registry_factories()}
    # the ones a hand-written list had missed, in two separate rounds
    for want in ("make_rs_embed_tools", "make_rs_embed_zonal_tools",
                 "make_langchain_file_tools", "make_quality_tools"):
        assert want in found, f"{want} must be discovered, not listed"


def test_only_read_only_listers_are_handed_to_the_capability_agent():
    """Generic by prefix, so a new list_* tool is offered without an edit — and nothing that
    could act is included."""
    import agent_runtime.capabilities as cap
    import agent_runtime.executor_factory as ef

    captured = {}

    def _build(**kw):
        captured["tools"] = [str(getattr(t, "name", ""))
                             for t in (kw.get("preloaded_tools") or [])]
        return object()

    orig_build, orig_invoke, orig_llm = (ef.build_agent_executor,
                                         ef.invoke_agent_with_payload_fallback,
                                         ef.build_default_llm)
    ef.build_agent_executor = _build
    ef.invoke_agent_with_payload_fallback = lambda *a, **k: {"messages": []}
    ef.build_default_llm = lambda: object()
    try:
        cap.describe_capabilities(query="which models do you have?", include_mcp_tools=False)
    finally:
        (ef.build_agent_executor, ef.invoke_agent_with_payload_fallback,
         ef.build_default_llm) = orig_build, orig_invoke, orig_llm

    names = captured["tools"]
    assert "list_my_capabilities" in names
    assert "list_embedding_models" in names, "the real model names must be reachable"
    non_listers = [n for n in names if not n.startswith("list_")]
    assert non_listers == [], f"only read-only listers belong here, got {non_listers}"
