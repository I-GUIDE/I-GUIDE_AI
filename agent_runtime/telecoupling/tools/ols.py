"""
Tool: Model Selection OLS — Ordinary Least Squares Regression with optional model search.

Accepts a CSV file. User specifies dependent and independent variable column names.
Optional model_selection mode tests all variable combinations and ranks by R².
"""
from __future__ import annotations
import logging
import os
import itertools
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from scipy import stats

from agent_runtime.telecoupling.shared.utils import CSISError, validate_required, generate_output_dir, scan_output_directory

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ["input_csv", "dependent_variable", "independent_variables"]


def _run_ols(y: np.ndarray, x: np.ndarray, var_labels: list[str]):
    """Core OLS computation. Returns a result dict."""
    n, k = x.shape
    dof = n - k
    if dof <= 2:
        raise CSISError(f"Too few observations ({n}) for {k} variables. Need at least {k+3}.",
                        "INVALID_PARAMS")

    xt = x.T
    xx = xt @ x
    try:
        xxi = np.linalg.inv(xx)
    except np.linalg.LinAlgError:
        raise CSISError("Perfect multicollinearity detected — cannot invert X'X.", "INVALID_PARAMS")

    xy = xt @ y
    coef = xxi @ xy

    yhat = x @ coef
    ybar = y.mean()
    e = y - yhat
    ess = float(e.T @ e)
    tss = float(((y - ybar) ** 2).sum())
    r2 = 1.0 - ess / tss
    r2_adj = 1.0 - (ess / dof) / (tss / (n - 1))
    s2 = ess / dof
    s2_mle = ess / n

    # Standard errors & t-stats
    var_beta = xxi * s2
    se_beta = np.sqrt(var_beta.diagonal())
    t_stat = (coef.flatten() / se_beta)
    p_vals = stats.t.sf(np.abs(t_stat), dof) * 2

    # White's robust SE
    u2 = e ** 2
    dof_scale = n / (n - k)
    s_hat = ((u2[:, np.newaxis] * x).T @ x) * dof_scale
    var_beta_rob = xxi @ s_hat @ xxi
    se_beta_rob = np.sqrt(var_beta_rob.diagonal())
    t_stat_rob = coef.flatten() / se_beta_rob
    p_vals_rob = stats.t.sf(np.abs(t_stat_rob), dof) * 2

    # Standardized coefficients
    y_std = y.std()
    x_std = x.std(0)
    coef_std = (x_std / y_std) * coef.flatten()

    # Jarque-Bera
    mu_e = e.mean()
    dev_e = e - mu_e
    u3 = (dev_e ** 3).mean()
    u4 = (dev_e ** 4).mean()
    skew = u3 / (s2_mle ** 1.5)
    kurt = u4 / (s2_mle ** 2)
    jb = (n / 6.0) * (skew ** 2 + (kurt - 3) ** 2 / 4.0)
    jb_p = float(stats.chi2.sf(jb, 2)) if jb >= 0 else float("nan")

    # Breusch-Pagan
    u2y = xt @ u2
    bp_coef = xxi @ u2y
    u2hat = x @ bp_coef
    eu = u2 - u2hat
    u2bar = u2.mean()
    r2u = 1.0 - float(eu.T @ eu) / float(((u2 - u2bar) ** 2).sum())
    bp = n * r2u
    bp_p = float(stats.chi2.sf(bp, k - 1)) if bp >= 0 else float("nan")

    # F-stat
    q = k - 1
    f_stat = (r2 / q) / ((1 - r2) / (n - k))
    f_p = float(stats.f.sf(f_stat, q, n - k))

    # Wald robust
    R = np.zeros((q, k))
    R[0:, 1:] = np.eye(q)
    Rb = R @ coef
    try:
        inv_rbr = np.linalg.inv(R @ var_beta_rob @ R.T)
        wald = float(Rb.T @ inv_rbr @ Rb)
        wald_p = float(stats.chi2.sf(wald, q))
    except np.linalg.LinAlgError:
        wald, wald_p = float("nan"), float("nan")

    # Log-likelihood, AIC, AICc
    log_lik = -(n / 2.0) * (1 + np.log(2 * np.pi)) - (n / 2.0) * np.log(s2_mle)
    k1 = k + 1
    aic = -2 * log_lik + 2 * k1
    aicc = -2 * log_lik + 2 * k1 * (n / (n - k1 - 1)) if n > k1 + 1 else float("nan")

    # VIF
    vif = None
    if k > 2:
        x_temp = x[:, 1:].T
        cor_x = np.corrcoef(x_temp)
        try:
            ic = np.linalg.inv(cor_x)
            vif = ic.diagonal().tolist()
        except np.linalg.LinAlgError:
            pass

    return {
        "n": n, "k": k, "dof": dof,
        "coef": coef.flatten().tolist(),
        "se_beta": se_beta.tolist(),
        "t_stat": t_stat.tolist(),
        "p_vals": p_vals.tolist(),
        "se_beta_rob": se_beta_rob.tolist(),
        "t_stat_rob": t_stat_rob.tolist(),
        "p_vals_rob": p_vals_rob.tolist(),
        "coef_std": coef_std.tolist(),
        "r2": r2, "r2_adj": r2_adj,
        "f_stat": f_stat, "f_p": f_p,
        "wald": wald, "wald_p": wald_p,
        "bp": bp, "bp_p": bp_p,
        "jb": jb, "jb_p": jb_p,
        "aic": aic, "aicc": aicc,
        "log_lik": log_lik,
        "yhat": yhat.flatten().tolist(),
        "residuals": e.flatten().tolist(),
        "std_residuals": (e.flatten() / np.sqrt(s2)).tolist(),
        "var_labels": var_labels,
        "vif": vif,
    }


