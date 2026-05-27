---
name: run-food-security
description: "Analyze FAO food security indicators for selected countries and generate trend charts."
allowed-tools:
  - run_food_security
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Food Security

Workflow guidance for the `run_food_security` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- fao_csv: path to a CSV file downloaded from FAOSTAT or formatted in the same structure (long format with country, year, and indicator value columns)
- countries: list of country names to include in the analysis (comma-separated); must match values in the country_field column
- indicator_field: the specific FAO indicator to analyze (e.g., "Prevalence of undernourishment", "Average dietary energy supply adequacy")

**Optional parameters**:
- country_field: column name in fao_csv that holds country/area names (default: 'Area')
- year_field: column name in fao_csv that holds the year (default: 'Year')
- value_field: column name in fao_csv that holds the indicator values (default: 'Value')
- unit_field: column name in fao_csv that holds the measurement unit (optional; used for axis labeling in charts)

**Key notes**:
- The tool filters fao_csv to the specified countries and indicator_field, then produces trend charts and a comparison chart
- Trigger words: food security, FAO indicators, undernourishment, food availability, food access, FAOSTAT, dietary energy, food insecurity

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| food_security_data.csv | CSV | Filtered and cleaned data for the selected countries and indicator across all available years |
| food_security_pivot.csv | CSV | Wide-format pivot table with years as rows and countries as columns for easy comparison |
| food_security_trend.png | Download | Line chart showing the indicator trend over time for each selected country |
| food_security_comparison.png | Download | Bar chart comparing the most recent available value across all selected countries |

### Suggested next steps
- Review food_security_trend.png to identify countries with improving or worsening food security over time
- Use food_security_comparison.png to rank countries by their latest indicator value
- Change indicator_field to explore different dimensions of food security (availability, access, utilization)
- Combine with population density analysis (run_population_count_density) to contextualize food security relative to population pressure
- Use food_security_pivot.csv as input for regression analysis (run_model_selection_ols) to identify predictors of food insecurity
