"""
Tool: Cost-Benefit Analysis — Join economic cost/revenue data to a feature table
and compute net returns (RETURNS = REVENUES - COSTS).
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import pandas as pd

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["input_csv", "economic_data_csv", "key_field"]


async def run_cost_benefit_analysis(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    input_csv = params["input_csv"]
    econ_csv = params["economic_data_csv"]
    key_field = params["key_field"].strip()
    cost_field = params.get("cost_field", "COSTS")
    revenue_field = params.get("revenue_field", "REVENUES")

    for path in [input_csv, econ_csv]:
        if not os.path.exists(path):
            raise CSISError(f"File not found: {path}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading input data...")
    df_main = pd.read_csv(input_csv)
    df_econ = pd.read_csv(econ_csv)

    if key_field not in df_main.columns:
        raise CSISError(f"Key field '{key_field}' not in input CSV columns: {list(df_main.columns)}", "INVALID_PARAMS")
    if key_field not in df_econ.columns:
        raise CSISError(f"Key field '{key_field}' not in economic CSV columns: {list(df_econ.columns)}", "INVALID_PARAMS")

    for col in [cost_field, revenue_field]:
        if col not in df_econ.columns:
            raise CSISError(
                f"Column '{col}' not found in economic data. Available: {list(df_econ.columns)}",
                "INVALID_PARAMS",
            )

    progress_callback(40, "Joining data and computing returns...")
    econ_sub = df_econ[[key_field, cost_field, revenue_field]].copy()
    econ_sub[cost_field] = pd.to_numeric(econ_sub[cost_field], errors="coerce").fillna(0)
    econ_sub[revenue_field] = pd.to_numeric(econ_sub[revenue_field], errors="coerce").fillna(0)

    merged = df_main.merge(econ_sub, on=key_field, how="left")
    merged[cost_field] = merged[cost_field].fillna(0)
    merged[revenue_field] = merged[revenue_field].fillna(0)
    merged["RETURNS"] = merged[revenue_field] - merged[cost_field]

    workspace_dir, _ = generate_output_dir("cost_benefit_analysis", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(70, "Writing outputs...")

    results_csv = os.path.join(workspace_dir, "cba_results.csv")
    merged.to_csv(results_csv, index=False)

    summary = {
        "total_features": len(merged),
        "total_costs": round(merged[cost_field].sum(), 2),
        "total_revenues": round(merged[revenue_field].sum(), 2),
        "net_returns": round(merged["RETURNS"].sum(), 2),
        "profitable_count": int((merged["RETURNS"] > 0).sum()),
        "loss_count": int((merged["RETURNS"] < 0).sum()),
    }
    summary_csv = os.path.join(workspace_dir, "cba_summary.csv")
    pd.DataFrame([summary]).to_csv(summary_csv, index=False)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "cost_benefit_analysis")
    return {"files": files}
