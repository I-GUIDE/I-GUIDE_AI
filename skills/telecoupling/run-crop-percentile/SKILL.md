---
name: run-crop-percentile
description: "Run InVEST Crop Production Percentile model for up to 172 crops based on climate percentiles."
allowed-tools:
  - run_crop_production_percentile
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Crop Percentile

Workflow guidance for the `run_crop_production_percentile` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: If landcover raster, crop table, or aggregate shapefile have been uploaded, extract their paths directly — do not ask for them again. Only ask for genuinely missing inputs.

**Parameters to collect**:
- landcover_raster_path: land cover raster path (pixel values = land class codes)
- landcover_to_crop_table_path: CSV mapping land class codes to crop names (columns: lucode, crop_name)
- aggregate_polygon_path: optional shapefile for aggregating yield statistics by polygon

**Key notes**:
- model_data_path is handled server-side — do NOT mention it to the user, do NOT ask for it, do NOT include it in any response
- Supports 172 crops; if unsure whether a specific crop is supported, it will be validated at runtime
- Difference from Tool 6: this tool uses climate percentile estimates, no fertilizer data needed, suitable for broad multi-crop surveys; Tool 6 uses fertilizer regression but only supports 10 crops

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| result_table_*.csv | Table + chart | Total area and production per crop across the study area |
| aggregate_results_*.csv | Table | Production aggregated by polygon boundaries (only if aggregate_polygon_path provided) |
| *_yield_*percentile_*_preview.png | Preview image | Spatial yield distribution per crop at each climate percentile |
| *_yield_*percentile_*.tif | Download | Yield raster; unit: t/ha/year |

### Domain knowledge — understanding Crop Percentile outputs

**What percentiles mean — this is not probability, it's intensification level**:
- The model draws on the **Monfreda et al. (2008) global dataset** of observed crop yields circa year 2000, organized by climate bins (temperature × precipitation zones)
- **25th percentile**: Low-intensity farming — typical of subsistence agriculture, degraded soils, or minimal inputs. Reflects the bottom quarter of observed yields globally within that climate bin.
- **50th percentile**: Average yield — roughly what most farmers achieve under conventional management in that climate zone.
- **75th percentile**: Above-average management — improved varieties, moderate inputs, reasonable extension services.
- **95th percentile**: Near-optimal yield — best achievable with current technology in that climate zone. Represents the yield gap target for intensification programs.
- **The yield gap** = 95th percentile − current observed yield. A large gap means the region is significantly underperforming its climate potential — an opportunity for intensification without expanding cropland.

**Climate bins — why geography matters for yield**:
- Each crop has a unique set of climate bins based on temperature and precipitation zones
- Yields are benchmarked within each bin — so tropical rice is compared to other tropical rice areas, not to temperate wheat regions
- This means a "high percentile" for a given pixel reflects performance relative to other farms in a similar climate, not globally

**Observed yield column — use it as a sanity check**:
- The result_table includes both percentile estimates and observed production (from FAO/sub-national data ~year 2000)
- If your study area's actual production is near the 25th percentile, there is large room to improve; if it's near the 95th, intensification is already high
- Large differences between observed and 50th percentile may reflect data quality or land use classification issues

**Nutritional output — 33 macro and micronutrients**:
- The result_table also reports nutritional content (calories, protein, fat, vitamins, minerals) derived from crop_nutrient.csv
- Useful for food security analysis: comparing caloric production per hectare across crop types, or assessing micronutrient availability from the landscape

**172 crops coverage**:
- This tool covers 172 crops globally — far more than the Regression model (10 crops only)
- Suitable for multi-crop landscapes, biodiversity-rich agroforestry systems, or any analysis where you need broad crop coverage
- If you need to evaluate fertilizer management for the 10 major staple crops, use Tool 6 (Crop Regression) instead

### Result interpretation
- Higher percentile = higher yield target — not a probability statement, but an intensification scenario
- total_production in result_table is the estimated total production within the study area at each percentile
- Spatial maps reveal geographic patterns of high-yield and low-yield zones within the study area

### Suggested next steps
- Download result_table_*.csv to compare yields and nutritional output across crop types
- Compare 25th vs 95th percentile production to quantify the yield gap and identify intensification opportunities
- To analyze fertilizer effects on yield (10 major crops only), use Tool 6 (Crop Regression)
- Combine with Tool 4 (Seasonal Water Yield) to analyze agricultural water pressure and food-water trade-offs
