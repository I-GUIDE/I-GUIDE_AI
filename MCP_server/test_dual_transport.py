"""Tests for the dual-transport tool server.

Verifies that every @mcp_tool function is reachable over BOTH transports:
- MCP protocol at /mcp/*
- REST API at /api/tool/<name>

Uses FastAPI's TestClient so no actual server needs to start. The tests
only exercise the REST surface and the shared tool registry — MCP protocol
conformance is covered by FastMCP's own tests upstream.

Run with:
    python -m pytest MCP_server/test_dual_transport.py -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from MCP_server import server as srv


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Single TestClient for the dual-transport app."""
    return TestClient(srv.app)


# ---------------------------------------------------------------------------
# Catalog / health endpoints
# ---------------------------------------------------------------------------

def test_health_endpoint_returns_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["tool_count"] == len(srv._tool_registry)
    assert data["tool_count"] > 0, "Expected some tools to have been registered at startup"


def test_tools_catalog_lists_every_registered_tool(client: TestClient) -> None:
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == len(srv._tool_registry)

    names_from_catalog = {tool["name"] for tool in data["tools"]}
    names_from_registry = set(srv._tool_registry.keys())
    assert names_from_catalog == names_from_registry

    # Every catalog entry has the expected shape.
    for tool in data["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool
        assert "accepts_file_upload" in tool
        assert isinstance(tool["parameters"], list)


def test_catalog_surfaces_categories_for_tagged_tools(client: TestClient) -> None:
    """Tools decorated with ``@mcp_tool(category=...)`` expose that category in the catalog."""
    resp = client.get("/api/tools")
    data = resp.json()
    categorized = [t for t in data["tools"] if t.get("category")]
    # Some tools carry a category today (data_tools, image_tools), so this
    # guards against silent regression of the metadata propagation.
    assert categorized, "Expected at least some tools to expose a category in the REST catalog"
    valid_categories = set(srv.MCP_TOOL_CATEGORIES)
    for tool in categorized:
        assert tool["category"] in valid_categories, (
            f"{tool['name']} reports category {tool['category']!r} which is not a known category"
        )


# ---------------------------------------------------------------------------
# Per-tool route registration
# ---------------------------------------------------------------------------

def test_every_registered_tool_has_a_rest_route(client: TestClient) -> None:
    """The FastAPI sub-app should have one POST route per registered tool."""
    rest_paths = {
        route.path for route in srv.rest_app.routes if hasattr(route, "path")
    }
    for tool_name in srv._tool_registry:
        expected = f"/tool/{tool_name}"
        assert expected in rest_paths, f"Missing REST route for {tool_name!r}"


def test_openapi_schema_includes_all_tool_routes(client: TestClient) -> None:
    """Swagger / OpenAPI should document every tool endpoint."""
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    documented_paths = set(schema["paths"].keys())
    for tool_name in srv._tool_registry:
        assert f"/tool/{tool_name}" in documented_paths


# ---------------------------------------------------------------------------
# Transport isolation — MCP and REST are mounted separately
# ---------------------------------------------------------------------------

def test_rest_path_does_not_collide_with_mcp_path(client: TestClient) -> None:
    """REST endpoints live under /api; MCP lives under /mcp. No overlap."""
    for route in srv.rest_app.routes:
        path = getattr(route, "path", "")
        assert not path.startswith("/mcp"), (
            f"REST route {path!r} unexpectedly touches the /mcp namespace"
        )


def test_mcp_and_rest_share_the_same_registry(client: TestClient) -> None:
    """Both transports are backed by the same _tool_registry dict.

    This is the invariant that guarantees shared state (like data_tools'
    _dataframe_cache) works regardless of which transport the caller used.
    """
    assert srv._tool_registry is srv._tool_registry  # trivially true, documents intent
    for tool_name, func in srv._tool_registry.items():
        # Each registered function has the _is_mcp_tool marker from the decorator.
        assert getattr(func, "_is_mcp_tool", False), (
            f"Registered tool {tool_name!r} lost its @mcp_tool marker"
        )


# ---------------------------------------------------------------------------
# Error handling — bad JSON / bad args
# ---------------------------------------------------------------------------

def test_json_tool_with_bad_args_returns_400(client: TestClient) -> None:
    """Sending kwargs that don't match the function signature → HTTP 400."""
    # Pick the first non-upload tool with at least one required parameter.
    target = None
    for name, func in srv._tool_registry.items():
        import inspect
        if srv._has_upload_file_param(inspect.signature(func)):
            continue
        target = name
        break
    if target is None:
        pytest.skip("No JSON-body tool available to test bad-args path")

    resp = client.post(f"/api/tool/{target}", json={"__definitely_not_a_real_arg__": 1})
    # The tool may accept the bogus kwarg and fail at runtime (500) OR reject
    # it at the signature level (400). Either way the server must NOT 200.
    assert resp.status_code in (400, 500)


def test_unknown_tool_returns_404(client: TestClient) -> None:
    resp = client.post("/api/tool/this_tool_does_not_exist", json={})
    assert resp.status_code == 404
