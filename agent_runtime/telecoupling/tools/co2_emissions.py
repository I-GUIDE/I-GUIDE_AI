"""
Tool: CO2 Emissions — Calculate CO2 emissions from wildlife/goods transport routes.

User provides a CSV with transport route data. Tool computes total CO2 emissions
per route based on: length × trips_needed × emission_factor_per_km_per_trip.
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import math
import pandas as pd

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["input_csv", "capacity_per_trip", "co2_per_km_per_trip"]


async def run_co2_emissions(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    input_csv = params["input_csv"]
    capacity = float(params["capacity_per_trip"])
    co2_per_km = float(params["co2_per_km_per_trip"])

    animal_field = params.get("animal_count_field", "animal_count")
    length_field = params.get("length_km_field", "length_km")
    id_field = params.get("id_field", None)

    if not os.path.exists(input_csv):
        raise CSISError(f"Input CSV not found: {input_csv}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading route data...")
    df = pd.read_csv(input_csv)

    for col in [animal_field, length_field]:
        if col not in df.columns:
            raise CSISError(
                f"Column '{col}' not found. Available columns: {list(df.columns)}",
                "INVALID_PARAMS",
            )

    progress_callback(40, "Calculating CO2 emissions...")

    df = df.copy()
    df["trips_needed"] = df[animal_field].apply(lambda n: math.ceil(n / capacity) if capacity > 0 else 0)
    df["co2_emissions_kg"] = df[length_field] * df["trips_needed"] * co2_per_km

    workspace_dir, _ = generate_output_dir("co2_emissions", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    out_cols = list(df.columns)
    if id_field and id_field not in out_cols:
        id_field = None

    summary = {
        "total_routes": len(df),
        "total_trips": int(df["trips_needed"].sum()),
        "total_co2_kg": round(df["co2_emissions_kg"].sum(), 3),
        "avg_co2_per_route_kg": round(df["co2_emissions_kg"].mean(), 3),
        "max_co2_route_kg": round(df["co2_emissions_kg"].max(), 3),
    }

    progress_callback(70, "Writing outputs...")

    results_csv = os.path.join(workspace_dir, "co2_emissions_results.csv")
    df.to_csv(results_csv, index=False)

    summary_csv = os.path.join(workspace_dir, "co2_emissions_summary.csv")
    pd.DataFrame([summary]).to_csv(summary_csv, index=False)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "co2_emissions")
    return {"files": files}
