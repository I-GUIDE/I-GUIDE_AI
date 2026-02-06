"""
Adapter to expose MCP tools in smolagents format.

Usage:
    from MCP_server.smolagents_adapter import get_smolagents_tools
    tools = get_smolagents_tools()
    agent = CodeAgent(tools=tools, model=model)
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import sys
from types import ModuleType
from typing import Callable, Iterable, List


def _load_tools_modules(tools_dir: str) -> Iterable[ModuleType]:
    for filename in os.listdir(tools_dir):
        if not filename.endswith(".py") or filename.startswith("__"):
            continue
        module_path = os.path.join(tools_dir, filename)
        spec = importlib.util.spec_from_file_location(filename[:-3], module_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


def _iter_mcp_tools(tools_dir: str) -> Iterable[Callable]:
    for module in _load_tools_modules(tools_dir):
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if callable(attr) and getattr(attr, "_is_mcp_tool", False):
                yield attr


def get_smolagents_tools(tools_dir: str | None = None) -> List[Callable]:
    """
    Returns a list of smolagents-compatible tool callables discovered from MCP tools.
    """
    try:
        from smolagents import tool as smol_tool
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise ImportError(
            "smolagents is not installed. Install it to use the adapter."
        ) from exc

    base_dir = os.path.dirname(__file__)
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    if tools_dir is None:
        tools_dir = os.path.join(base_dir, "tools")

    wrapped = []
    for func in _iter_mcp_tools(tools_dir):
        # Preserve name/docstring for smolagents metadata
        func.__signature__ = inspect.signature(func)
        wrapped.append(smol_tool(func))
    return wrapped


__all__ = ["get_smolagents_tools"]
