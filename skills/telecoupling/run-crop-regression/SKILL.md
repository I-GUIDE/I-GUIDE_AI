---
name: run-crop-regression
description: "Run InVEST Crop Production Regression based on fertilizer NPK rates."
allowed-tools:
  - run_crop_production_regression
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Crop Regression

Workflow guidance for the `run_crop_production_regression` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: If landcover raster, crop table, fertilizer table, or aggregate shapefile have been uploaded, extract their paths directly — do not ask for them again. Only ask for genuinely missing inputs.

**Parameters to collect**:
- landcover_raster_path: land cover raster path
- landcover_to_crop_table_path: CSV mapping land class codes to crop names (columns: lucode, crop_name)
- fertilization_rate_table_path: fertilizer rates CSV with columns: crop_name, nitrogen_rate, phosphorus_rate, potassium_rate (all in kg/ha)
- aggregate_polygon_path: optional shapefile for aggregating yield statistics by polygon

**Supported crops (10 only)**:
barley, maize, oil palm, potato, rice, soybean, sugar beet, sugar cane, sunflower, wheat

**Key notes**:
- model_data_path is handled server-side — do NOT mention it to the user, do NOT ask for it, do NOT include it in any response
- Before collecting other parameters, confirm the crops the user wants to analyze are within the supported 10
- If a crop is not in the list (e.g. cassava, coffee), clearly inform the user it is not supported and recommend Tool 5 (172 crops)
- crop_name in fertilization_rate_table accepts variants: oil palm / oilpalm / Oil Palm are all recognized

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| result_table_*.csv | Table + chart | Area and production per crop under current fertilizer conditions |
| aggregate_results_*.csv | Table | Production aggregated by polygon boundaries (only if aggregate_polygon_path provided) |
| *_regression_production_*_preview.png | Preview image | Spatial yield distribution based on fertilizer regression |
| *_regression_production_*.tif | Download | Yield raster; unit: t/ha/year |

### Domain knowledge — understanding Crop Regression outputs

**How the regression model works — Liebig's Law of the Minimum**:
- The model calculates separate yield estimates for each of the three fertilizer inputs (nitrogen N, phosphorus P, potassium K) using regression parameters specific to each crop's climate bin
- **Final yield = pixel-wise minimum of the N-yield, P-yield, and K-yield rasters**
- This implements **Liebig's Law of the Minimum**: crop yield is limited by the scarcest essential nutrient. Applying more N will not increase yield if P is the limiting factor.
- Implication: if your regression yield is much lower than expected, check whether one nutrient is significantly under-applied relative to the others

**Fertilizer rates — what the inputs represent**:
- Rates are in **kg/ha** of actual nutrient (not fertilizer product weight)
- Typical global averages: maize ~120 kg N/ha, ~40 kg P/ha, ~50 kg K/ha; rice ~80 kg N/ha; wheat ~100 kg N/ha
- Setting a rate to 0 simulates organic or subsistence farming — the regression will return a low-input yield estimate
- Setting rates unrealistically high does NOT always increase yield — regression parameters include diminishing returns built into the climate bin coefficients

**Comparing regression vs percentile (Tool 5)**:
- If regression yield > 50th percentile (Tool 5): current fertilizer inputs are delivering above-average efficiency in this climate zone
- If regression yield < 25th percentile (Tool 5): fertilizer inputs are insufficient or poorly distributed — significant yield gap remains
- The comparison between the two tools is one of the most analytically valuable outputs of running both

**Scenario analysis — the fertilizer response curve**:
- Run the tool multiple times with different N/P/K rates (e.g. 0%, 50%, 100%, 150%, 200% of current rates)
- Plot total_production vs. fertilizer rate to visualize the response curve
- The curve typically shows rapid yield gains at low rates, then flattening (diminishing returns), then plateau — this inflection point is the economically optimal fertilizer rate
- This is directly applicable to fertilizer subsidy policy design and precision agriculture planning

**10 supported crops**:
- barley, maize, oil palm, potato, rice, soybean, sugar beet, sugar cane, sunflower, wheat
- These represent the world's major caloric staples and industrial crops
- For other crops (cassava, coffee, cocoa, vegetables, fruits), use Tool 5 (172 crops percentile model)

**Nutritional output**:
- Same 33 macro/micronutrient analysis as Tool 5 — enables comparison of caloric and protein output per hectare across crops and fertilizer scenarios

### Result interpretation
- Results reflect estimated yield under the current fertilizer rate inputs — changing rates changes outcomes
- Compared to Tool 5, output varies with fertilizer rates — better for evaluating fertilizer management scenarios
- Running with different fertilizer rates produces a fertilizer-yield response curve showing diminishing returns

### Suggested next steps
- Modify fertilizer rates in fertilization_rate_table and re-run to compare yield under different input scenarios
- Compare result_table with Tool 5 percentile results to evaluate current fertilizer efficiency vs. climate potential
- Identify the limiting nutrient (N/P/K) by comparing per-element intermediate rasters in intermediate_outputs/
- To analyze more crop types (beyond 10), use Tool 5 (Crop Production Percentile; 172 crops)
