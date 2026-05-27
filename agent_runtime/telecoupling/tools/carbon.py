"""
Tool: Carbon Storage and Sequestration (natcap.invest.carbon).

Minimum required: lulc_cur_path + carbon_pools_path.
Optional: lulc_fut_path triggers sequestration; lulc_redd_path triggers REDD scenario;
do_valuation requires lulc_cur_year, lulc_fut_year, price_per_metric_ton_of_c,
discount_rate, rate_change.
"""
from __future__ import annotations
import logging
from typing import Callable

import natcap.invest.carbon

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["lulc_cur_path", "carbon_pools_path"]


async def run_carbon(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)
    progress_callback(5, "Validated parameters")

    workspace_dir, _ = generate_output_dir("carbon", session_id)
    progress_callback(10, "Created output directory")

    has_fut = bool(params.get("lulc_fut_path"))
    has_redd = bool(params.get("lulc_redd_path"))
    do_valuation = bool(params.get("do_valuation", False))

    invest_args = {
        "workspace_dir":        workspace_dir,
        "results_suffix":       params.get("results_suffix", ""),
        "lulc_cur_path":        params["lulc_cur_path"],
        "carbon_pools_path":    params["carbon_pools_path"],
        "calc_sequestration":   has_fut or do_valuation,
        "lulc_fut_path":        params.get("lulc_fut_path", ""),
        "do_redd":              has_redd,
        "lulc_redd_path":       params.get("lulc_redd_path", ""),
        "do_valuation":         do_valuation,
    }

    if do_valuation:
        for key in ["lulc_cur_year", "lulc_fut_year", "price_per_metric_ton_of_c",
                    "discount_rate", "rate_change"]:
            if params.get(key) is None:
                raise CSISError(
                    f"do_valuation=true requires '{key}'", "INVALID_PARAMS"
                )
        invest_args.update({
            "lulc_cur_year":             params["lulc_cur_year"],
            "lulc_fut_year":             params["lulc_fut_year"],
            "price_per_metric_ton_of_c": params["price_per_metric_ton_of_c"],
            "discount_rate":             params["discount_rate"],
            "rate_change":               params["rate_change"],
        })

    progress_callback(20, "Running InVEST Carbon Storage model...")
    try:
        natcap.invest.carbon.execute(invest_args)
    except Exception as e:
        raise CSISError(f"Carbon model failed: {e}", "TOOL_FAILED")
    progress_callback(85, "InVEST model completed")

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "carbon")
    progress_callback(100, "Done")

    return {"success": True, "files": files}
