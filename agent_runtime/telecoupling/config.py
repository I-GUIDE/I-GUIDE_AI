"""Lightweight settings shim for the vendored TelecouplingAI toolbox.

The original TelecouplingAI backend used a ``pydantic_settings.BaseSettings``
object (``backend/config.py``).  Inside i-GUIDE we do not want to pull in that
dependency or its Docker-specific defaults, so this module provides a minimal,
dependency-free ``settings`` object exposing the same attribute names the
vendored tool / renderer / shared modules read.

Scratch outputs are written under ``SHARED_DIR`` (a temp directory by default);
the LangChain wrapper copies any produced files into i-GUIDE's managed file
store so they get a stable ``download_url``.  Every value can be overridden via
environment variables for deployments that run the heavy InVEST/R/QGIS stack.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent


def _env_path(var: str, default: str) -> str:
    value = os.getenv(var)
    return value if value else default


class _Settings:
    """Attribute-compatible stand-in for the TelecouplingAI ``settings``."""

    # --- Storage ---------------------------------------------------------
    # Scratch workspace for tool runs. Defaults to a temp dir so i-GUIDE can
    # run the toolbox without provisioning Docker volumes. Outputs are later
    # copied into the managed agent file store for download.
    SHARED_DIR: str = _env_path(
        "TELECOUPLING_SHARED_DIR", str(Path(tempfile.gettempdir()) / "telecoupling_outputs")
    )
    UPLOADS_DIR: str = _env_path(
        "TELECOUPLING_UPLOADS_DIR", str(Path(tempfile.gettempdir()) / "telecoupling_uploads")
    )
    # Used only by build_result_urls (unused in the i-GUIDE wrapper, which
    # registers files via the managed store instead). Kept for compatibility.
    FILE_SERVER_URL: str = _env_path("TELECOUPLING_FILE_SERVER_URL", "file:///")

    # --- InVEST model data (sample/auxiliary datasets some models need) ---
    MODEL_DATA_PATH: str = _env_path("TELECOUPLING_MODEL_DATA_PATH", str(_PKG_ROOT / "model_data"))

    # --- R scripts (vendored alongside this package) ---------------------
    R_SCRIPT_DIR: str = _env_path("TELECOUPLING_R_SCRIPT_DIR", str(_PKG_ROOT / "r_scripts"))

    # --- QGIS ------------------------------------------------------------
    # Rendering in i-GUIDE goes through headless PyQGIS (rag_pipeline.
    # qgis_headless_tools), not these QGIS-Desktop paths. Retained so any
    # stray reference resolves rather than raising AttributeError.
    QGIS_PYTHON_PATH: str = _env_path("TELECOUPLING_QGIS_PYTHON_PATH", "python3")
    QGIS_PYTHON_BINDINGS: str = _env_path("TELECOUPLING_QGIS_PYTHON_BINDINGS", "")
    QGIS_MAX_CONCURRENT: int = int(os.getenv("TELECOUPLING_QGIS_MAX_CONCURRENT", "3"))

    # --- Misc (referenced by TC code paths we don't exercise) ------------
    REDIS_URL: str = _env_path("TELECOUPLING_REDIS_URL", "redis://localhost:6379/0")
    SESSION_TTL_HOURS: int = int(os.getenv("TELECOUPLING_SESSION_TTL_HOURS", "24"))
    MAX_SESSIONS: int = int(os.getenv("TELECOUPLING_MAX_SESSIONS", "50"))


settings = _Settings()
