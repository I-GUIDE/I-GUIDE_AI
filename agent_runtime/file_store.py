from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

DEFAULT_STORAGE_DIR = "agent_chat_files"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_configured_root(value: str) -> Path:
    configured = Path(value).expanduser()
    if configured.is_absolute():
        return configured.resolve()
    return (_repo_root() / configured).resolve()


def storage_root() -> Path:
    configured = os.getenv("AGENT_FILE_STORAGE_ROOT")
    if configured:
        root = _resolve_configured_root(configured)
        root.mkdir(parents=True, exist_ok=True)
        return root

    candidates = [(_repo_root() / DEFAULT_STORAGE_DIR).resolve()]

    last_error: Optional[Exception] = None
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error

    raise RuntimeError("Unable to determine agent file storage root.")


def _uploads_dir() -> Path:
    path = storage_root() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _outputs_dir() -> Path:
    path = storage_root() / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _metadata_dir() -> Path:
    path = storage_root() / "metadata"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _metadata_path(file_id: str) -> Path:
    return _metadata_dir() / f"{file_id}.json"


def _build_download_url(file_id: str) -> str:
    return f"/agent/files/{file_id}/download"


def get_file_record(file_id: str) -> Optional[Dict[str, Any]]:
    if not file_id:
        return None
    meta_path = _metadata_path(str(file_id).strip())
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def require_file_record(file_id: str) -> Dict[str, Any]:
    record = get_file_record(file_id)
    if not record:
        raise ValueError(f"unknown file_id: {file_id}")
    return record


def resolve_file_id(file_id: str) -> Path:
    record = require_file_record(file_id)
    path = _record_path(record)
    if not path.exists():
        raise ValueError(
            f"file for file_id does not exist: {file_id}; "
            f"expected under storage root: {storage_root()}"
        )
    return path


def _write_record(record: Dict[str, Any]) -> Dict[str, Any]:
    _metadata_path(record["file_id"]).write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")
    return record


def _record_path(record: Dict[str, Any]) -> Path:
    root = storage_root()
    relative_path = record.get("relative_path")
    if relative_path:
        return (root / str(relative_path)).resolve()

    stored_path = record.get("path")
    if stored_path:
        candidate = Path(str(stored_path)).expanduser()
        if candidate.is_absolute() and candidate.exists():
            return candidate.resolve()
        if not candidate.is_absolute():
            relative_candidate = (root / candidate).resolve()
            if relative_candidate.exists():
                return relative_candidate

    filename = record.get("filename")
    file_id = record.get("file_id")
    kind = record.get("kind") or "upload"
    if filename and file_id:
        folder = "outputs" if kind == "output" else "uploads"
        return (root / folder / f"{file_id}__{filename}").resolve()

    if stored_path:
        candidate = Path(str(stored_path)).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    raise ValueError(f"file record missing path for file_id: {record.get('file_id')}")


def save_uploaded_file(file_storage: FileStorage) -> Dict[str, Any]:
    original_name = secure_filename(file_storage.filename or "upload.bin") or "upload.bin"
    file_id = f"file_{uuid4().hex[:12]}"
    relative_path = Path("uploads") / f"{file_id}__{original_name}"
    target = storage_root() / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(target)
    record = {
        "file_id": file_id,
        "filename": original_name,
        "kind": "upload",
        "path": str(relative_path),
        "relative_path": str(relative_path),
        "size_bytes": target.stat().st_size,
        "download_url": _build_download_url(file_id),
    }
    return _write_record(record)


def create_output_file(filename: str, content: str, overwrite: bool = False) -> Dict[str, Any]:
    safe_name = secure_filename(filename or "agent_output.txt") or "agent_output.txt"
    existing_record: Optional[Dict[str, Any]] = None

    if overwrite:
        for meta_path in _metadata_dir().glob("*.json"):
            try:
                record = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if record.get("kind") == "output" and record.get("filename") == safe_name:
                existing_record = record
                break

    if existing_record:
        file_id = str(existing_record["file_id"])
    else:
        file_id = f"file_{uuid4().hex[:12]}"

    relative_path = Path("outputs") / f"{file_id}__{safe_name}"
    target = storage_root() / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    record = {
        "file_id": file_id,
        "filename": safe_name,
        "kind": "output",
        "path": str(relative_path),
        "relative_path": str(relative_path),
        "size_bytes": target.stat().st_size,
        "download_url": _build_download_url(file_id),
    }
    return _write_record(record)


def create_output_file_from_path(
    source_path: str | Path,
    filename: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"source file does not exist: {source}")

    safe_name = secure_filename(filename or source.name or "agent_output.bin") or "agent_output.bin"
    existing_record: Optional[Dict[str, Any]] = None

    if overwrite:
        for meta_path in _metadata_dir().glob("*.json"):
            try:
                record = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if record.get("kind") == "output" and record.get("filename") == safe_name:
                existing_record = record
                break

    if existing_record:
        file_id = str(existing_record["file_id"])
    else:
        file_id = f"file_{uuid4().hex[:12]}"

    relative_path = Path("outputs") / f"{file_id}__{safe_name}"
    target = storage_root() / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    record = {
        "file_id": file_id,
        "filename": safe_name,
        "kind": "output",
        "path": str(relative_path),
        "relative_path": str(relative_path),
        "size_bytes": target.stat().st_size,
        "download_url": _build_download_url(file_id),
    }
    return _write_record(record)


__all__ = [
    "create_output_file",
    "create_output_file_from_path",
    "get_file_record",
    "require_file_record",
    "resolve_file_id",
    "save_uploaded_file",
    "storage_root",
]
