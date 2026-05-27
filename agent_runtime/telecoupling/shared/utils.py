"""
Shared utility functions for CSIS backend.
"""
from __future__ import annotations
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Matches absolute filesystem paths (unix /data/uploads/... or Windows C:\...).
# Only matches when preceded by whitespace/quote/paren/start so module names
# like "natcap/invest" inside prose are left untouched.
_ABS_PATH_RE = re.compile(r'(?<![^\s\'"(>])(?:[A-Za-z]:)?(?:[/\\][^/\\\s\'"]+)+')


def sanitize_error_message(msg: Any) -> str:
    """Strip absolute server paths from error text shown to end users.

    InVEST/GDAL errors embed full server paths such as
    /data/uploads/csis_xxx/file.shx — users should only ever see the
    filename, never the internal directory layout.
    """
    if not msg:
        return "" if msg is None else str(msg)

    def _basename(m: re.Match) -> str:
        return m.group(0).replace('\\', '/').rsplit('/', 1)[-1]

    return _ABS_PATH_RE.sub(_basename, str(msg))


class CSISError(Exception):
    """Base class for all CSIS tool errors."""

    def __init__(self, message: str, error_code: str, details: dict | None = None):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(message)


ERROR_CODES = {
    "MISSING_PARAMS":   "Missing required parameters",
    "INVALID_PARAMS":   "Invalid parameter format",
    "TOOL_FAILED":      "Tool execution failed",
    "QGIS_TIMEOUT":     "QGIS render timeout (120s)",
    "QGIS_FAILED":      "QGIS render failed",
    "FILE_NOT_FOUND":   "Input file not found",
    "R_FAILED":         "R script execution failed",
    "OUTPUT_NOT_FOUND": "Tool did not produce expected output files",
}


def parse_input_data(raw: Any) -> dict:
    """Parse input data from dict or JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            import ast
            return ast.literal_eval(raw)
        except Exception:
            pass
        try:
            return json.loads(raw)
        except Exception:
            pass
        raise CSISError("Cannot parse input_data string", "INVALID_PARAMS")
    raise CSISError(f"Unexpected input_data type: {type(raw)}", "INVALID_PARAMS")


async def download_file(url: str, local_name: str, session_id: str) -> str:
    """Download a file from URL and save locally. Returns local path."""
    import aiohttp
    from agent_runtime.telecoupling.config import settings
    upload_dir = os.path.join(settings.UPLOADS_DIR, session_id)
    os.makedirs(upload_dir, exist_ok=True)
    local_path = os.path.join(upload_dir, local_name)
    async with aiohttp.ClientSession() as http_session:
        async with http_session.get(url) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(8192):
                    f.write(chunk)
    return local_path


def generate_output_dir(tool_name: str, session_id: str) -> tuple[str, str]:
    """
    Generate a unique output directory for a tool run.
    Returns (absolute_path, folder_name).
    folder_name is relative to SHARED_DIR: "{session_id}/{timestamp}_{tool_name}"
    """
    from agent_runtime.telecoupling.config import settings
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    subdir_name = f"{timestamp}_{tool_name}"
    abs_path = os.path.join(settings.SHARED_DIR, session_id, subdir_name)
    os.makedirs(abs_path, exist_ok=True)
    folder_name = f"{session_id}/{subdir_name}"
    return abs_path, folder_name


def build_result_urls(folder_name: str, files: list, subfolder: str = "") -> list[dict]:
    """Build result URL list from output files."""
    from agent_runtime.telecoupling.config import settings
    base = settings.FILE_SERVER_URL.rstrip("/")
    results = []
    for f in files:
        filename = f.get("filename", "")
        render_type = f.get("render_type", "download")
        if subfolder:
            url = f"{base}/{folder_name}/{subfolder}/{filename}"
        else:
            url = f"{base}/{folder_name}/{filename}"
        results.append({
            "filename": filename,
            "url": url,
            "render_type": render_type,
            "path": f.get("path", ""),
        })
    return results


def run_r_script(script_path: str, config: dict, session_id: str, task_id: str) -> dict:
    """Run an R script with the given config dict. Returns parsed stdout JSON."""
    import shutil
    rscript_path = shutil.which("Rscript")
    if not rscript_path:
        raise CSISError("Rscript not found in PATH", "R_FAILED")
    # Use absolute script path to avoid CWD-dependent resolution
    script_path_abs = os.path.abspath(script_path)
    logger.info(f"[R] Rscript={rscript_path} script={script_path_abs} cwd={os.getcwd()}")
    if not os.path.isfile(script_path_abs):
        raise CSISError(f"R script not found: {script_path_abs}", "R_FAILED")
    try:
        proc = subprocess.run(
            [rscript_path, script_path_abs, json.dumps(config)],
            capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        raise CSISError("Rscript not found in PATH", "R_FAILED")
    except subprocess.TimeoutExpired:
        raise CSISError("R script timed out (600s)", "R_FAILED")

    logger.info(f"[R] returncode={proc.returncode} stdout={proc.stdout[:200]} stderr={proc.stderr[:200]}")
    if proc.returncode != 0:
        raise CSISError(
            f"R script failed (code {proc.returncode}): {proc.stderr[:500]}",
            "R_FAILED", {"stderr": proc.stderr, "stdout": proc.stdout},
        )

    stdout = proc.stdout.strip()
    if not stdout:
        return {"success": True}
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning(f"R stdout not JSON: {stdout[:200]}")
        return {"success": True, "raw_output": stdout}


def validate_required(params: dict, required_keys: list[str]) -> None:
    """Raise CSISError(MISSING_PARAMS) if any required key is absent or empty."""
    missing = [k for k in required_keys if not params.get(k)]
    if missing:
        raise CSISError(
            f"Missing required parameters: {', '.join(missing)}",
            "MISSING_PARAMS", {"missing": missing},
        )


async def scan_output_directory(workspace_dir: str, tool_name: str) -> list[dict]:
    """
    Scan workspace_dir for output files. Returns list of
    {"filename": ..., "path": ..., "render_type": ...} dicts.
    Async to allow preview generation within running event loops.
    """
    from agent_runtime.telecoupling.renderers.output_router import route_outputs_async
    return await route_outputs_async(workspace_dir, tool_name)
