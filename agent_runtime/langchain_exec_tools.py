"""The `execute_code` tool — run/debug code in the sandbox (see code_execution.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Bounds on how much gets auto-staged into a sandbox run (conversation files +
# explicitly requested files). Keeps a large session from blowing up disk/time.
DEFAULT_MAX_INPUT_FILES = 20
DEFAULT_MAX_INPUT_MB = 200


def _max_input_files() -> int:
    try:
        return max(1, int(os.getenv("AGENT_CODE_EXEC_MAX_INPUT_FILES", str(DEFAULT_MAX_INPUT_FILES))))
    except (TypeError, ValueError):
        return DEFAULT_MAX_INPUT_FILES


def _max_input_bytes() -> int:
    try:
        mb = float(os.getenv("AGENT_CODE_EXEC_MAX_INPUT_MB", str(DEFAULT_MAX_INPUT_MB)))
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_INPUT_MB
    return int(max(1.0, mb) * 1024 * 1024)


def _resolve_input_file(ref: str) -> Tuple[Path, Optional[Dict[str, Any]]]:
    """Resolve an uploaded ``file_id`` (or an allowed local path) to a host path.

    Returns ``(host_path, record_or_None)``.  Raises ``ValueError`` if the
    reference cannot be resolved.
    """
    from agent_runtime.file_store import get_file_record, resolve_file_id

    ref = str(ref or "").strip()
    if not ref:
        raise ValueError("empty file reference")
    record = get_file_record(ref)
    if record:
        return resolve_file_id(ref), record
    # Fall back to an allowed local path (same policy as the file tools).
    from agent_runtime.langchain_file_tools import _resolve_allowed_path

    path, rec = _resolve_allowed_path(ref, must_exist=True)
    return path, rec


def _build_staging(refs: List[str]) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]], List[Dict[str, str]], List[Dict[str, Any]]]:
    """Resolve file references into copy specs for the sandbox work dir.

    Each resolved file is staged under BOTH its file_id and its original filename.
    Dedupes by resolved host path and enforces file-count / total-size caps.

    Returns ``(staging_specs, staged_info, errors, skipped)``.
    """
    staging: List[Dict[str, str]] = []
    staged_info: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    skipped: List[Dict[str, Any]] = []

    max_files = _max_input_files()
    max_bytes = _max_input_bytes()
    seen_sources: set[str] = set()
    total_bytes = 0

    for ref in refs:
        try:
            host_path, record = _resolve_input_file(ref)
        except Exception as exc:
            errors.append({"ref": str(ref), "error": str(exc)})
            continue
        src = str(host_path)
        if src in seen_sources:  # same file referenced by id and filename/path
            continue
        try:
            size = int((record or {}).get("size_bytes") or host_path.stat().st_size)
        except OSError:
            size = 0
        if len(staged_info) >= max_files:
            skipped.append({"ref": str(ref), "reason": "max input files exceeded", "limit": max_files})
            continue
        if total_bytes + size > max_bytes:
            skipped.append({"ref": str(ref), "reason": "max total input size exceeded",
                            "limit_bytes": max_bytes, "size_bytes": size})
            continue

        seen_sources.add(src)
        total_bytes += size
        filename = (record or {}).get("filename") or host_path.name
        file_id = (record or {}).get("file_id")
        names = list(dict.fromkeys(n for n in (file_id, filename) if n))
        for dest in names:
            staging.append({"source": src, "dest": dest})
        staged_info.append({"ref": str(ref), "file_id": file_id, "filename": filename, "available_as": names})

    return staging, staged_info, errors, skipped


def make_code_execution_tools(
    executor: Optional[Any] = None,
    default_input_file_ids: Optional[List[str]] = None,
    session_id: Optional[str] = None,
) -> List[Any]:
    """Build the `execute_code` StructuredTool (container-per-run sandbox).

    ``default_input_file_ids`` are the files attached to the current conversation;
    they are auto-staged into EVERY run so the model can read them without naming
    them, and are unioned (deduped) with any explicit ``input_files`` it passes.
    """
    from langchain_core.tools import StructuredTool

    from agent_runtime.code_execution import DEFAULT_TIMEOUT, get_code_executor

    default_ids = [str(x).strip() for x in (default_input_file_ids or []) if str(x).strip()]

    def execute_code(
        code: str,
        language: str = "python",
        timeout_seconds: int = DEFAULT_TIMEOUT,
        dependencies: Optional[List[str]] = None,
        input_files: Optional[List[str]] = None,
        label: Optional[str] = None,
    ) -> str:
        ex = executor or get_code_executor()

        # Union: conversation-attached files (auto) first, then any explicitly
        # named files, order-preserving and deduped.
        explicit = [str(r).strip() for r in (input_files or []) if str(r).strip()]
        refs = list(dict.fromkeys([*default_ids, *explicit]))
        staging, staged_info, input_errors, skipped = _build_staging(refs)

        # `label` only names the saved source, so pass it optionally: an executor
        # implementing the older signature (or a test double) still works.
        extra = {"label": label} if label else {}
        if session_id:
            extra["session"] = session_id   # durable workspace across runs in this conversation
        result = ex.execute(
            code,
            language=language,
            timeout=timeout_seconds,
            dependencies=dependencies,
            input_files=staging,
            **extra,
        )
        payload = result.to_dict()
        if staged_info:
            payload["input_files"] = staged_info
        if input_errors:
            payload["input_file_errors"] = input_errors
        if skipped:
            payload["input_files_skipped"] = skipped
        return json.dumps(payload, ensure_ascii=True, default=str)

    tool = StructuredTool.from_function(
        func=execute_code,
        name="execute_code",
        description=(
            "Execute code in an isolated, sandboxed container and return JSON with "
            "exit_code, stdout, stderr, timed_out, the executed `code`, `installed`, and "
            "`artifacts` (the source is saved as a downloadable .py named from `label`, plus "
            "any files the run wrote). Pass `dependencies` (a list of pip specs, e.g. "
            "[\"numpy\", \"pandas==2.2\"]) to install third-party packages before running — "
            "they are installed with network in a separate step, then the code runs with NO "
            "network. Files attached to this conversation are AUTOMATICALLY available in the "
            "working directory under both their file_id and their original filename (e.g. "
            "open('data.csv') or pd.read_csv('data.csv')). To read any other uploaded file, "
            "add its file_id to `input_files`. Use this to RUN and DEBUG code: run, read "
            "stdout/stderr, fix, re-run. Files you write persist in this conversation's working "
            "directory, so a later run can open what an earlier one produced and keep building "
            "on it (the container itself is fresh each time). `label` is a short slug for what this particular run "
            "does (e.g. \"csv_to_geojson\", \"rivers_buffer\") and becomes the saved source's "
            "filename — several runs in one conversation otherwise arrive as identically-named "
            "downloads; name the files your code writes for their contents too, for the same "
            "reason."
        ),
    )
    return [tool]


__all__ = ["make_code_execution_tools"]
