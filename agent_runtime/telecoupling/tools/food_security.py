"""
Tool: Food Security — Analyze FAO food security indicators for selected countries
and generate trend charts (PNG) and summary tables.

Adapted from original (removed Earth Engine dependency): user provides FAO CSV data
and selects countries and indicators to analyze.
"""
from __future__ import annotations
import logging
import os
from typing import Callable

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["fao_csv", "countries", "indicator_field"]


async def run_food_security(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    fao_csv = params["fao_csv"]
    countries_raw = params["countries"]
    indicator_field = params["indicator_field"].strip()
    country_field = params.get("country_field", "Area")
    year_field = params.get("year_field", "Year")
    value_field = params.get("value_field", "Value")
    unit_field = params.get("unit_field", None)

    if isinstance(countries_raw, str):
        countries = [c.strip() for c in countries_raw.split(",") if c.strip()]
    else:
        countries = [str(c).strip() for c in countries_raw]

    if not os.path.exists(fao_csv):
        raise CSISError(f"FAO CSV not found: {fao_csv}", "FILE_NOT_FOUND")

    progress_callback(10, "Reading FAO data...")
    df = pd.read_csv(fao_csv, encoding="utf-8")

    for col in [country_field, year_field, value_field]:
        if col not in df.columns:
            raise CSISError(
                f"Column '{col}' not found. Available: {list(df.columns)}", "INVALID_PARAMS"
            )

    # Filter by indicator
    if indicator_field in df.columns:
        df = df[df[indicator_field].notna()]
        indicator_col = indicator_field
    else:
        # indicator_field might be a value to filter on "Item" column
        item_col = next((c for c in df.columns if c.lower() in ["item", "indicator", "element"]), None)
        if item_col:
            df = df[df[item_col].str.contains(indicator_field, case=False, na=False)]
            indicator_col = item_col
        else:
            raise CSISError(f"Indicator column '{indicator_field}' not found.", "INVALID_PARAMS")

    # Filter by countries
    df_filtered = df[df[country_field].isin(countries)].copy()
    if df_filtered.empty:
        available = df[country_field].unique()[:20].tolist()
        raise CSISError(
            f"No data for countries: {countries}. Available (sample): {available}", "INVALID_PARAMS"
        )

    df_filtered[value_field] = pd.to_numeric(df_filtered[value_field], errors="coerce")
    df_filtered[year_field] = pd.to_numeric(df_filtered[year_field], errors="coerce")
    df_filtered = df_filtered.dropna(subset=[year_field, value_field])

    workspace_dir, _ = generate_output_dir("food_security", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(50, "Generating charts...")

    unit_label = ""
    if unit_field and unit_field in df_filtered.columns:
        units = df_filtered[unit_field].dropna().unique()
        if len(units) > 0:
            unit_label = f" ({units[0]})"

    # Line chart: trend over years per country
    fig, ax = plt.subplots(figsize=(12, 6))
    for country in countries:
        sub = df_filtered[df_filtered[country_field] == country].sort_values(year_field)
        if not sub.empty:
            ax.plot(sub[year_field], sub[value_field], marker="o", label=country)
    ax.set_title(f"Food Security Indicator: {indicator_field}")
    ax.set_xlabel("Year")
    ax.set_ylabel(f"Value{unit_label}")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    fig.savefig(os.path.join(workspace_dir, "food_security_trend.png"), dpi=150)
    plt.close(fig)

    # Bar chart: latest year comparison
    latest_year = df_filtered[year_field].max()
    latest = df_filtered[df_filtered[year_field] == latest_year]
    if not latest.empty:
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        ax2.bar(latest[country_field], latest[value_field], color="steelblue")
        ax2.set_title(f"Comparison ({int(latest_year)}): {indicator_field}")
        ax2.set_xlabel("Country")
        ax2.set_ylabel(f"Value{unit_label}")
        ax2.tick_params(axis="x", rotation=45)
        plt.tight_layout()
        fig2.savefig(os.path.join(workspace_dir, "food_security_comparison.png"), dpi=150)
        plt.close(fig2)

    progress_callback(80, "Writing summary tables...")
    df_filtered.to_csv(os.path.join(workspace_dir, "food_security_data.csv"), index=False)

    pivot = df_filtered.pivot_table(index=year_field, columns=country_field,
                                    values=value_field, aggfunc="mean")
    pivot.to_csv(os.path.join(workspace_dir, "food_security_pivot.csv"))

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "food_security")
    return {"files": files}
