from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .file_store import create_output_file, get_file_record, resolve_file_id

DEFAULT_MAX_CHARS = 12000
DEFAULT_MAX_ROWS = 20


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _allowed_roots() -> List[Path]:
    roots = {_repo_root().resolve()}
    upload_folder = os.getenv("UPLOAD_FOLDER")
    if upload_folder:
        roots.add(Path(upload_folder).expanduser().resolve())
    return sorted(roots)


def _resolve_local_allowed_path(path: str, *, must_exist: bool = True) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (_repo_root() / candidate).resolve()
    else:
        candidate = candidate.resolve()

    allowed = _allowed_roots()
    if not any(candidate == root or root in candidate.parents for root in allowed):
        allowed_list = ", ".join(str(root) for root in allowed)
        raise ValueError(f"path must be inside an allowed root: {allowed_list}")

    if must_exist and not candidate.exists():
        raise ValueError(f"file does not exist: {candidate}")

    return candidate


def _resolve_allowed_path(path: str, *, must_exist: bool = True) -> Tuple[Path, Optional[Dict[str, Any]]]:
    ref = str(path or "").strip()
    if not ref:
        raise ValueError("path is required")

    record = get_file_record(ref)
    if record:
        return resolve_file_id(ref), record

    return _resolve_local_allowed_path(ref, must_exist=must_exist), None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _truncate(text: str, max_chars: int) -> str:
    max_chars = max(256, int(max_chars or DEFAULT_MAX_CHARS))
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[truncated {len(text) - max_chars} characters]"


def _csv_preview(text: str, max_rows: int) -> Dict[str, Any]:
    reader = csv.reader(io.StringIO(text))
    rows: List[List[str]] = []
    for idx, row in enumerate(reader):
        if idx >= max(1, int(max_rows or DEFAULT_MAX_ROWS)):
            break
        rows.append([str(item) for item in row])
    header = rows[0] if rows else []
    return {"format": "csv", "header": header, "rows": rows}


def _json_preview(text: str) -> Dict[str, Any]:
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        keys = list(parsed.keys())[:50]
        preview = {key: parsed[key] for key in keys[:10]}
        return {"format": "json", "top_level_type": "object", "keys": keys, "preview": preview}
    if isinstance(parsed, list):
        preview = parsed[:5]
        return {"format": "json", "top_level_type": "array", "length": len(parsed), "preview": preview}
    return {"format": "json", "top_level_type": type(parsed).__name__, "preview": parsed}


def read_text_file_tool(path: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    resolved, record = _resolve_allowed_path(path, must_exist=True)
    if not resolved.is_file():
        raise ValueError(f"path is not a file: {resolved}")

    text = _read_text(resolved)
    payload = {
        "file_id": (record or {}).get("file_id"),
        "filename": (record or {}).get("filename") or resolved.name,
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "content": _truncate(text, max_chars),
    }
    if record:
        payload["download_url"] = record.get("download_url")
    return json.dumps(payload, ensure_ascii=True, default=str)


def inspect_file_for_analysis_tool(
    path: str,
    question: Optional[str] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> str:
    resolved, record = _resolve_allowed_path(path, must_exist=True)
    if not resolved.is_file():
        raise ValueError(f"path is not a file: {resolved}")

    suffix = resolved.suffix.lower()
    text = _read_text(resolved)
    payload: Dict[str, Any] = {
        "file_id": (record or {}).get("file_id"),
        "path": str(resolved),
        "filename": (record or {}).get("filename") or resolved.name,
        "question": question or "",
        "size_bytes": resolved.stat().st_size,
    }
    if record:
        payload["download_url"] = record.get("download_url")

    try:
        if suffix == ".csv":
            payload["analysis_ready_content"] = _csv_preview(text, max_rows=max_rows)
        elif suffix == ".json":
            payload["analysis_ready_content"] = _json_preview(text)
        else:
            payload["analysis_ready_content"] = {"format": "text", "content": _truncate(text, max_chars)}
    except Exception:
        payload["analysis_ready_content"] = {"format": "text", "content": _truncate(text, max_chars)}

    return json.dumps(payload, ensure_ascii=True, default=str)


def write_text_file_tool(path: str, content: str, overwrite: bool = False) -> str:
    if path and not any(sep in str(path) for sep in ("/", "\\")) and not str(path).startswith("."):
        record = create_output_file(str(path), content or "", overwrite=overwrite)
        payload = {
            "file_id": record["file_id"],
            "filename": record["filename"],
            "path": record["path"],
            "size_bytes": record["size_bytes"],
            "download_url": record["download_url"],
            "overwritten": bool(overwrite),
        }
        return json.dumps(payload, ensure_ascii=True, default=str)

    resolved, record = _resolve_allowed_path(path, must_exist=False)
    existed_before = resolved.exists()
    if existed_before and not overwrite:
        raise ValueError(f"file already exists: {resolved}")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content or "", encoding="utf-8")
    payload = {
        "file_id": (record or {}).get("file_id"),
        "filename": (record or {}).get("filename") or resolved.name,
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "overwritten": bool(overwrite and existed_before),
    }
    if record:
        payload["download_url"] = record.get("download_url")
    return json.dumps(payload, ensure_ascii=True, default=str)


def write_output_file_tool(filename: str, content: str, overwrite: bool = False) -> str:
    record = create_output_file(filename, content or "", overwrite=overwrite)
    payload = {
        "file_id": record["file_id"],
        "filename": record["filename"],
        "path": record["path"],
        "size_bytes": record["size_bytes"],
        "download_url": record["download_url"],
        "overwritten": bool(overwrite),
    }
    return json.dumps(payload, ensure_ascii=True, default=str)


def make_langchain_file_tools() -> List[Any]:
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    return [
        StructuredTool.from_function(
            func=read_text_file_tool,
            name="read_text_file",
            description=(
                "Read a UTF-8 text-like file from an allowed local path and return its contents. "
                "Input may be either a local path or an uploaded file_id. "
                "Use for txt, md, py, csv, json, or other text files."
            ),
            metadata={"category": "io"},
        ),
        StructuredTool.from_function(
            func=inspect_file_for_analysis_tool,
            name="inspect_file_for_analysis",
            description=(
                "Load a local file or uploaded file_id into an LLM-friendly JSON payload for interpretation. "
                "For CSV returns header and sample rows, for JSON returns structural preview, "
                "otherwise returns text content."
            ),
            metadata={"category": "io"},
        ),
        StructuredTool.from_function(
            func=write_text_file_tool,
            name="write_text_file",
            description=(
                "Write text output to a local file under an allowed root. "
                "Use for saving summaries, reports, code, or extracted notes."
            ),
            metadata={"category": "io"},
        ),
        StructuredTool.from_function(
            func=write_output_file_tool,
            name="write_output_file",
            description=(
                "Write downloadable output for the user into managed agent storage using only a filename. "
                "Returns file_id and download_url for the generated file."
            ),
            metadata={"category": "io"},
        ),
    ]


__all__ = [
    "inspect_file_for_analysis_tool",
    "make_langchain_file_tools",
    "read_text_file_tool",
    "write_output_file_tool",
    "write_text_file_tool",
]
