from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def storage_root() -> Path:
    configured = os.getenv("AGENT_FILE_STORAGE_ROOT")
    if configured:
        root = Path(configured).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    candidates = []
    upload_folder = os.getenv("UPLOAD_FOLDER")
    if upload_folder:
        candidates.append(Path(upload_folder).expanduser().resolve() / "agent_chat_files")
    candidates.append((_repo_root() / "agent_chat_files").resolve())

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
    path = Path(record["path"]).resolve()
    if not path.exists():
        raise ValueError(f"file for file_id does not exist: {file_id}")
    return path


def _write_record(record: Dict[str, Any]) -> Dict[str, Any]:
    _metadata_path(record["file_id"]).write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")
    return record


def save_uploaded_file(file_storage: FileStorage) -> Dict[str, Any]:
    original_name = secure_filename(file_storage.filename or "upload.bin") or "upload.bin"
    file_id = f"file_{uuid4().hex[:12]}"
    target = _uploads_dir() / f"{file_id}__{original_name}"
    file_storage.save(target)
    record = {
        "file_id": file_id,
        "filename": original_name,
        "kind": "upload",
        "path": str(target),
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

    target = _outputs_dir() / f"{file_id}__{safe_name}"
    target.write_text(content or "", encoding="utf-8")
    record = {
        "file_id": file_id,
        "filename": safe_name,
        "kind": "output",
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "download_url": _build_download_url(file_id),
    }
    return _write_record(record)


__all__ = [
    "create_output_file",
    "get_file_record",
    "require_file_record",
    "resolve_file_id",
    "save_uploaded_file",
    "storage_root",
]
