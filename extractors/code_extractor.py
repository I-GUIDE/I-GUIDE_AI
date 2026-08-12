"""Code-asset extractor (#4) — GitHub path.

Per .py file: AST-extract the API surface (top-level functions, classes, and their
methods) as CodeAsset blocks (signature + docstring + file path + module imports),
element_id-anchored. Detect entry points (a top-level ``main``/``run``, an
``if __name__ == '__main__'`` block, or argparse usage) and promote them to runnable
code manifests (consumed by the generic executor). Emits ``DEFINES`` provenance edges
(element → asset). Index-only for non-entry-point assets.

Reuse: doc_ids (element-anchored ids, workflow_id_for(code=True), mcp_tool_name_for),
the form ``fields`` inherited into source_fields (mirrors NotebookExtractor).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    EMIT_MCP,
    EMIT_OPENSEARCH,
    KIND_CODE_BLOCK,
    AssetRecord,
    ExtractContext,
    Extractor,
    ExtractionResult,
    ProvenanceEdge,
)
from .doc_ids import (
    code_asset_doc_id,
    mcp_tool_name_for,
    resource_type_for,
    workflow_id_for,
)


def _rel_path(ctx: ExtractContext, path: str) -> str:
    repo_dir = ctx.extra.get("repo_dir")
    if repo_dir:
        try:
            return os.path.relpath(path, repo_dir)
        except ValueError:
            pass
    return os.path.basename(path)


def _inherited(ctx: ExtractContext) -> Tuple[List[str], Dict[str, Any]]:
    f = ctx.fields or {}
    tags = f.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    source_fields = {k: f[k] for k in ("authors", "contributor", "abstract", "description", "license", "doi")
                     if f.get(k)}
    return list(tags), source_fields


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = [a.arg for a in node.args.args]
        if node.args.vararg:
            args.append("*" + node.args.vararg.arg)
        if node.args.kwarg:
            args.append("**" + node.args.kwarg.arg)
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(args)})"
    if isinstance(node, ast.ClassDef):
        bases = [getattr(b, "id", getattr(b, "attr", "")) for b in node.bases]
        return f"class {node.name}({', '.join(b for b in bases if b)})" if bases else f"class {node.name}"
    return ""


def _module_imports(tree: ast.AST) -> List[str]:
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module.split(".")[0])
    return sorted(set(mods))


def _func_params(node: ast.AST) -> List[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [a.arg for a in node.args.args]
    return []


def _api_surface(tree: ast.AST) -> List[Tuple[str, str, ast.AST]]:
    """Return (qualified_name, kind, node) for top-level funcs/classes + methods."""
    out: List[Tuple[str, str, ast.AST]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, "function", node))
        elif isinstance(node, ast.ClassDef):
            out.append((node.name, "class", node))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("_"):
                    out.append((f"{node.name}.{sub.name}", "method", sub))
    return out


def _entry_point(tree: ast.AST) -> Tuple[bool, Optional[str], List[str]]:
    """(has_entry_point, entrypoint_func_or_None, params). Function mode if a
    top-level main/run exists, else script mode if a __main__ block / argparse."""
    top_funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in ("run_workflow", "main", "run"):
        if name in top_funcs:
            return True, name, _func_params(top_funcs[name])
    has_main_block = any(
        isinstance(n, ast.If) and "__main__" in ast.dump(n.test) for n in tree.body
    )
    uses_argparse = any(
        isinstance(n, (ast.Import, ast.ImportFrom)) and "argparse" in ast.dump(n)
        for n in ast.walk(tree)
    )
    if has_main_block or uses_argparse:
        return True, None, []   # script mode
    return False, None, []


class CodeExtractor:
    name = "code"

    def extract(self, path: str, *, ctx: ExtractContext) -> ExtractionResult:
        rel_path = _rel_path(ctx, path)
        anchor = ctx.anchor() or "repo"
        tags, source_fields = _inherited(ctx)
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError) as exc:
            return ExtractionResult(warnings=[f"code: skipped {rel_path}: {type(exc).__name__}: {exc}"])

        imports = _module_imports(tree)
        assets: List[AssetRecord] = []
        edges: List[ProvenanceEdge] = []

        for qualname, kind, node in _api_surface(tree):
            doc_id = code_asset_doc_id(anchor, rel_path, qualname)
            sig = _signature(node)
            doc = ast.get_docstring(node) or ""
            contents = f"{sig}\n{doc}\n# file: {rel_path}".strip()
            assets.append(AssetRecord(
                asset_id=doc_id,
                kind=KIND_CODE_BLOCK,
                resource_type=resource_type_for(KIND_CODE_BLOCK),
                doc_id=doc_id,
                emit_targets=[EMIT_OPENSEARCH],
                source_rel_path=rel_path,
                title=f"{qualname}  ({rel_path})",
                contents=contents,
                source_fields={**source_fields, "tags": tags},
                block={
                    "qualified_name": qualname, "kind": kind, "signature": sig,
                    "docstring": doc, "file_path": rel_path, "imports": imports,
                    "params": _func_params(node),
                },
                extracted={"parent_doc_id": anchor, "parent_type": "Code",
                           "parent_title": str((ctx.fields or {}).get("title") or "")},
            ))
            edges.append(ProvenanceEdge(src=anchor, rel="DEFINES", dst=doc_id, detail={"kind": kind}))

        # promote a per-file runnable entry point
        has_ep, entrypoint, params = _entry_point(tree)
        if has_ep:
            asset_id = f"{anchor}::codewf::{rel_path}"
            wid = workflow_id_for(asset_id, code=True)
            runnable_tool = mcp_tool_name_for(wid)
            assets.append(AssetRecord(
                asset_id=asset_id,
                kind=KIND_CODE_BLOCK,
                resource_type=resource_type_for(KIND_CODE_BLOCK),
                doc_id=asset_id,
                emit_targets=([EMIT_OPENSEARCH, EMIT_MCP] if EMIT_MCP in ctx.targets else [EMIT_OPENSEARCH]),
                source_rel_path=rel_path,
                title=f"{rel_path}  (runnable)",
                # Identify, do not advertise a callable tool — see
                # doc_ids.mcp_tool_name_for and notebook_extractor's matching marker.
                contents=f"[workflow {wid}] Entry point in {rel_path} "
                         f"({'function:' + entrypoint if entrypoint else 'script'} mode). "
                         f"Not directly callable; reuse the extracted functions.",
                source_fields={**source_fields, "tags": tags},
                runnable={
                    "workflow_id": wid, "runnable_tool": runnable_tool,
                    "mode": "function" if entrypoint else "script",
                    "entrypoint": entrypoint, "entrypoint_parameters": params,
                    "module_source": source,
                },
                extracted={"parent_doc_id": anchor, "parent_type": "Code", "runnable_tool": runnable_tool},
            ))
            edges.append(ProvenanceEdge(src=asset_id, rel="HAS_WORKFLOW", dst=wid,
                                        detail={"mcp_tool": runnable_tool}))

        return ExtractionResult(assets=assets, edges=edges, skill=None)


_: Extractor = CodeExtractor()  # type: ignore[assignment]

__all__ = ["CodeExtractor"]
