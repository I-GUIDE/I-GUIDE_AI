---
name: run-model-selection-ols
description: "Run Ordinary Least Squares (OLS) regression on tabular CSV data."
allowed-tools:
  - run_model_selection_ols
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Model Selection Ols

Workflow guidance for the `run_model_selection_ols` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- input_csv: path to the input CSV file containing all variables for regression
- dependent_variable: column name of the response/outcome variable (Y)
- independent_variables: list of column names to use as predictors (X variables); comma-separated if provided as text

**Optional parameters**:
- model_selection: if true, runs automated stepwise model selection to find the best subset of independent variables (default: false)
- min_r2: minimum acceptable R-squared threshold during model selection (default: 0.5)
- max_vif: maximum acceptable Variance Inflation Factor for multicollinearity screening (default: 7.5)
- max_p_value: maximum acceptable p-value for predictor significance (default: 0.05)

**Key notes**:
- If model_selection is false (default), OLS is run on all provided independent_variables as-is
- If model_selection is true, the tool iterates over subsets of independent_variables and returns the best-fitting model satisfying min_r2, max_vif, and max_p_value thresholds
- Trigger words that indicate this tool: OLS, linear regression, model selection, R-squared, regression analysis, multicollinearity, VIF

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| ols_coefficients.csv | CSV | Regression coefficients, standard errors, t-statistics, and p-values for each predictor |
| ols_diagnostics.csv | CSV | Model-level diagnostics: R-squared, adjusted R-squared, F-statistic, AIC, BIC |
| ols_residuals.csv | CSV | Per-observation residuals and fitted values for diagnostic plotting |
| model_selection_results.csv | CSV | All candidate models evaluated during selection, ranked by R-squared (only if model_selection=true) |

### Suggested next steps
- Review ols_diagnostics.csv to assess overall model fit (R-squared, F-statistic)
- Check ols_coefficients.csv for statistically significant predictors (p-value < max_p_value)
- Plot residuals from ols_residuals.csv to check for heteroscedasticity or non-linearity
- If VIF values are high (> max_vif), consider removing correlated predictors or running with model_selection=true
- Use model_selection_results.csv to compare alternative model specifications when model_selection=true
