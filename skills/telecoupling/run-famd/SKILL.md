---
name: run-famd
description: "Run Factor Analysis for Mixed Data (FAMD), PCA, or MCA via R/FactoMineR."
allowed-tools:
  - run_factor_analysis_mixed_data
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Famd

Workflow guidance for the `run_factor_analysis_mixed_data` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- input_csv: path to the input CSV file containing variables for analysis

**At least one of the following must be provided**:
- quantitative_variables: list of column names for continuous/numeric variables (comma-separated)
- qualitative_variables: list of column names for categorical/factor variables (comma-separated)

**Optional parameters**:
- n_components: number of dimensions/components to extract and retain (default: 5)
- handle_na: if true, rows with missing values are handled automatically before analysis (default: true)

**Key notes**:
- If only quantitative_variables are given, the tool runs PCA
- If only qualitative_variables are given, the tool runs MCA
- If both are given, the tool runs FAMD (the general case that handles mixed data types)
- Trigger words that indicate this tool: factor analysis, FAMD, PCA, MCA, dimensionality reduction, mixed data, principal component, correspondence analysis

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| famd_plots.pdf | Download | Multi-page PDF with scree plot, individual map, variable correlation circle, and biplot |
| famd_eigenvalues.csv | CSV | Eigenvalues and percentage of variance explained by each component/dimension |
| famd_individual_coordinates.csv | CSV | Coordinates (scores) of each observation on the retained dimensions |

### Suggested next steps
- Open famd_plots.pdf to inspect the scree plot and decide if n_components should be adjusted
- Review famd_eigenvalues.csv to determine how many components explain a satisfactory cumulative variance (e.g., >70%)
- Use famd_individual_coordinates.csv to cluster observations, color them by group, or use as input for further analysis (e.g., regression, OLS)
- Compare individual positions on Dim 1 vs Dim 2 in the biplot to interpret which variables drive the most variation
- If dimensionality is reduced for regression, use the component coordinates as new predictors with run_model_selection_ols
