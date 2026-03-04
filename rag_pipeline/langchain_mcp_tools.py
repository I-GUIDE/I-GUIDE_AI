from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_MCP_MODULES = (
    "search_tools",
    "data_tools",
    "spatial_analysis_tools",
    "biomass_tools",
    "image_tools",
)


def _mcp_tool(
    _func=None,
    *,
    summary: str | None = None,
    description: str | None = None,
    tool_description: str | None = None,
    mcp_description: str | None = None,
):
    def decorator(func):
        func._is_mcp_tool = True
        if summary:
            func._mcp_summary = summary
            func._tool_summary = summary
        if description:
            func._tool_description = description
            func._mcp_description = description
        if tool_description:
            func._tool_description = tool_description
        if mcp_description:
            func._mcp_description = mcp_description
        return func

    if callable(_func):
        return decorator(_func)
    return decorator


def _ensure_mcp_import_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    mcp_dir = repo_root / "MCP_server"
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    return mcp_dir


def _ensure_server_stub() -> None:
    existing = sys.modules.get("server")
    if existing is not None and hasattr(existing, "mcp_tool"):
        return
    stub = ModuleType("server")
    stub.mcp_tool = _mcp_tool  # type: ignore[attr-defined]
    sys.modules["server"] = stub


def _ensure_fastapi_stub() -> None:
    existing = sys.modules.get("fastapi")
    if existing is not None:
        return

    stub = ModuleType("fastapi")

    class UploadFile:  # pragma: no cover - simple import shim
        pass

    def File(default: Any = None):  # pragma: no cover - simple import shim
        return default

    stub.UploadFile = UploadFile  # type: ignore[attr-defined]
    stub.File = File  # type: ignore[attr-defined]
    sys.modules["fastapi"] = stub


def _iter_mcp_functions(module: Any) -> Iterable[Callable[..., Any]]:
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if callable(attr) and getattr(attr, "_is_mcp_tool", False):
            yield attr


def _tool_description(func: Callable[..., Any]) -> str:
    return (
        getattr(func, "_mcp_description", None)
        or getattr(func, "_tool_description", None)
        or getattr(func, "__doc__", None)
        or f"MCP tool: {func.__name__}"
    ).strip()


def _make_image_tools(module: Any) -> List[Callable[..., Any]]:
    api_url = getattr(
        module,
        "API_URL",
        os.getenv("VISION_API_URL", "http://149.165.153.129:8000/v1/chat/completions"),
    )
    api_key = getattr(module, "API_KEY", os.getenv("VISION_API_KEY"))
    model_name = getattr(
        module,
        "MODEL_NAME",
        os.getenv("VISION_API_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct"),
    )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def describe_image_b64(image_base64: str, prompt_text: str = "Describe what is in this image.") -> str:
        """
        Describe an image from base64-encoded bytes using the configured vision model.
        """
        body = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    ],
                }
            ],
            "stream": False,
        }
        response = requests.post(api_url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        data = response.json() or {}
        choices = data.get("choices") or []
        if not choices:
            return "No description generated."
        return (choices[0].get("message") or {}).get("content") or "No description generated."

    def describe_map_b64(
        image_base64: str,
        prompt_text: str = (
            "Describe the given map. Focus on which area the map depicts, "
            "what problem the map describes, and what information the map provides. "
            "Format the response in markdown format."
        ),
    ) -> str:
        """
        Describe a map image from base64-encoded bytes with area/problem/information focus.
        """
        body = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    ],
                }
            ],
            "stream": False,
        }
        response = requests.post(api_url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        data = response.json() or {}
        choices = data.get("choices") or []
        if not choices:
            return "No description generated."
        return (choices[0].get("message") or {}).get("content") or "No description generated."

    describe_image_b64.__name__ = "describe_image_b64"
    describe_map_b64.__name__ = "describe_map_b64"
    return [describe_image_b64, describe_map_b64]


def make_langchain_mcp_tools(
    *,
    include_modules: Optional[List[str]] = None,
) -> List[Any]:
    """
    Build LangChain tools from selected MCP tool modules under MCP_server/tools.
    """
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    _ensure_mcp_import_path()
    _ensure_server_stub()

    modules = include_modules or list(DEFAULT_MCP_MODULES)
    tools: List[Any] = []

    for module_name in modules:
        if module_name == "image_tools":
            _ensure_fastapi_stub()
        try:
            module = importlib.import_module(f"tools.{module_name}")
        except Exception as exc:
            logger.warning("Skipping MCP module %s: %s", module_name, exc)
            continue

        if module_name == "image_tools":
            candidates: List[Callable[..., Any]] = _make_image_tools(module)
        else:
            candidates = list(_iter_mcp_functions(module))

        for func in candidates:
            tool_name = f"mcp_{func.__name__}"
            try:
                tools.append(
                    StructuredTool.from_function(
                        func=func,
                        name=tool_name,
                        description=_tool_description(func),
                    )
                )
            except Exception as exc:
                logger.warning("Skipping MCP tool %s from %s: %s", tool_name, module_name, exc)
                continue

    return tools


__all__ = ["make_langchain_mcp_tools", "DEFAULT_MCP_MODULES"]
