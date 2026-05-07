from __future__ import annotations

import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import nbformat


def _ensure_repo_root_on_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


REPO_ROOT = _ensure_repo_root_on_path()

from services.file_store import get_file_record, resolve_file_id, storage_root


ENTRYPOINT_PRIORITY = ("run_workflow", "main", "run")
SCRIPT_OUTPUT_PRIORITY = ("result", "results", "output", "outputs", "summary", "df", "gdf", "fig", "figure")
RESERVED_TOOL_NAMES = {
    "create_notebook_workflow_tool",
    "list_generated_notebook_workflow_tools",
}


def _generated_root() -> Path:
    root = storage_root() / "generated_notebook_workflows"
    root.mkdir(parents=True, exist_ok=True)
    return root


def manifests_dir() -> Path:
    path = _generated_root() / "manifests"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sources_dir() -> Path:
    path = _generated_root() / "sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _allowed_roots() -> List[Path]:
    roots = {REPO_ROOT.resolve(), storage_root().resolve()}
    return sorted(roots)


def _resolve_notebook_ref(notebook_ref: str) -> tuple[Path, Optional[Dict[str, Any]]]:
    ref = str(notebook_ref or "").strip()
    if not ref:
        raise ValueError("notebook_ref is required")

    record = get_file_record(ref)
    if record:
        path = resolve_file_id(ref).resolve()
        return path, record

    candidate = Path(ref).expanduser()
    if not candidate.is_absolute():
        candidate = (REPO_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if not candidate.exists():
        raise ValueError(f"notebook file does not exist: {candidate}")

    allowed = _allowed_roots()
    if not any(candidate == root or root in candidate.parents for root in allowed):
        allowed_list = ", ".join(str(root) for root in allowed)
        raise ValueError(f"notebook path must be inside an allowed root: {allowed_list}")

    return candidate, None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_").lower()
    if not slug:
        slug = f"notebook_workflow_{uuid4().hex[:8]}"
    if slug[0].isdigit():
        slug = f"workflow_{slug}"
    return slug


def _tool_name_from_path(notebook_path: Path) -> str:
    base = _slugify(notebook_path.stem)
    return f"notebook_{base}"


def _sanitize_line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("%%") or stripped.startswith("%") or stripped.startswith("!"):
        return f"# notebook-magic removed: {line}"
    if stripped.startswith("?"):
        return f"# help-command removed: {line}"
    return line


def _extract_code_cells(notebook_path: Path) -> List[Dict[str, Any]]:
    notebook = nbformat.read(notebook_path, as_version=4)
    cells: List[Dict[str, Any]] = []
    for idx, cell in enumerate(notebook.cells):
        if cell.cell_type != "code":
            continue
        source = str(cell.source or "")
        if not source.strip():
            continue
        sanitized = "\n".join(_sanitize_line(line) for line in source.splitlines())
        cells.append(
            {
                "index": idx,
                "execution_count": getattr(cell, "execution_count", None),
                "source": sanitized.rstrip(),
            }
        )
    if not cells:
        raise ValueError("notebook does not contain any non-empty code cells")
    return cells


def _build_module_source(notebook_path: Path, cells: List[Dict[str, Any]]) -> str:
    lines = [
        '"""Generated notebook workflow source."""',
        "",
        f"# Source notebook: {notebook_path.name}",
        "",
    ]
    for cell in cells:
        lines.append(f"# Cell {cell['index']}")
        lines.append(cell["source"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _top_level_function_names(tree: ast.AST) -> List[str]:
    names: List[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
    return names


def _find_entrypoint(function_names: List[str]) -> Optional[str]:
    for name in ENTRYPOINT_PRIORITY:
        if name in function_names:
            return name
    for name in function_names:
        if not name.startswith("_"):
            return name
    return None


def _entrypoint_params(tree: ast.AST, entrypoint: Optional[str]) -> List[str]:
    if not entrypoint:
        return []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == entrypoint:
            return [arg.arg for arg in node.args.args]
    return []


def _script_output_candidates(tree: ast.AST) -> List[str]:
    seen: List[str] = []
    priority = list(SCRIPT_OUTPUT_PRIORITY)
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in seen:
                    seen.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id not in seen:
                seen.append(node.target.id)
    ordered = [name for name in priority if name in seen]
    extras = [name for name in seen if name not in ordered]
    return (ordered + extras)[:8]


def _manifest_path(tool_name: str) -> Path:
    return manifests_dir() / f"{tool_name}.json"


def list_manifest_paths() -> List[Path]:
    return sorted(manifests_dir().glob("*.json"))


def build_notebook_workflow_artifacts(
    notebook_ref: str,
    *,
    tool_name: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    notebook_path, record = _resolve_notebook_ref(notebook_ref)
    if notebook_path.suffix.lower() != ".ipynb":
        raise ValueError("notebook_ref must point to a .ipynb file")

    resolved_tool_name = _slugify(tool_name or _tool_name_from_path(notebook_path))
    if resolved_tool_name in RESERVED_TOOL_NAMES:
        raise ValueError(f"tool_name is reserved: {resolved_tool_name}")

    manifest_path = _manifest_path(resolved_tool_name)
    if manifest_path.exists() and not overwrite:
        raise ValueError(
            f"a generated notebook tool named '{resolved_tool_name}' already exists; pass overwrite=True to replace it"
        )

    cells = _extract_code_cells(notebook_path)
    module_source = _build_module_source(notebook_path, cells)
    tree = ast.parse(module_source)
    function_names = _top_level_function_names(tree)
    entrypoint = _find_entrypoint(function_names)
    mode = "function" if entrypoint else "script"
    source_path = sources_dir() / f"{resolved_tool_name}.py"
    source_path.write_text(module_source, encoding="utf-8")

    manifest: Dict[str, Any] = {
        "workflow_id": f"nbwf_{uuid4().hex[:12]}",
        "tool_name": resolved_tool_name,
        "notebook_path": str(notebook_path),
        "notebook_file_id": (record or {}).get("file_id"),
        "source_path": str(source_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cell_count": len(cells),
        "mode": mode,
        "entrypoint": entrypoint,
        "entrypoint_parameters": _entrypoint_params(tree, entrypoint),
        "script_output_candidates": _script_output_candidates(tree),
        "top_level_functions": function_names,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest

