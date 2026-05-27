---
name: run-cost-benefit-analysis
description: "Join cost and revenue data to a feature table and compute net returns (RETURNS = REVENUES - COSTS)."
allowed-tools:
  - run_cost_benefit_analysis
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Cost Benefit Analysis

Workflow guidance for the `run_cost_benefit_analysis` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- input_csv: path to the primary input CSV file (e.g., spatial or route data with unique IDs)
- economic_data_csv: path to a second CSV file containing cost and revenue data per unit/route
- key_field: column name used to join input_csv and economic_data_csv (must exist in both files)

**Optional parameters**:
- cost_field: column name in economic_data_csv that holds cost values (default: 'COSTS')
- revenue_field: column name in economic_data_csv that holds revenue values (default: 'REVENUES')

**Key notes**:
- The two CSVs are joined on key_field; rows present in input_csv but missing in economic_data_csv will have NaN costs/revenues
- RETURNS = REVENUES − COSTS is computed automatically for each joined record
- Trigger words: cost-benefit analysis, CBA, economic returns, costs revenues, return on investment, economic evaluation

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| cba_results.csv | CSV | Per-record results with COSTS, REVENUES, and RETURNS (= REVENUES − COSTS) columns merged from both input files |
| cba_summary.csv | CSV | Aggregate summary: total costs, total revenues, total returns, and overall benefit-cost ratio |

### Suggested next steps
- Review cba_results.csv to identify which units/routes are profitable (RETURNS > 0) vs. loss-making (RETURNS < 0)
- Use cba_summary.csv to report the overall economic viability of the project or program
- Combine with CO2 emissions analysis (run_co2_emissions) to evaluate economic vs. environmental trade-offs
- Filter cba_results.csv by RETURNS to prioritize high-return routes or activities for further investment
