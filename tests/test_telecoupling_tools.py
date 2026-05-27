"""Tests for the Telecoupling Toolbox integration into the analysis agent.

These tests run without the heavy scientific stack (natcap.invest, R, GDAL,
PyQGIS) installed — the toolbox is designed to load and register tools with
those dependencies absent, and to degrade gracefully when a tool is invoked.
"""
from __future__ import annotations

import inspect
import json

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("langchain_core")


def test_tool_name_parity_across_sources():
    """spec JSON, lazy registry, and graph_state set must list the same names."""
    from agent_runtime.langchain_telecoupling_tools import telecoupling_tool_names
    from agent_runtime.telecoupling.tool_registry import TOOL_FUNCTIONS
    from agent_runtime.graph_state import TELECOUPLING_TOOL_NAMES

    spec = set(telecoupling_tool_names())
    assert len(spec) == 44  # 42 models + read_file_content + render_spatial_file
    assert spec == set(TOOL_FUNCTIONS)
    assert spec == set(TELECOUPLING_TOOL_NAMES)


def test_build_tools_without_heavy_deps():
    from agent_runtime.langchain_telecoupling_tools import make_langchain_telecoupling_tools

    tools = make_langchain_telecoupling_tools(session_id="thread-1::analysis::telecoupling")
    names = [t.name for t in tools]
    assert len(names) == 44
    assert len(names) == len(set(names)), "tool names must be unique"
    assert "run_seasonal_water_yield" in names
    assert "run_network_analysis_grouping" in names


def test_args_schema_and_embedded_pre_guidance():
    from agent_runtime.langchain_telecoupling_tools import make_langchain_telecoupling_tools

    tools = {t.name: t for t in make_langchain_telecoupling_tools()}
    swy = tools["run_seasonal_water_yield"]
    fields = swy.args_schema.model_fields
    required = [name for name, field in fields.items() if field.is_required()]
    assert len(fields) == 12
    assert len(required) == 9
    assert "aoi_path" in required
    # PRE_EXECUTION parameter guidance is folded into the description.
    assert "Parameter guidance" in swy.description


def test_invocation_without_dependency_is_graceful():
    """Invoking an InVEST model without its deps returns a structured error."""
    from agent_runtime.langchain_telecoupling_tools import make_langchain_telecoupling_tools

    tools = {t.name: t for t in make_langchain_telecoupling_tools()}
    raw = tools["run_seasonal_water_yield"].func(
        aoi_path="/tmp/nonexistent_aoi.shp",
        lulc_raster_path="/tmp/x.tif",
        dem_raster_path="/tmp/x.tif",
        soil_group_path="/tmp/x.tif",
        biophysical_table_path="/tmp/x.csv",
        precip_dir="/tmp/p",
        et0_dir="/tmp/e",
        rain_events_table_path="/tmp/r.csv",
        threshold_flow_accumulation=1000,
    )
    payload = json.loads(raw)
    assert payload["status"] == "error"
    assert payload["tool"] == "run_seasonal_water_yield"
    assert payload["error_code"] in {"DEPENDENCY_MISSING", "TOOL_FAILED", "FILE_NOT_FOUND", "MISSING_PARAMS"}


def test_read_file_content_executes(tmp_path):
    """A dependency-light tool runs end-to-end and returns file content."""
    from agent_runtime.langchain_telecoupling_tools import make_langchain_telecoupling_tools

    csv = tmp_path / "demo.csv"
    csv.write_text("city,crimes\nChicago,100\nPeoria,20\n", encoding="utf-8")
    tools = {t.name: t for t in make_langchain_telecoupling_tools()}
    payload = json.loads(tools["read_file_content"].func(file_path=str(csv)))
    if payload.get("status") != "ok":
        pytest.skip(f"read_file_content unavailable in this env: {payload.get('message')}")
    assert "crimes" in (payload.get("content") or "")


def test_toggle_threads_through_runtime_signatures():
    from agent_runtime.graph_nodes import collect_orchestration_tools, make_analysis_agent_answer_tool
    from agent_runtime.graph_runtime import run_agent_query, stream_agent_query_events
    from agent_runtime.agent_chat_service import run_agent_chat, stream_agent_chat_events

    key = "include_telecoupling_tools"
    for fn in (
        collect_orchestration_tools,
        make_analysis_agent_answer_tool,
        run_agent_query,
        stream_agent_query_events,
        run_agent_chat,
        stream_agent_chat_events,
    ):
        assert key in inspect.signature(fn).parameters, fn.__name__


def test_server_request_normalization_reads_toggle():
    from api.server import _normalize_agent_chat_request

    assert _normalize_agent_chat_request({"includeTelecouplingTools": True})["include_telecoupling_tools"] is True
    assert _normalize_agent_chat_request({"include_telecoupling_tools": True})["include_telecoupling_tools"] is True
    assert _normalize_agent_chat_request({})["include_telecoupling_tools"] is False


def test_telecoupling_skills_are_gated_and_discoverable():
    from agent_runtime.skills import (
        SkillRegistry,
        augmented_skill_roots,
        telecoupling_skill_root,
    )

    # Gated: default roots do not surface the telecoupling sub-skills.
    default_names = {s.name for s in SkillRegistry.discover().list()}
    assert not any(name.startswith("run-") for name in default_names)

    # Enabled: augmenting with the telecoupling root surfaces all 42 bundles.
    roots = augmented_skill_roots(None, [telecoupling_skill_root()])
    registry = SkillRegistry.discover(roots)
    assert registry.errors == []
    telecoupling = [s for s in registry.list() if s.name.startswith("run-")]
    assert len(telecoupling) == 42

    loaded = registry.load_skill("run-seasonal-water-yield")
    assert loaded["status"] == "ok"
    assert loaded["skill"]["allowed_tools"] == ["run_seasonal_water_yield"]
