"""Tests for the MCP tool list cache.

These tests verify that ``make_langchain_mcp_tools()`` avoids redundant
network calls within the TTL window and respects the escape hatch.
They patch the underlying remote fetch with a call counter so no real
MCP server or network is needed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rag_pipeline import langchain_mcp_tools as mcp_tools_module
from rag_pipeline.langchain_mcp_tools import (
    clear_mcp_cache,
    get_mcp_cache_stats,
    make_langchain_mcp_tools,
)


class _FakeTool:
    """Minimal stand-in for a LangChain StructuredTool."""

    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture(autouse=True)
def _reset_cache_and_env(monkeypatch):
    """Every test starts with an empty cache and a deterministic TTL."""
    monkeypatch.setenv("MCP_CACHE_TTL_SECONDS", "60")
    clear_mcp_cache()
    yield
    clear_mcp_cache()


def _fake_remote_factory():
    """Build a fresh fake + counter pair so each test is isolated."""
    counter = {"calls": 0}

    def _fake_make_remote_mcp_tools(url: str):
        counter["calls"] += 1
        return [_FakeTool("mcp_example_a"), _FakeTool("mcp_example_b")]

    return _fake_make_remote_mcp_tools, counter


def test_cache_prevents_redundant_remote_fetches():
    fake_make_remote, counter = _fake_remote_factory()
    with patch.object(mcp_tools_module, "_make_remote_mcp_tools", fake_make_remote):
        first = make_langchain_mcp_tools()
        second = make_langchain_mcp_tools()
        third = make_langchain_mcp_tools()

    assert counter["calls"] == 1, "Expected exactly one remote fetch across three calls"
    assert len(first) == 2
    assert [tool.name for tool in first] == [tool.name for tool in second] == [tool.name for tool in third]

    stats = get_mcp_cache_stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 2, f"Expected 2 cache hits, got {stats['hits']}"
    assert stats["misses"] == 1
    assert stats["stores"] == 1


def test_cache_separates_entries_by_include_modules():
    fake_make_remote, counter = _fake_remote_factory()
    with patch.object(mcp_tools_module, "_make_remote_mcp_tools", fake_make_remote):
        make_langchain_mcp_tools(include_modules=["data_tools"])
        make_langchain_mcp_tools(include_modules=["data_tools"])  # hit
        make_langchain_mcp_tools(include_modules=["search_tools"])  # miss — different key

    assert counter["calls"] == 2, "Different module selections should miss independently"
    stats = get_mcp_cache_stats()
    assert stats["entries"] == 2
    assert stats["hits"] == 1
    assert stats["misses"] == 2


def test_clear_mcp_cache_forces_refetch():
    fake_make_remote, counter = _fake_remote_factory()
    with patch.object(mcp_tools_module, "_make_remote_mcp_tools", fake_make_remote):
        make_langchain_mcp_tools()
        make_langchain_mcp_tools()  # cache hit
        clear_mcp_cache()
        make_langchain_mcp_tools()  # fresh fetch

    assert counter["calls"] == 2
    stats = get_mcp_cache_stats()
    # clear_mcp_cache() resets the counters too, so only the post-clear stats remain.
    assert stats["entries"] == 1
    assert stats["hits"] == 0
    assert stats["misses"] == 1
    assert stats["stores"] == 1


def test_ttl_zero_disables_cache(monkeypatch):
    monkeypatch.setenv("MCP_CACHE_TTL_SECONDS", "0")
    clear_mcp_cache()

    fake_make_remote, counter = _fake_remote_factory()
    with patch.object(mcp_tools_module, "_make_remote_mcp_tools", fake_make_remote):
        make_langchain_mcp_tools()
        make_langchain_mcp_tools()
        make_langchain_mcp_tools()

    assert counter["calls"] == 3, "TTL=0 must re-fetch every call"
    stats = get_mcp_cache_stats()
    assert stats["entries"] == 0
    assert stats["hits"] == 0
    assert stats["misses"] == 0


def test_expired_entry_triggers_refetch(monkeypatch):
    """Simulate TTL expiry by rewinding the stored timestamp."""
    monkeypatch.setenv("MCP_CACHE_TTL_SECONDS", "60")
    clear_mcp_cache()

    fake_make_remote, counter = _fake_remote_factory()
    with patch.object(mcp_tools_module, "_make_remote_mcp_tools", fake_make_remote):
        make_langchain_mcp_tools()
        assert counter["calls"] == 1

        # Forcibly age the single cache entry past the TTL.
        with mcp_tools_module._mcp_cache_lock:
            for key, (_stored_at, tools) in list(mcp_tools_module._mcp_tool_cache.items()):
                mcp_tools_module._mcp_tool_cache[key] = (0.0, tools)

        make_langchain_mcp_tools()

    assert counter["calls"] == 2, "Expired entries should trigger a refetch"
    stats = get_mcp_cache_stats()
    assert stats["misses"] == 2
    assert stats["hits"] == 0


def test_empty_remote_result_is_not_cached():
    """If the remote returns no tools, we should not cache the empty list.

    Local fallback is also empty here (every MCP tool module raises
    ImportError), so the function returns ``[]`` and nothing is stored.
    Next call should re-attempt the remote fetch instead of returning
    an empty cached list.
    """
    import importlib

    counter = {"calls": 0}

    def _empty_remote(url: str):
        counter["calls"] += 1
        return []

    # Narrowly intercept only the MCP tool module imports so langchain_core's
    # own lazy imports continue to work.
    real_import_module = importlib.import_module

    def _selective_import(name, package=None):
        if name.startswith("tools."):
            raise ImportError(f"no tools (stubbed) for {name}")
        return real_import_module(name, package)

    with patch.object(mcp_tools_module, "_make_remote_mcp_tools", _empty_remote):
        with patch.object(mcp_tools_module, "_ensure_mcp_import_path", lambda: None):
            with patch.object(mcp_tools_module, "_ensure_server_stub", lambda: None):
                with patch.object(mcp_tools_module.importlib, "import_module", side_effect=_selective_import):
                    make_langchain_mcp_tools()
                    make_langchain_mcp_tools()

    assert counter["calls"] == 2, "Empty remote results must not be cached"
    stats = get_mcp_cache_stats()
    assert stats["entries"] == 0
