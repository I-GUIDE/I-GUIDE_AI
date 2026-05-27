"""
Tool: Factor Analysis for Mixed Data (FAMD) — Runs PCA, MCA, or FAMD depending on
variable types, via R subprocess using FactoMineR package.

- Quantitative only → PCA
- Qualitative only  → MCA
- Mixed             → FAMD
Missing values handled by missMDA package if handle_na=True.
"""
from __future__ import annotations
import json
import logging
import os
import subprocess
import tempfile
from typing import Callable

import pandas as pd

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["input_csv"]

_R_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "r_scripts", "famd.R")


async def run_factor_analysis_mixed_data(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    input_csv = params["input_csv"]
    quant_vars_raw = params.get("quantitative_variables", "")
    qual_vars_raw = params.get("qualitative_variables", "")
    n_comp = int(params.get("n_components", 5))
    handle_na = bool(params.get("handle_na", True))

    if isinstance(quant_vars_raw, str):
        quant_vars = [v.strip() for v in quant_vars_raw.split(",") if v.strip()]
    else:
        quant_vars = list(quant_vars_raw)

    if isinstance(qual_vars_raw, str):
        qual_vars = [v.strip() for v in qual_vars_raw.split(",") if v.strip()]
    else:
        qual_vars = list(qual_vars_raw)

    if not quant_vars and not qual_vars:
        raise CSISError(
            "Provide at least one of: quantitative_variables or qualitative_variables.",
            "INVALID_PARAMS",
        )

    if not os.path.exists(input_csv):
        raise CSISError(f"Input CSV not found: {input_csv}", "FILE_NOT_FOUND")

    workspace_dir, _ = generate_output_dir("famd", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    config = {
        "input_csv": input_csv,
        "quant_vars": quant_vars,
        "qual_vars": qual_vars,
        "n_comp": n_comp,
        "handle_na": handle_na,
        "output_dir": workspace_dir,
    }
    config_path = os.path.join(workspace_dir, "famd_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f)

    r_script = os.path.abspath(_R_SCRIPT)
    if not os.path.exists(r_script):
        raise CSISError(f"R script not found: {r_script}", "TOOL_FAILED")

    progress_callback(20, "Running FAMD analysis in R...")

    r_exe = os.environ.get("R_EXECUTABLE", "Rscript")
    try:
        proc = subprocess.run(
            [r_exe, r_script, config_path],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        raise CSISError("Rscript not found. Ensure R is installed.", "TOOL_FAILED")
    except subprocess.TimeoutExpired:
        raise CSISError("FAMD R analysis timed out (5 min limit).", "TOOL_FAILED")

    if proc.returncode != 0:
        raise CSISError(f"FAMD R script failed:\n{proc.stderr[-2000:]}", "TOOL_FAILED")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "famd")
    return {"files": files}
