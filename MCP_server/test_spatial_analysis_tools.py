import importlib.util
import sys
import types
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon


def _identity_mcp_tool(_func=None, **_kwargs):
    def decorator(func):
        func._is_mcp_tool = True
        return func

    if callable(_func):
        return decorator(_func)
    return decorator


def _load_tool_module(module_name: str, filename: str, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    mcp_root = repo_root / "MCP_server"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))

    monkeypatch.setitem(sys.modules, "server", types.SimpleNamespace(mcp_tool=_identity_mcp_tool))
    monkeypatch.setitem(
        sys.modules,
        "ddgs",
        types.SimpleNamespace(DDGS=object),
    )

    path = mcp_root / "tools" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_data_and_spatial_tools_share_dataframe_cache(monkeypatch):
    data_tools = _load_tool_module("data_tools_under_test", "data_tools.py", monkeypatch)
    spatial_tools = _load_tool_module(
        "spatial_analysis_tools_under_test", "spatial_analysis_tools.py", monkeypatch
    )

    assert data_tools._dataframe_cache is spatial_tools._dataframe_cache

    data_tools._dataframe_cache["sentinel"] = object()
    assert "sentinel" in spatial_tools._dataframe_cache


def test_count_crimes_per_community_counts_only_matched_points(monkeypatch):
    spatial_tools = _load_tool_module(
        "spatial_analysis_tools_count_test", "spatial_analysis_tools.py", monkeypatch
    )
    spatial_tools._dataframe_cache.clear()
    spatial_tools._dataframe_cache["chicago_community_areas"] = gpd.GeoDataFrame(
        {
            "community": ["ALPHA", "BETA"],
            "geometry": [
                Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
                Polygon([(3, 3), (5, 3), (5, 5), (3, 5)]),
            ],
        },
        crs="EPSG:4326",
    )
    spatial_tools._dataframe_cache["chicago_crime_data"] = gpd.GeoDataFrame(
        {
            "primary_type": ["THEFT", "BATTERY"],
            "geometry": [Point(1, 1), Point(1.5, 1.5)],
        },
        crs="EPSG:4326",
    )

    result = spatial_tools.count_crimes_per_community(crime_type="THEFT")

    assert result["total_crimes"] == 1
    assert result["crime_counts"] == {"ALPHA": 1, "BETA": 0}


def test_load_chicago_crime_data_reuses_cached_dataframe(monkeypatch):
    data_tools = _load_tool_module("tools.data_tools", "data_tools.py", monkeypatch)
    data_tools._dataframe_cache.clear()
    calls = {"read_json": 0}

    def fake_read_json(_url):
        calls["read_json"] += 1
        return pd.DataFrame(
            {
                "id": ["1"],
                "date": ["2026-01-01T00:00:00.000"],
                "primary_type": ["THEFT"],
                "description": ["OVER $500"],
                "location_description": ["STREET"],
                "arrest": [False],
                "longitude": [-87.6],
                "latitude": [41.8],
            }
        )

    monkeypatch.setattr(data_tools.pd, "read_json", fake_read_json)

    first = data_tools.load_chicago_crime_data()
    second = data_tools.load_chicago_crime_data()

    assert calls["read_json"] == 1
    assert first["_cache_hit"] is False
    assert second["_cache_hit"] is True
    assert second["_cache_key"] == "chicago_crime_data"


def test_count_crimes_per_community_auto_loads_once_then_uses_cache(monkeypatch):
    data_tools = _load_tool_module("tools.data_tools", "data_tools.py", monkeypatch)
    spatial_tools = _load_tool_module("tools.spatial_analysis_tools", "spatial_analysis_tools.py", monkeypatch)
    spatial_tools._dataframe_cache.clear()
    calls = {"read_file": 0, "read_json": 0}

    def fake_read_file(_url):
        calls["read_file"] += 1
        return gpd.GeoDataFrame(
            {
                "community": ["ALPHA", "BETA"],
                "geometry": [
                    Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
                    Polygon([(3, 3), (5, 3), (5, 5), (3, 5)]),
                ],
            },
            crs="EPSG:4326",
        )

    def fake_read_json(_url):
        calls["read_json"] += 1
        return pd.DataFrame(
            {
                "id": ["1", "2"],
                "date": ["2026-01-01T00:00:00.000", "2026-01-02T00:00:00.000"],
                "primary_type": ["THEFT", "BATTERY"],
                "description": ["OVER $500", "SIMPLE"],
                "location_description": ["STREET", "SIDEWALK"],
                "arrest": [False, True],
                "longitude": [1.0, 1.5],
                "latitude": [1.0, 1.5],
            }
        )

    monkeypatch.setattr(data_tools.gpd, "read_file", fake_read_file)
    monkeypatch.setattr(data_tools.pd, "read_json", fake_read_json)

    first = spatial_tools.count_crimes_per_community(crime_type="THEFT")
    second = spatial_tools.count_crimes_per_community(crime_type="THEFT")

    assert calls == {"read_file": 1, "read_json": 1}
    assert first["total_crimes"] == 1
    assert second["total_crimes"] == 1
    assert second["crime_counts"] == {"ALPHA": 1, "BETA": 0}
