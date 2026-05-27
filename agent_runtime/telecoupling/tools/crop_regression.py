"""
Tool 6: Crop Production Regression (natcap.invest).

Supported crops are determined dynamically from:
  model_data/climate_regression_yield_tables/*_regression_yield_table.csv

Crop name matching is normalized (lowercase + remove spaces/hyphens).
model_data_path is provided by the user in params.
"""
from __future__ import annotations
import csv
import glob
import logging
import os
from typing import Callable

import natcap.invest.crop_production_regression

from agent_runtime.telecoupling.shared.utils import (
    CSISError, validate_required, generate_output_dir, scan_output_directory
)

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "landcover_raster_path",
    "landcover_to_crop_table_path",
    "fertilization_rate_table_path",
]


def _normalize(name: str) -> str:
    return name.strip().lower().replace(" ", "").replace("-", "")


def get_supported_crops(model_data_path: str) -> dict[str, str]:
    table_dir = os.path.join(model_data_path, "climate_regression_yield_tables")
    if not os.path.isdir(table_dir):
        raise CSISError(
            f"model_data_path is invalid or missing climate_regression_yield_tables/: {model_data_path}",
            "INVALID_PARAMS",
        )
    pattern = os.path.join(table_dir, "*_regression_yield_table.csv")
    files = glob.glob(pattern)
    if not files:
        raise CSISError(f"No regression yield tables found in: {table_dir}", "INVALID_PARAMS")
    crops = {}
    for f in files:
        canonical = os.path.basename(f).replace("_regression_yield_table.csv", "")
        crops[_normalize(canonical)] = canonical
    return crops


def _rewrite_crop_csv(src_path: str, supported: dict[str, str], tmp_dir: str, filename: str) -> str:
    rows = []
    fieldnames = None
    unsupported = []
    with open(src_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            raw = row.get("crop_name", "").strip()
            normalized = _normalize(raw)
            if raw and normalized not in supported:
                unsupported.append(raw)
            else:
                row["crop_name"] = supported.get(normalized, raw)
            rows.append(row)
    if unsupported:
        raise CSISError(
            f"Unsupported crop(s): {', '.join(set(unsupported))}. "
            f"Supported: {', '.join(sorted(supported.values()))}. "
            f"For other crops, use Tool 5 (Crop Production Percentile) which supports 172 crops.",
            "INVALID_PARAMS",
        )
    out_path = os.path.join(tmp_dir, filename)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


async def run_crop_regression(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    from agent_runtime.telecoupling.config import settings
    model_data_path = params.get("model_data_path") or settings.MODEL_DATA_PATH

    # Validate fertilization_rate_table_path first for crop validation
    validate_required(params, ["fertilization_rate_table_path"])

    # Validate fertilization table crops before full param check
    fert_path_check = params["fertilization_rate_table_path"]
    if os.path.isfile(fert_path_check):
        supported_check = get_supported_crops(model_data_path)
        with open(fert_path_check, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if "crop_name" in (reader.fieldnames or []):
                unsupported = []
                for row in reader:
                    raw = row.get("crop_name", "").strip()
                    if raw and _normalize(raw) not in supported_check:
                        unsupported.append(raw)
                if unsupported:
                    raise CSISError(
                        f"Unsupported crop(s): {', '.join(set(unsupported))}. "
                        f"Supported: {', '.join(sorted(supported_check.values()))}. "
                        f"For other crops, use Tool 5 (Crop Production Percentile) which supports 172 crops.",
                        "INVALID_PARAMS",
                    )

    validate_required(params, REQUIRED_KEYS)
    supported = get_supported_crops(model_data_path)
    progress_callback(5, "Validated parameters")

    workspace_dir, folder_name = generate_output_dir("crop_production_regression", session_id)
    progress_callback(10, "Created output directory")

    progress_callback(15, "Normalizing crop names in input tables...")
    fert_path = _rewrite_crop_csv(
        params["fertilization_rate_table_path"],
        supported,
        workspace_dir,
        f"fertilization_normalized_{task_id}.csv",
    )
    lulc_crop_path = _rewrite_crop_csv(
        params["landcover_to_crop_table_path"],
        supported,
        workspace_dir,
        f"landcover_to_crop_normalized_{task_id}.csv",
    )

    invest_args = {
        "aggregate_polygon_path":        params.get("aggregate_polygon_path", ""),
        "fertilization_rate_table_path": fert_path,
        "landcover_raster_path":         params["landcover_raster_path"],
        "landcover_to_crop_table_path":  lulc_crop_path,
        "model_data_path":               model_data_path,
        "results_suffix":                params.get("results_suffix", ""),
        "workspace_dir":                 workspace_dir,
    }

    progress_callback(20, "Running InVEST Crop Production Regression model...")
    try:
        natcap.invest.crop_production_regression.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Crop Regression model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Generating previews...")
    files = await scan_output_directory(workspace_dir, "crop_production_regression")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
