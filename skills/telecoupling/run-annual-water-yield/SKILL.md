---
name: run-annual-water-yield
description: "Run InVEST Annual Water Yield (Budyko curve) to estimate water yield per watershed."
allowed-tools:
  - run_annual_water_yield
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Annual Water Yield

Workflow guidance for the `run_annual_water_yield` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- lulc_path: land use / land cover raster (pixel values = land class codes)
- depth_to_root_rest_layer_path: raster of depth to root-restricting soil layer (mm)
- precipitation_path: mean annual precipitation raster (mm/year)
- pawc_path: plant available water content raster (fraction, 0–1)
- eto_path: reference evapotranspiration raster (mm/year)
- watersheds_path: watershed boundary shapefile (must have 'ws_id' integer field)
- biophysical_table_path: CSV with columns lucode (int), root_depth (mm), Kc (crop coefficient)

**Optional parameters**:
- seasonality_constant: Zhang Z parameter controlling seasonal water partitioning (default 15; range 10–30; higher = more precipitation becomes runoff)
- sub_watersheds_path: sub-watershed shapefile for finer spatial aggregation
- demand_table_path: CSV of water demand by land use (for water scarcity analysis)
- valuation_table_path: CSV for hydropower economic valuation

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| per_pixel_wyield.tif | Download | Annual water yield per pixel (mm) |
| per_pixel_aet.tif | Download | Actual evapotranspiration per pixel (mm) |
| watershed_results_wyield.shp | Download | Summary statistics per watershed (volume m³, mean mm) |
| sub_watershed_results_wyield.shp | Download | Sub-watershed summary (if sub_watersheds_path provided) |
| watershed_results_wyield.csv | Table | Tabular watershed summary |

### Domain knowledge — understanding Annual Water Yield outputs

**Water balance framework**:
- The model uses the Budyko curve to partition precipitation into evapotranspiration and water yield
- Water yield = Precipitation − Actual Evapotranspiration (AET)
- AET depends on potential ET (via Kc), rooting depth, plant available water content, and precipitation

**per_pixel_wyield.tif**:
- High values in wet, low-evapotranspiration areas (e.g. cloud forests, riparian zones)
- Low values in arid areas or dense vegetation with high water demand
- Unit is mm per pixel — multiply by pixel area (m²) and divide by 1000 to get m³/pixel

**Watershed summary (CSV)**:
- vol: total annual water yield volume (m³) for the watershed — useful for reservoir sizing or downstream availability
- mn_wyield: mean annual yield (mm) — comparable across watersheds
- Compare against demand_table outputs to assess water scarcity or surplus

**Effect of land use change on water yield**:
- Deforestation typically increases water yield (less transpiration) but degrades water quality and seasonal regulation
- Reforestation reduces total yield but stabilizes seasonal flows and reduces flooding risk
- The model captures quantity but not timing — pair with Seasonal Water Yield for flow seasonality

**Zhang Z parameter (seasonality_constant)**:
- Controls the shape of the Budyko curve — higher Z = more runoff per unit precipitation
- Default 15 works for most humid tropical/temperate landscapes
- Calibrate against observed streamflow if gauging data are available

### Suggested next steps
- Download per_pixel_wyield.tif to map water supply hotspots
- Review watershed_results_wyield.csv to compare total yield across watersheds
- Run Seasonal Water Yield (run_seasonal_water_yield) to analyze monthly flow patterns
- Combine with NDR (run_ndr) to assess nutrient loading alongside water delivery