async def run_ols(
    params: dict,
    session_id: str,
    task_id: str,
    progress_callback: Callable[[int, str], None],
) -> dict:
    validate_required(params, REQUIRED_KEYS)

    input_csv = params["input_csv"]
    dep_var = params["dependent_variable"].strip()
    ind_vars_raw = params["independent_variables"]
    if isinstance(ind_vars_raw, str):
        ind_vars = [v.strip() for v in ind_vars_raw.split(",") if v.strip()]
    else:
        ind_vars = [str(v).strip() for v in ind_vars_raw]

    model_selection = params.get("model_selection", False)
    min_r2 = float(params.get("min_r2", 0.5))
    max_vif = float(params.get("max_vif", 7.5))
    max_p_value = float(params.get("max_p_value", 0.05))

    if not os.path.exists(input_csv):
        raise CSISError(f"Input CSV not found: {input_csv}", "FILE_NOT_FOUND")

    progress_callback(5, "Reading data...")
    df = pd.read_csv(input_csv)

    missing = [c for c in [dep_var] + ind_vars if c not in df.columns]
    if missing:
        raise CSISError(f"Columns not found in CSV: {missing}", "INVALID_PARAMS")

    all_cols = [dep_var] + ind_vars
    df_clean = df[all_cols].dropna()
    if len(df_clean) < len(ind_vars) + 3:
        raise CSISError("Too few rows after removing NaN values.", "INVALID_PARAMS")

    workspace_dir, _ = generate_output_dir("ols", session_id)
    os.makedirs(workspace_dir, exist_ok=True)

    progress_callback(20, "Running OLS...")

    y = df_clean[dep_var].values.astype(float)
    x_raw = df_clean[ind_vars].values.astype(float)
    x = np.column_stack([np.ones(len(y)), x_raw])
    var_labels = ["Intercept"] + ind_vars

    result = _run_ols(y, x, var_labels)

    # --- Write coefficient CSV ---
    coef_rows = []
    for i, label in enumerate(var_labels):
        row = {
            "Variable": label,
            "Coefficient": round(result["coef"][i], 6),
            "Std_Error": round(result["se_beta"][i], 6),
            "t_Statistic": round(result["t_stat"][i], 4),
            "Prob": round(result["p_vals"][i], 4),
            "Robust_SE": round(result["se_beta_rob"][i], 6),
            "Robust_t": round(result["t_stat_rob"][i], 4),
            "Robust_Prob": round(result["p_vals_rob"][i], 4),
            "Std_Coefficient": round(result["coef_std"][i], 6),
        }
        if result["vif"] and i > 0:
            row["VIF"] = round(result["vif"][i - 1], 4)
        coef_rows.append(row)
    coef_csv = os.path.join(workspace_dir, "ols_coefficients.csv")
    pd.DataFrame(coef_rows).to_csv(coef_csv, index=False)

    # --- Write diagnostics CSV ---
    diag_rows = [
        {"Diagnostic": "Observations", "Value": result["n"]},
        {"Diagnostic": "R-Squared", "Value": round(result["r2"], 6)},
        {"Diagnostic": "Adjusted R-Squared", "Value": round(result["r2_adj"], 6)},
        {"Diagnostic": "AIC", "Value": round(result["aic"], 4)},
        {"Diagnostic": "AICc", "Value": round(result["aicc"], 4)},
        {"Diagnostic": "Log-Likelihood", "Value": round(result["log_lik"], 4)},
        {"Diagnostic": "F-Statistic", "Value": round(result["f_stat"], 4)},
        {"Diagnostic": "F-Prob", "Value": round(result["f_p"], 4)},
        {"Diagnostic": "Wald Statistic", "Value": round(result["wald"], 4)},
        {"Diagnostic": "Wald-Prob", "Value": round(result["wald_p"], 4)},
        {"Diagnostic": "Breusch-Pagan", "Value": round(result["bp"], 4)},
        {"Diagnostic": "BP-Prob", "Value": round(result["bp_p"], 4)},
        {"Diagnostic": "Jarque-Bera", "Value": round(result["jb"], 4)},
        {"Diagnostic": "JB-Prob", "Value": round(result["jb_p"], 4)},
    ]
    diag_csv = os.path.join(workspace_dir, "ols_diagnostics.csv")
    pd.DataFrame(diag_rows).to_csv(diag_csv, index=False)

    # --- Write residuals CSV ---
    res_df = df_clean[[dep_var]].copy()
    res_df["Estimated"] = result["yhat"]
    res_df["Residual"] = result["residuals"]
    res_df["Std_Residual"] = result["std_residuals"]
    res_csv = os.path.join(workspace_dir, "ols_residuals.csv")
    res_df.to_csv(res_csv, index=False)

    # --- Model selection mode ---
    if model_selection and len(ind_vars) > 1:
        progress_callback(50, "Running model selection across variable combinations...")
        _run_model_selection(y, df_clean, ind_vars, min_r2, max_vif, max_p_value, workspace_dir)

    progress_callback(90, "Scanning outputs...")
    files = await scan_output_directory(workspace_dir, "ols")
    return {"files": files}


