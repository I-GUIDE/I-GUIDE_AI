---
name: run-nutrition-metrics
description: "Calculate Lower Limit Energy Requirements (LLER) by age group and sex using FAO nutritional formulas."
allowed-tools:
  - run_nutrition_metrics
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Nutrition Metrics

Workflow guidance for the `run_nutrition_metrics` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- population_csv: path to a CSV file where each row represents an age-sex demographic group with population count

**Optional parameters**:
- age_group_field: column name in population_csv holding the age group label (default: 'age_group')
  - Accepted values: '0-3', '3-10', '10-18', '18-30', '30-60', '60+'
- sex_field: column name in population_csv holding the sex label (default: 'sex')
  - Accepted values: 'male', 'female'
- population_count_field: column name in population_csv holding the count of individuals in each group (default: 'population')
- weight_kg_field: column name in population_csv holding the mean body weight in kg for each group (optional; uses built-in age-sex defaults if not provided)
- male_height_cm: default reference height in cm used for male groups when height is not in the CSV (default: 170)
- female_height_cm: default reference height in cm used for female groups when height is not in the CSV (default: 158)

**Key notes**:
- The tool computes per-group and population-weighted LLER (kcal/person/day) and total energy requirements
- Built-in energy requirement coefficients are applied per age-sex group following standard nutritional reference values
- Trigger words: nutrition metrics, LLER, energy requirements, caloric needs, nutrition by age, dietary energy demand, population nutrition

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| nutrition_metrics_results.csv | CSV | Per-group results: age group, sex, population count, mean weight, LLER (kcal/person/day), and total group energy requirement (kcal/day) |
| nutrition_metrics_summary.csv | CSV | Aggregate summary: total population, population-weighted average LLER, and total daily energy requirement across all groups |
| nutrition_ller_chart.png | Download | Bar chart of LLER by age-sex group, with bars sized proportionally to population count |

### Suggested next steps
- Review nutrition_metrics_results.csv to identify which age-sex groups have the highest per-capita energy requirements
- Use nutrition_metrics_summary.csv to estimate total daily caloric demand for the population (useful for food supply planning)
- Combine with food security analysis (run_food_security) to compare estimated dietary energy demand against available food supply indicators
- Combine with population density (run_population_count_density) to scale energy requirements to specific geographic areas
- Adjust male_height_cm and female_height_cm to reflect local anthropometric data for more accurate estimates
