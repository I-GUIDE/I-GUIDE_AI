"""
Tool: Population Count and Density — Calculate population density per reporting unit
and optionally compute population change between two time periods.

Input: CSV with reporting unit IDs, population counts, and area (km²).
Optional second CSV for time-period comparison.
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import pandas as pd

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["input_csv", "population_field", "area_km2_field"]


async def run_population_count_density(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    input_csv = params["input_csv"]
    pop_field = params["population_field"].strip()
    area_field = params["area_km2_field"].strip()
    unit_id_field = params.get("unit_id_field", None)
    second_csv = params.get("second_period_csv", None)
    second_pop_field = params.get("second_period_population_field", None)

    if not os.path.exists(input_csv):
        raise CSISError(f"Input CSV not found: {input_csv}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading population data...")
    df = pd.read_csv(input_csv)

    for col in [pop_field, area_field]:
        if col not in df.columns:
            raise CSISError(
                f"Column '{col}' not found. Available: {list(df.columns)}", "INVALID_PARAMS"
            )

    progress_callback(40, "Calculating population density...")
    df = df.copy()
    df[pop_field] = pd.to_numeric(df[pop_field], errors="coerce")
    df[area_field] = pd.to_numeric(df[area_field], errors="coerce")

    df["pop_density_per_km2"] = df[pop_field] / df[area_field]

    # Time-period comparison
    if second_csv and second_pop_field:
        if not os.path.exists(second_csv):
            raise CSISError(f"Second period CSV not found: {second_csv}", "FILE_NOT_FOUND")
        df2 = pd.read_csv(second_csv)
        if second_pop_field not in df2.columns:
            raise CSISError(f"Column '{second_pop_field}' not in second CSV.", "INVALID_PARAMS")

        if unit_id_field and unit_id_field in df.columns and unit_id_field in df2.columns:
            df2_sub = df2[[unit_id_field, second_pop_field]].copy()
            df = df.merge(df2_sub, on=unit_id_field, how="left")
        else:
            if len(df2) == len(df):
                df[second_pop_field] = df2[second_pop_field].values
            else:
                raise CSISError(
                    "Cannot align second period data: provide a unit_id_field or ensure same row count.",
                    "INVALID_PARAMS",
                )

        df[second_pop_field] = pd.to_numeric(df[second_pop_field], errors="coerce")
        df["pop_growth_pct"] = ((df[second_pop_field] - df[pop_field]) / df[pop_field]) * 100

    workspace_dir, _ = generate_output_dir("population_density", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(70, "Writing outputs...")

    results_csv = os.path.join(workspace_dir, "population_density_results.csv")
    df.to_csv(results_csv, index=False)

    summary = {
        "total_units": len(df),
        "total_population": int(df[pop_field].sum()),
        "total_area_km2": round(df[area_field].sum(), 2),
        "avg_density_per_km2": round(df["pop_density_per_km2"].mean(), 4),
        "max_density_per_km2": round(df["pop_density_per_km2"].max(), 4),
        "min_density_per_km2": round(df["pop_density_per_km2"].min(), 4),
    }
    if "pop_growth_pct" in df.columns:
        summary["avg_pop_growth_pct"] = round(df["pop_growth_pct"].mean(), 2)

    summary_csv = os.path.join(workspace_dir, "population_density_summary.csv")
    pd.DataFrame([summary]).to_csv(summary_csv, index=False)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "population_density")
    return {"files": files}