def _run_model_selection(y, df_clean, ind_vars, min_r2, max_vif, max_p_value, workspace_dir):
    """Test all combinations of independent variables and save passing models."""
    rows = []
    for size in range(1, len(ind_vars) + 1):
        for combo in itertools.combinations(ind_vars, size):
            x_raw = df_clean[list(combo)].values.astype(float)
            x = np.column_stack([np.ones(len(y)), x_raw])
            try:
                r = _run_ols(y, x, ["Intercept"] + list(combo))
            except CSISError:
                continue

            if r["r2"] < min_r2:
                continue
            if max(r["p_vals"][1:]) > max_p_value:
                continue
            if r["vif"] and max(r["vif"]) > max_vif:
                continue

            rows.append({
                "Variables": ", ".join(combo),
                "R2": round(r["r2"], 4),
                "Adj_R2": round(r["r2_adj"], 4),
                "AICc": round(r["aicc"], 4),
                "F_Prob": round(r["f_p"], 4),
                "JB_Prob": round(r["jb_p"], 4),
                "BP_Prob": round(r["bp_p"], 4),
                "Max_VIF": round(max(r["vif"]), 2) if r["vif"] else "N/A",
            })

    if rows:
        out = pd.DataFrame(rows).sort_values("Adj_R2", ascending=False)
        out.to_csv(os.path.join(workspace_dir, "model_selection_results.csv"), index=False)
