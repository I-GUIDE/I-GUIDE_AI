"""LangChain tools for the vendored Telecoupling Toolbox (42 InVEST/telecoupling
models + 2 utilities).

Each tool in ``agent_runtime/telecoupling/tools_spec.json`` is wrapped as a
LangChain ``StructuredTool`` and exposed to the i-GUIDE **analysis agent** when
the ``include_telecoupling_tools`` toggle is on.

Design choices transplanted from the TelecouplingAI project:
  * **Schema-driven tools** — argument schemas come straight from the original
    Gemini ``FunctionDeclaration`` definitions (pydantic models built here).
  * **PRE_EXECUTION parameter guidance** from each SKILL.md is folded into the
    tool description so the agent collects the right inputs; the full
    PRE+POST workflow is also available as a loadable skill.
  * **Progress streaming** — the tool's ``progress_callback`` is bridged to the
    i-GUIDE trace stream via ``emit_trace_event``.
  * **Output routing & downloads** — tool outputs (classified qgis/csv/image/
    download by the vendored ``output_router``) are registered in the managed
    agent file store so the dashboard can offer ``download_url`` links.
  * **Error sanitization** — server paths are stripped from error text.

Heavy scientific dependencies (``natcap.invest``, R, GDAL, PyQGIS) are imported
lazily per tool, so this module loads with none of them installed; a tool whose
dependency is missing returns a structured "dependency not installed" message.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agent_runtime.streaming_trace import emit_trace_event

logger = logging.getLogger(__name__)

_SPEC_PATH = Path(__file__).resolve().parent / "telecoupling" / "tools_spec.json"

_JSON_TO_PY = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

# Heuristic: param keys that may carry a path / uploaded file reference.
_PATHISH_TOKENS = (
    "path", "dir", "table", "csv", "file", "raster", "shapefile",
    "aoi", "lulc", "dem", "html", "snapshot", "predictor",
)


@lru_cache(maxsize=1)
def load_spec() -> List[Dict[str, Any]]:
    """Load and cache the vendored tool spec (schemas + PRE guidance)."""
    try:
        return json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not load telecoupling tools spec at %s: %s", _SPEC_PATH, exc)
        return []


def telecoupling_tool_names() -> List[str]:
    """Names of every telecoupling tool (for tool-policy registration/tests)."""
    return [entry["name"] for entry in load_spec() if entry.get("name")]


def _safe_session_id(session_id: Optional[str]) -> str:
    value = (session_id or "telecoupling").strip() or "telecoupling"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:80]


def _python_type(json_type: Any) -> Any:
    if isinstance(json_type, list):
        non_null = [t for t in json_type if t != "null"]
        json_type = non_null[0] if non_null else "string"
    return _JSON_TO_PY.get(str(json_type).lower(), str)


def _build_args_schema(name: str, parameters: Dict[str, Any]) -> Any:
    """Build a pydantic args model from a JSON-schema-style ``parameters`` dict."""
    from pydantic import Field, create_model

    properties = (parameters or {}).get("properties") or {}
    required = set((parameters or {}).get("required") or [])
    fields: Dict[str, Any] = {}
    for prop_name, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        py_type = _python_type(spec.get("type", "string"))
        description = str(spec.get("description") or "")
        if prop_name in required:
            fields[prop_name] = (py_type, Field(..., description=description))
        else:
            fields[prop_name] = (Optional[py_type], Field(default=None, description=description))
    model_name = "TelecouplingArgs_" + re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if not fields:
        return create_model(model_name)
    return create_model(model_name, **fields)


def _compose_description(entry: Dict[str, Any]) -> str:
    desc = (entry.get("description") or "").strip()
    pre = (entry.get("pre_execution") or "").strip()
    parts = [desc] if desc else []
    if pre:
        pre_short = pre if len(pre) <= 1400 else pre[:1400].rstrip() + " ..."
        parts.append("[Telecoupling Toolbox] Parameter guidance:\n" + pre_short)
    parts.append(
        "Part of the Telecoupling Toolbox (vendored InVEST/telecoupling models). "
        "File-path arguments accept an uploaded file_id, a managed filename, or an "
        "absolute path. Outputs are returned as JSON with download_url links where available."
    )
    return "\n\n".join(parts)


def _maybe_resolve_path(key: str, value: Any) -> Any:
    """Best-effort: turn an uploaded file_id / managed filename into a real path.

    Never raises and never blocks a valid absolute path the user supplied —
    on any failure the original value is returned unchanged.
    """
    if not isinstance(value, str) or not value.strip():
        return value
    key_l = key.lower()
    if not (value.startswith("file_") or any(tok in key_l for tok in _PATHISH_TOKENS)):
        return value
    try:
        from agent_runtime.langchain_file_tools import _resolve_allowed_path
        resolved, _record = _resolve_allowed_path(value, must_exist=True)
        return str(resolved)
    except Exception:
        return value


def _finalize_files(files: Any) -> List[Dict[str, Any]]:
    """Register output files in the managed store so they get a download_url."""
    from agent_runtime.file_store import create_output_file_from_path

    finalized: List[Dict[str, Any]] = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        entry: Dict[str, Any] = {
            "filename": f.get("filename"),
            "render_type": f.get("render_type", "download"),
        }
        # Already registered (e.g. render_spatial_file via PyQGIS) — pass through.
        if f.get("download_url"):
            entry["download_url"] = f.get("download_url")
            entry["file_id"] = f.get("file_id")
            entry["path"] = f.get("path")
            finalized.append(entry)
            continue
        path = f.get("path")
        if path and os.path.isfile(path):
            try:
                record = create_output_file_from_path(path, filename=f.get("filename"))
                entry["file_id"] = record["file_id"]
                entry["download_url"] = record["download_url"]
                entry["size_bytes"] = record.get("size_bytes")
            except Exception as exc:  # pragma: no cover - registration best-effort
                entry["path"] = path
                entry["registration_error"] = str(exc)
        else:
            entry["path"] = path
        finalized.append(entry)
    return finalized


def _run_async(coro) -> Any:
    """Run *coro* to completion whether or not a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a running loop (rare for the sync agent path): isolate it.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def _build_tool(entry: Dict[str, Any], session_id: Optional[str]) -> Any:
    from langchain_core.tools import StructuredTool

    name = entry["name"]
    args_schema = _build_args_schema(name, entry.get("parameters") or {})
    description = _compose_description(entry)
    sid = _safe_session_id(session_id)

    def runner(**kwargs: Any) -> str:
        from agent_runtime.telecoupling.shared.utils import CSISError, sanitize_error_message
        from agent_runtime.telecoupling.tool_registry import resolve_tool

        # Drop unset optionals so the tool's own defaults apply; resolve paths.
        params = {
            key: _maybe_resolve_path(key, val)
            for key, val in kwargs.items()
            if val is not None
        }
        task_id = uuid4().hex

        def progress_callback(pct: int, message: str) -> None:
            try:
                emit_trace_event(
                    "telecoupling_progress",
                    {
                        "kind": "telecoupling_progress",
                        "label": f"{name}",
                        "message": f"{pct}% — {message}",
                        "tool_name": name,
                        "progress": pct,
                    },
                    agent_role="analysis_agent",
                )
            except Exception:
                pass

        try:
            func = resolve_tool(name)
        except ImportError as exc:
            return json.dumps(
                {
                    "status": "error",
                    "error_code": "DEPENDENCY_MISSING",
                    "tool": name,
                    "message": (
                        f"The '{name}' tool requires a scientific dependency that is not "
                        f"installed in this environment ({exc}). Install the Telecoupling "
                        "Toolbox extras (e.g. natcap.invest, geopandas/GDAL, R, or PyQGIS) "
                        "to run this model."
                    ),
                },
                ensure_ascii=True,
            )
        except KeyError:
            return json.dumps(
                {"status": "error", "error_code": "UNKNOWN_TOOL", "tool": name,
                 "message": f"Unknown telecoupling tool: {name}"},
                ensure_ascii=True,
            )

        try:
            result = _run_async(func(params, sid, task_id, progress_callback))
        except CSISError as exc:
            return json.dumps(
                {
                    "status": "error",
                    "error_code": getattr(exc, "error_code", "TOOL_FAILED"),
                    "tool": name,
                    "message": sanitize_error_message(getattr(exc, "message", str(exc))),
                    "details": getattr(exc, "details", {}) or {},
                },
                ensure_ascii=True,
                default=str,
            )
        except Exception as exc:
            logger.exception("Telecoupling tool %s failed", name)
            return json.dumps(
                {"status": "error", "error_code": "TOOL_FAILED", "tool": name,
                 "message": sanitize_error_message(str(exc))},
                ensure_ascii=True,
            )

        result = result if isinstance(result, dict) else {}
        payload: Dict[str, Any] = {
            "status": "ok",
            "tool": name,
            "files": _finalize_files(result.get("files")),
        }
        if result.get("content"):
            payload["content"] = result["content"]
        if result.get("warning"):
            payload["warning"] = sanitize_error_message(result["warning"])
        payload["message"] = f"{len(payload['files'])} output file(s) produced."
        return json.dumps(payload, ensure_ascii=True, default=str)

    runner.__name__ = name
    runner.__doc__ = description

    return StructuredTool.from_function(
        func=runner,
        name=name,
        description=description,
        args_schema=args_schema,
        infer_schema=False,
        metadata={"category": "telecoupling_toolbox", "toolbox": "telecoupling"},
    )


def make_langchain_telecoupling_tools(*, session_id: Optional[str] = None) -> List[Any]:
    """Build the full set of Telecoupling Toolbox LangChain tools.

    Importing/building these never requires the heavy scientific stack; each
    tool resolves its implementation lazily when first invoked.
    """
    try:
        from langchain_core.tools import StructuredTool  # noqa: F401
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    tools: List[Any] = []
    for entry in load_spec():
        if not entry.get("name"):
            continue
        try:
            tools.append(_build_tool(entry, session_id))
        except Exception as exc:
            logger.warning("Skipping telecoupling tool %s: %s", entry.get("name"), exc)
    logger.info("Built %s Telecoupling Toolbox tools", len(tools))
    return tools


__all__ = [
    "make_langchain_telecoupling_tools",
    "telecoupling_tool_names",
    "load_spec",
]
