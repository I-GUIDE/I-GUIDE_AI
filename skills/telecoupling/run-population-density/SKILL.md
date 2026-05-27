---
name: run-population-density
description: "Calculate population density per reporting unit and optionally compute population change between two time periods."
allowed-tools:
  - run_population_count_density
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Population Density

Workflow guidance for the `run_population_count_density` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- input_csv: path to the input CSV file containing population and area data
- population_field: column name in input_csv holding population count for the primary period
- area_km2_field: column name in input_csv holding the area of each unit in square kilometers

**Optional parameters**:
- unit_id_field: column name to use as a unique identifier for each spatial unit (e.g., district name, region code); uses row index if not provided
- second_period_csv: path to a second CSV file with population data for a comparison period (enables change analysis)
- second_period_population_field: column name in second_period_csv holding the population count for the second period (required if second_period_csv is provided)

**Key notes**:
- Population density is computed as: density = population_count / area_km2
- If second_period_csv is provided, the tool computes population change and percent change between the two periods
- Trigger words: population density, population count, demographic analysis, population change, per capita density, spatial demographics

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| population_density_results.csv | CSV | Per-unit results with population count, area, density (persons/km2), and change metrics if two periods are provided |
| population_density_summary.csv | CSV | Aggregate summary: total population, mean density, min/max density, and overall population change if applicable |

### Suggested next steps
- Review population_density_results.csv to identify the most and least densely populated units
- If two periods are provided, examine percent change to identify areas of rapid growth or decline
- Combine with food security analysis (run_food_security) to explore relationships between population growth and food access
- Combine with nutrition metrics (run_nutrition_metrics) to estimate aggregate caloric or nutrient demand by region
- Use unit density values to normalize other indicators (e.g., per-capita CO2 emissions, per-capita land use)
