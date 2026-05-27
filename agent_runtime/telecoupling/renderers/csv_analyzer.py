"""
CSV analyzer — parse tool output CSVs into frontend-renderable data.
"""
from __future__ import annotations
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

MAX_PREVIEW_ROWS = 50


def analyze_csv(csv_path: str) -> dict:
    """
    Read a CSV file and return frontend-renderable data.

    Returns:
        {
            "filename": str,
            "columns": list[str],
            "rows": list[dict],     # first MAX_PREVIEW_ROWS rows
            "total_rows": int,
            "chart_config": dict | None,
        }
    """
    filename = Path(csv_path).name
    df = pd.read_csv(csv_path)
    preview = df.head(MAX_PREVIEW_ROWS)
    # Convert to JSON-safe types
    rows = preview.where(pd.notnull(preview), None).to_dict(orient="records")
    return {
        "filename": filename,
        "columns": list(df.columns),
        "rows": rows,
        "total_rows": len(df),
        "chart_config": suggest_chart(df, filename),
    }


def suggest_chart(df: pd.DataFrame, filename: str) -> dict | None:
    """
    Suggest a chart type based on CSV content.
    Returns chart_config dict or None.
    """
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if not numeric_cols:
        return None

    # For result_table CSVs (crop production) → bar chart
    if "result_table" in filename:
        crop_col = next((c for c in df.columns if "crop" in c.lower()), None)
        prod_col = next(
            (c for c in df.columns if "prod" in c.lower() or "yield" in c.lower()), None
        )
        if crop_col and prod_col:
            return {
                "type": "bar",
                "x_field": crop_col,
                "y_field": prod_col,
                "title": "Crop Production",
            }

    # For network stats → degree centrality bar chart
    if "network_stats" in filename:
        if "degree" in df.columns:
            return {
                "type": "bar",
                "x_field": df.columns[0],
                "y_field": "degree",
                "title": "Node Degree Centrality",
            }

    # Generic: text column + numeric column → bar chart
    text_cols = df.select_dtypes(include="object").columns.tolist()
    if text_cols and numeric_cols:
        return {
            "type": "bar",
            "x_field": text_cols[0],
            "y_field": numeric_cols[0],
            "title": f"{numeric_cols[0]} by {text_cols[0]}",
        }

    return None
