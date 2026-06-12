"""Notebook extractor (#1) — GitHub path.

Per .ipynb: split cells, IPython-transform code cells (NON-lossy via the R1 front
end), classify constructs, and emit:
  - one NotebookBlock AssetRecord per code cell -> OpenSearch (code + adjacent
    markdown, resolved_tools, file_io, imports);
  - one whole-notebook runnable descriptor (deterministic workflow_id + runnable_tool,
    function|script mode, entrypoint/params, module_source) when all code cells parse;
  - INCLUDES (notebook->block) + HAS_WORKFLOW (workflow->blocks) provenance edges;
  - the ordered pipeline -> a SkillSpec.

Reuse: extractors.r1_ipython_frontend (transform_cell / classify_line / _ast_extra /
_resolve_tool) replaces the lossy notebook_workflow_builder._sanitize_line. Entry-point
+ module-source logic is inlined (small) to keep the extractor self-contained.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import nbformat

from .base import (
    EMIT_MCP,
    EMIT_OPENSEARCH,
    EMIT_SKILL,
    KIND_NOTEBOOK_BLOCK,
    AssetRecord,
    ExtractContext,
    Extractor,
    ExtractionResult,
    ProvenanceEdge,
    SkillSpec,
)
from .doc_ids import (
    mcp_tool_name_for,
    notebook_block_doc_id,
    resource_type_for,
    slugify,
    workflow_id_for,
)
from .fileclass import RASTER_EXT, TABULAR_EXT, VECTOR_EXT
from .r1_ipython_frontend import _ast_extra, classify_line, transform_cell

_ENTRYPOINT_PRIORITY = ("run_workflow", "main", "run")
_DATA_EXT = RASTER_EXT | VECTOR_EXT | TABULAR_EXT
_FILE_TOKEN_RE = re.compile(r"[\w./\-]+\.(?:" + "|".join(e.lstrip(".") for e in _DATA_EXT) + r")\b")


def _notebook_doc_id(ctx: ExtractContext, rel_path: str) -> str:
    # The notebook IS the knowledge element -> anchor on the platform element_id.
    if ctx.element_id:
        return ctx.element_id
    base = ctx.repo_id or "nb"
    stem = slugify(Path(rel_path).stem)
    return f"{base}::notebook::{stem}"


def _inherited(ctx: ExtractContext) -> tuple[str, list, dict]:
    """Return (title, form_tags, source_fields) inherited from the submission form."""
    f = ctx.fields or {}
    title = str(f.get("title") or "")
    form_tags = f.get("tags") or []
    if isinstance(form_tags, str):
        form_tags = [t.strip() for t in form_tags.split(",") if t.strip()]
    source_fields = {k: f[k] for k in ("authors", "contributor", "abstract", "description", "license", "doi")
                     if f.get(k)}
    return title, list(form_tags), source_fields


def _file_refs(text: str) -> List[str]:
    """Best-effort data-file references in a command or code string (referenced, not
    yet split into reads/writes — that is a follow-on refinement)."""
    return sorted({m.group(0) for m in _FILE_TOKEN_RE.finditer(text or "")})


def _classify_cell(source: str) -> Tuple[str, bool, List[Dict[str, Any]], List[str], List[str], List[str]]:
    """Return (transformed, parse_ok, constructs, resolved_tools, imports, file_refs)."""
    transformed, parse_ok, _note = transform_cell(source)
    constructs: List[Dict[str, Any]] = []
    tools: List[str] = []
    file_refs: List[str] = []
    for line in source.splitlines():
        res = classify_line(line)
        if not res:
            continue
        cat, detail = res
        constructs.append({"category": cat, "detail": detail})
        if detail.get("tool"):
            tools.append(detail["tool"])
        if detail.get("wrapper"):
            tools.append(detail["wrapper"])
        if detail.get("command"):
            file_refs.extend(_file_refs(detail["command"]))
    imports: List[str] = []
    for c in _ast_extra(transformed, 0):
        if c.category == "IMPORT" and c.detail.get("module"):
            imports.append(c.detail["module"])
        if c.category == "CLI_STEP" and c.detail.get("tool"):
            tools.append(c.detail["tool"])
    file_refs.extend(_file_refs(source))
    return (transformed, parse_ok,
            constructs, sorted(set(tools)), sorted(set(imports)), sorted(set(file_refs)))


def _top_level_functions(module_source: str) -> List[str]:
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return []
    return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _find_entrypoint(fns: List[str]) -> Optional[str]:
    for name in _ENTRYPOINT_PRIORITY:
        if name in fns:
            return name
    for name in fns:
        if not name.startswith("_"):
            return name
    return None


def _entrypoint_params(module_source: str, entrypoint: Optional[str]) -> List[str]:
    if not entrypoint:
        return []
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == entrypoint:
            return [a.arg for a in n.args.args]
    return []


class NotebookExtractor:
    name = "notebook"

    def extract(self, path: str, *, ctx: ExtractContext) -> ExtractionResult:
        nb = nbformat.read(path, as_version=4)
        rel_path = os.path.relpath(path, ctx.extra.get("repo_dir", os.path.dirname(path) or ".")) \
            if ctx.extra.get("repo_dir") else os.path.basename(path)
        nb_doc_id = _notebook_doc_id(ctx, rel_path)
        title_base = Path(rel_path).stem
        form_title, form_tags, source_fields = _inherited(ctx)
        title = form_title or title_base

        assets: List[AssetRecord] = []
        edges: List[ProvenanceEdge] = []
        ordered_steps: List[Dict[str, Any]] = []
        transformed_code: List[Tuple[int, str]] = []
        all_parse_ok = True
        md_buffer: List[str] = []
        tags: set[str] = set()

        for order, cell in enumerate(nb.cells):
            if cell.cell_type == "markdown":
                md_buffer.append(str(cell.source or ""))
                continue
            if cell.cell_type != "code" or not str(cell.source or "").strip():
                continue
            source = str(cell.source)
            md_context = "\n".join(md_buffer).strip()
            md_buffer = []

            transformed, parse_ok, constructs, tools, imports, file_refs = _classify_cell(source)
            all_parse_ok = all_parse_ok and parse_ok
            transformed_code.append((order, transformed))
            tags.update(imports)

            doc_id = notebook_block_doc_id(nb_doc_id, order)
            contents = (f"{md_context}\n\n{source}" if md_context else source).strip()
            assets.append(AssetRecord(
                asset_id=doc_id,
                kind=KIND_NOTEBOOK_BLOCK,
                resource_type=resource_type_for(KIND_NOTEBOOK_BLOCK),
                doc_id=doc_id,
                emit_targets=[EMIT_OPENSEARCH],
                source_rel_path=rel_path,
                title=f"{title} — cell {order}",
                contents=contents,
                source_fields={**source_fields, "tags": sorted(set(form_tags) | set(imports))},
                block={
                    "code": source,
                    "transformed": transformed,
                    "markdown_context": md_context,
                    "constructs": constructs,
                    "resolved_tools": tools,
                    "imports": imports,
                    "file_io": {"referenced": file_refs},
                    "parse_ok": parse_ok,
                    "order": order,
                },
                extracted={"parent_doc_id": nb_doc_id, "parent_type": "Notebook",
                           "parent_title": title, "order": order},
            ))
            edges.append(ProvenanceEdge(src=nb_doc_id, rel="INCLUDES", dst=doc_id, detail={"order": order}))
            ordered_steps.append({"order": order, "tools": tools, "summary": (md_context or source.splitlines()[0])[:120]})

        # whole-notebook runnable descriptor (promotion gate: every code cell parsed)
        if assets and all_parse_ok:
            module_source = self._build_module_source(title_base, transformed_code)
            fns = _top_level_functions(module_source)
            entrypoint = _find_entrypoint(fns)
            mode = "function" if entrypoint else "script"
            wid = workflow_id_for(nb_doc_id)
            runnable_tool = mcp_tool_name_for(wid)
            wf_doc_id = f"{nb_doc_id}::workflow"
            runnable = {
                "workflow_id": wid,
                "runnable_tool": runnable_tool,
                "mode": mode,
                "entrypoint": entrypoint,
                "entrypoint_parameters": _entrypoint_params(module_source, entrypoint),
                "module_source": module_source,
            }
            assets.append(AssetRecord(
                asset_id=wf_doc_id,
                kind=KIND_NOTEBOOK_BLOCK,
                resource_type=resource_type_for(KIND_NOTEBOOK_BLOCK),
                doc_id=wf_doc_id,
                emit_targets=([EMIT_OPENSEARCH, EMIT_MCP, EMIT_SKILL] if EMIT_MCP in ctx.targets else [EMIT_OPENSEARCH]),
                source_rel_path=rel_path,
                title=f"{title} — workflow",
                # prepend the run pointer so the code peer sees it via _format_documents
                contents=f"[runnable: {runnable_tool}] Workflow extracted from {rel_path} ({mode} mode).",
                runnable=runnable,
                source_fields={**source_fields, "tags": sorted(set(form_tags) | tags)},
                extracted={"parent_doc_id": nb_doc_id, "parent_type": "Notebook", "runnable_tool": runnable_tool},
            ))
            edges.append(ProvenanceEdge(src=wf_doc_id, rel="HAS_WORKFLOW", dst=wid,
                                        detail={"mcp_tool": runnable_tool, "mode": mode}))
            skill = SkillSpec(
                name=slugify(title),
                description=str(ctx.fields.get("abstract") or ctx.fields.get("description")
                               or f"Run the {title} workflow extracted from {rel_path}."),
                allowed_tools=[runnable_tool],
                tags=sorted(set(form_tags) | tags),
                ordered_steps=ordered_steps,
            )
        else:
            skill = None

        warnings: List[str] = []
        if assets and not all_parse_ok:
            warnings.append("not all code cells parsed; workflow not promoted (index-only blocks).")
        return ExtractionResult(assets=assets, edges=edges, skill=skill, warnings=warnings)

    @staticmethod
    def _build_module_source(title: str, cells: List[Tuple[int, str]]) -> str:
        lines = [f'"""Generated workflow source from notebook: {title}."""', ""]
        for order, code in cells:
            lines.append(f"# Cell {order}")
            lines.append(code)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


_: Extractor = NotebookExtractor()  # type: ignore[assignment]

__all__ = ["NotebookExtractor"]
