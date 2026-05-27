"""
Tool: Nutrition Metrics — Calculate Lower Limit Energy Requirements (LLER) using
FAO nutritional formulas applied to population data by age group and sex.

Adapted from original (removed ArcGIS/WorldPop raster dependency):
user provides a population CSV with columns for age group, sex, and count.
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["population_csv"]

# FAO Basal Metabolic Rate (BMR) equations by age group and sex (Harris-Benedict / Schofield)
# Returns BMR in kcal/day given weight_kg
# LLER = BMR * 1.55 (sedentary PAL lower bound)
_BMR_EQUATIONS: dict[str, dict] = {
    "male": {
        "0-3":   {"a": 60.9,  "b": -54},
        "3-10":  {"a": 22.7,  "b": 495},
        "10-18": {"a": 17.5,  "b": 651},
        "18-30": {"a": 15.3,  "b": 679},
        "30-60": {"a": 11.6,  "b": 879},
        "60+":   {"a": 13.5,  "b": 487},
    },
    "female": {
        "0-3":   {"a": 61.0,  "b": -51},
        "3-10":  {"a": 22.5,  "b": 499},
        "10-18": {"a": 12.2,  "b": 746},
        "18-30": {"a": 14.7,  "b": 496},
        "30-60": {"a": 8.7,   "b": 829},
        "60+":   {"a": 10.5,  "b": 596},
    },
}

_DEFAULT_WEIGHTS = {
    "male":   {"0-3": 14, "3-10": 26, "10-18": 55, "18-30": 65, "30-60": 68, "60+": 66},
    "female": {"0-3": 13, "3-10": 25, "10-18": 50, "18-30": 55, "30-60": 58, "60+": 56},
}
PAL_LOWER = 1.55


def _ller_per_person(sex: str, age_group: str, weight_kg: float) -> float:
    eq = _BMR_EQUATIONS.get(sex.lower(), {}).get(age_group)
    if eq is None:
        return 0.0
    bmr = eq["a"] * weight_kg + eq["b"]
    return bmr * PAL_LOWER


async def run_nutrition_metrics(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    pop_csv = params["population_csv"]
    age_field = params.get("age_group_field", "age_group")
    sex_field = params.get("sex_field", "sex")
    count_field = params.get("population_count_field", "population")
    weight_field = params.get("weight_kg_field", None)
    male_height_cm = float(params.get("male_height_cm", 170))
    female_height_cm = float(params.get("female_height_cm", 158))

    if not os.path.exists(pop_csv):
        raise CSISError(f"Population CSV not found: {pop_csv}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading population data...")
    df = pd.read_csv(pop_csv)

    for col in [age_field, sex_field, count_field]:
        if col not in df.columns:
            raise CSISError(f"Column '{col}' not found. Available: {list(df.columns)}", "INVALID_PARAMS")

    progress_callback(40, "Calculating LLER by age group and sex...")
    df = df.copy()
    df[count_field] = pd.to_numeric(df[count_field], errors="coerce").fillna(0)

    ller_rows = []
    for _, row in df.iterrows():
        sex = str(row[sex_field]).lower().strip()
        age_grp = str(row[age_field]).strip()
        count = float(row[count_field])

        if weight_field and weight_field in df.columns:
            weight = float(row[weight_field])
        else:
            weight = _DEFAULT_WEIGHTS.get(sex, _DEFAULT_WEIGHTS["male"]).get(age_grp, 60)

        ller_pp = _ller_per_person(sex, age_grp, weight)
        ller_total = ller_pp * count

        ller_rows.append({
            **row.to_dict(),
            "weight_kg_used": weight,
            "ller_kcal_per_person_day": round(ller_pp, 2),
            "ller_kcal_total_day": round(ller_total, 2),
            "ller_kcal_total_year": round(ller_total * 365, 2),
        })

    result_df = pd.DataFrame(ller_rows)

    workspace_dir, _ = generate_output_dir("nutrition_metrics", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(70, "Writing outputs and charts...")

    result_df.to_csv(os.path.join(workspace_dir, "nutrition_metrics_results.csv"), index=False)

    summary = {
        "total_population": int(df[count_field].sum()),
        "total_ller_kcal_per_day": round(result_df["ller_kcal_total_day"].sum(), 2),
        "total_ller_kcal_per_year": round(result_df["ller_kcal_total_year"].sum(), 2),
        "avg_ller_per_person_kcal_day": round(result_df["ller_kcal_per_person_day"].mean(), 2),
    }
    pd.DataFrame([summary]).to_csv(os.path.join(workspace_dir, "nutrition_metrics_summary.csv"), index=False)

    # Chart: LLER by age group
    fig, ax = plt.subplots(figsize=(10, 5))
    pivot = result_df.groupby([age_field, sex_field])["ller_kcal_total_day"].sum().unstack(fill_value=0)
    pivot.plot(kind="bar", ax=ax, color=["#2196F3", "#E91E63"])
    ax.set_title("Total LLER (kcal/day) by Age Group and Sex")
    ax.set_xlabel("Age Group")
    ax.set_ylabel("LLER (kcal/day)")
    ax.tick_params(axis="x", rotation=45)
    plt.tight_layout()
    fig.savefig(os.path.join(workspace_dir, "nutrition_ller_chart.png"), dpi=150)
    plt.close(fig)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "nutrition_metrics")
    return {"files": files}
