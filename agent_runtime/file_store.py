from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
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


def _public_base_url() -> str:
    """Optional absolute origin for download URLs (e.g. ``http://149.165.147.219:3500``).

    Unset (default): ``download_url`` stays HOST-RELATIVE and clients resolve it against the
    origin they call — robust across port mappings and proxies. Set ``AGENT_PUBLIC_BASE_URL``
    when clients can't do that resolution and need ready-to-use absolute URLs.
    """
    base = (os.getenv("AGENT_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return base if base.lower().startswith(("http://", "https://")) else ""


def _with_public_url(record: Dict[str, Any]) -> Dict[str, Any]:
    """Absolutize the record's download_url for EMISSION only. Persisted metadata always keeps
    the host-relative path, so records stay valid if the public base URL changes later."""
    base = _public_base_url()
    url = record.get("download_url")
    if base and isinstance(url, str) and url.startswith("/"):
        record = dict(record)
        record["download_url"] = f"{base}{url}"
    return record


# ---------------------------------------------------------------------------
# Retention / TTL sweep
# ---------------------------------------------------------------------------
# The store has no other GC: uploads + generated outputs accumulate forever. An OPT-IN TTL
# sweep deletes data files (and their metadata) older than AGENT_FILE_RETENTION_DAYS. It is
# DISABLED by default (unset / <= 0) so existing deployments keep their current behavior until
# an operator opts in — deleting user files is irreversible. Age is measured from each file's
# last-modified time. The sweep runs opportunistically (throttled) when files are created, so
# no background scheduler is needed; it can also be invoked directly (cron / CLI).

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86400.0
_DEFAULT_SWEEP_INTERVAL_SECONDS = 3600.0
_LAST_SWEEP = {"t": 0.0}
_SWEEP_LOCK = threading.Lock()


def _retention_seconds() -> Optional[float]:
    """TTL in seconds from AGENT_FILE_RETENTION_DAYS, or None when retention is disabled
    (unset, non-numeric, or <= 0). None => keep files forever (the default)."""
    raw = os.getenv("AGENT_FILE_RETENTION_DAYS")
    if raw is None:
        return None
    try:
        days = float(raw)
    except (TypeError, ValueError):
        return None
    return days * _SECONDS_PER_DAY if days > 0 else None


def _sweep_interval_seconds() -> float:
    try:
        return max(60.0, float(os.getenv("AGENT_FILE_SWEEP_INTERVAL_SECONDS",
                                         str(_DEFAULT_SWEEP_INTERVAL_SECONDS))))
    except (TypeError, ValueError):
        return _DEFAULT_SWEEP_INTERVAL_SECONDS


def sweep_expired_files(max_age_days: Optional[float] = None, *, now: Optional[float] = None) -> Dict[str, int]:
    """Delete stored files (uploads + outputs) and their metadata older than the TTL.

    Age is each file's mtime. Deleting an expired data file also removes its ``metadata/<id>.json``
    sidecar; an expired metadata json whose data file is already gone is removed too (a present,
    non-expired data file is never orphaned). TTL comes from *max_age_days* else
    AGENT_FILE_RETENTION_DAYS; <= 0 / unset disables the sweep (no-op). Never raises — a sweep
    must not break an upload or a request. Returns counts: {scanned, deleted, freed_bytes, errors}.
    """
    ttl = (max_age_days * _SECONDS_PER_DAY) if (max_age_days and max_age_days > 0) else _retention_seconds()
    result = {"scanned": 0, "deleted": 0, "freed_bytes": 0, "errors": 0}
    if not ttl:
        return result
    cutoff = (time.time() if now is None else now) - ttl
    try:
        root = storage_root()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("File sweep skipped: storage root unavailable (%s)", exc)
        return result

    def _expired(p: Path) -> bool:
        try:
            return p.stat().st_mtime < cutoff
        except OSError:
            return False

    # 1) expired DATA files (+ their metadata sidecar)
    for sub in ("uploads", "outputs"):
        d = root / sub
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if not p.is_file():
                continue
            result["scanned"] += 1
            if not _expired(p):
                continue
            try:
                size = p.stat().st_size
                p.unlink()
                result["deleted"] += 1
                result["freed_bytes"] += size
            except OSError:
                result["errors"] += 1
                continue
            file_id = p.name.split("__", 1)[0]
            meta = root / "metadata" / f"{file_id}.json"
            try:
                if meta.is_file():
                    result["freed_bytes"] += meta.stat().st_size
                    meta.unlink()
                    result["deleted"] += 1
            except OSError:
                pass

    # 2) expired ORPHAN metadata (its data file is already gone) — never orphan a present file
    meta_dir = root / "metadata"
    if meta_dir.is_dir():
        for mp in meta_dir.glob("*.json"):
            if not _expired(mp):
                continue
            file_id = mp.stem
            data_present = any(
                next((root / sub).glob(f"{file_id}__*"), None) is not None
                for sub in ("uploads", "outputs")
            )
            if data_present:
                continue
            try:
                mp.unlink()
                result["deleted"] += 1
            except OSError:
                result["errors"] += 1

    if result["deleted"]:
        logger.info("File sweep removed %d item(s), freed %d bytes from %s",
                    result["deleted"], result["freed_bytes"], root)
    return result


def maybe_sweep_expired_files() -> None:
    """Opportunistic, throttled sweep — at most once per AGENT_FILE_SWEEP_INTERVAL_SECONDS per
    process. Called from the file-creation paths so retention is enforced without a scheduler.
    No-op when retention is disabled. Never raises."""
    if _retention_seconds() is None:
        return
    nowt = time.time()
    with _SWEEP_LOCK:
        if nowt - _LAST_SWEEP["t"] < _sweep_interval_seconds():
            return
        _LAST_SWEEP["t"] = nowt
    try:
        sweep_expired_files()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Opportunistic file sweep failed: %s", exc)


def get_file_record(file_id: str) -> Optional[Dict[str, Any]]:
    if not file_id:
        return None
    meta_path = _metadata_path(str(file_id).strip())
    if not meta_path.exists():
        return None
    return _with_public_url(json.loads(meta_path.read_text(encoding="utf-8")))


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
    maybe_sweep_expired_files()
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
    return _with_public_url(_write_record(record))


def create_output_file(filename: str, content: str, overwrite: bool = False) -> Dict[str, Any]:
    maybe_sweep_expired_files()
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
    return _with_public_url(_write_record(record))


def create_output_file_from_path(
    source_path: str | Path,
    filename: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    maybe_sweep_expired_files()
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
    return _with_public_url(_write_record(record))


__all__ = [
    "create_output_file",
    "create_output_file_from_path",
    "get_file_record",
    "maybe_sweep_expired_files",
    "require_file_record",
    "resolve_file_id",
    "save_uploaded_file",
    "storage_root",
    "sweep_expired_files",
]
