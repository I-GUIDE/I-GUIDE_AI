"""MCP emitter — persist runnable units as manifests for the generic executor.

For each asset with target ``mcp`` and a ``runnable`` sub-block, write a source file
+ a manifest JSON under ``storage_root()/generated_notebook_workflows/{sources,manifests}``
(the same layout MCP_server/notebook_workflow_builder uses, so
``generated_notebook_tools._run_generated_manifest`` consumes it unchanged). NO
per-tool registration — the single generic executor
(``MCP_server/tools/generic_executor_tools.py`` → exposed via the MCP+REST adapter
``server.register_tool_with_mcp`` at ``/api/tool/run_notebook_workflow``) resolves a
manifest by ``workflow_id`` at call time.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..manifest import UnifiedManifest

_OUTPUT_PRIORITY = ("result", "results", "output", "outputs", "summary", "df", "gdf", "fig", "figure")


def _storage_root() -> Path:
    from agent_runtime.file_store import storage_root  # lightweight, repo-root importable
    return Path(storage_root())


def _generated_root() -> Path:
    root = _storage_root() / "generated_notebook_workflows"
    root.mkdir(parents=True, exist_ok=True)
    return root


def workflow_manifests_dir() -> Path:
    p = _generated_root() / "manifests"
    p.mkdir(parents=True, exist_ok=True)
    return p


def workflow_sources_dir() -> Path:
    p = _generated_root() / "sources"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _script_output_candidates(module_source: str) -> List[str]:
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return []
    seen: List[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id not in seen:
                    seen.append(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id not in seen:
                seen.append(node.target.id)
    ordered = [n for n in _OUTPUT_PRIORITY if n in seen]
    extras = [n for n in seen if n not in ordered]
    return (ordered + extras)[:8]


def _runnable_assets(manifest: UnifiedManifest) -> List[Dict[str, Any]]:
    d = manifest.to_dict() if isinstance(manifest, UnifiedManifest) else dict(manifest)
    out = []
    for a in d.get("assets") or []:
        if "mcp" in (a.get("emit_targets") or []) and a.get("runnable"):
            out.append(a)
    return out


def emit(manifest: UnifiedManifest, *, dry_run: bool = False) -> Dict[str, Any]:
    assets = _runnable_assets(manifest)
    if dry_run:
        return {"dry_run": True, "workflow_ids": [a["runnable"].get("workflow_id") for a in assets]}

    sources = workflow_sources_dir()
    manifests = workflow_manifests_dir()
    written: List[str] = []
    for a in assets:
        r = a["runnable"]
        wid = r["workflow_id"]
        module_source = r.get("module_source") or ""
        source_path = (sources / f"{wid}.py")
        source_path.write_text(module_source, encoding="utf-8")
        record = {
            "workflow_id": wid,
            "tool_name": wid,                       # _run_generated_manifest echoes this
            "runnable_tool": r.get("runnable_tool"),
            "source_path": str(source_path.resolve()),
            "mode": r.get("mode") or "script",
            "entrypoint": r.get("entrypoint"),
            "entrypoint_parameters": r.get("entrypoint_parameters") or [],
            "script_output_candidates": _script_output_candidates(module_source),
            "element_id": (manifest.element_id if isinstance(manifest, UnifiedManifest) else None),
            "element_type": (manifest.element_type if isinstance(manifest, UnifiedManifest) else None),
            "notebook_path": a.get("source_rel_path"),
            "doc_id": a.get("doc_id"),
        }
        (manifests / f"{wid}.json").write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")
        written.append(wid)
    return {"dry_run": False, "written": written,
            "manifests_dir": str(manifests), "sources_dir": str(sources)}


__all__ = ["emit", "workflow_manifests_dir", "workflow_sources_dir"]
