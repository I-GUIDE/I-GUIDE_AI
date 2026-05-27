---
name: run-ndr
description: "Run InVEST Nutrient Delivery Ratio (NDR) to estimate nitrogen and phosphorus export from watersheds."
allowed-tools:
  - run_ndr
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Ndr

Workflow guidance for the `run_ndr` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- dem_path: digital elevation model raster (meters, projected CRS)
- lulc_path: land use / land cover raster
- runoff_proxy_path: raster representing water runoff or precipitation (mm/year) — drives nutrient transport
- watersheds_path: watershed boundary shapefile (must have 'ws_id' integer field)
- biophysical_table_path: CSV with lucode and nutrient-specific columns — ask user which nutrients they need (N, P, or both), then confirm the table has the appropriate columns (load_n, eff_n, crit_len_n for nitrogen; load_p, eff_p, crit_len_p for phosphorus)
- threshold_flow_accumulation: integer defining stream network (e.g. 1000)

**Nutrient selection** (ask the user):
- calc_n: model nitrogen delivery (default True)
- calc_p: model phosphorus delivery (default False; requires P columns in biophysical table)
- At least one must be True

**Optional subsurface parameters** (use defaults unless user has data):
- subsurface_critical_length_n/p: travel length (m) for subsurface flow attenuation (default 150)
- subsurface_eff_n/p: subsurface nutrient removal efficiency (0–1; default 0.8)

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| n_export.tif | Download | Nitrogen export to streams per pixel (kg/ha/yr); present if calc_n=True |
| p_export.tif | Download | Phosphorus export to streams per pixel (kg/ha/yr); present if calc_p=True |
| n_retention.tif | Download | Nitrogen retained on landscape (kg/ha/yr) |
| p_retention.tif | Download | Phosphorus retained on landscape (kg/ha/yr) |
| watershed_results_ndr_n.shp | Download | Per-watershed nitrogen totals |
| watershed_results_ndr_p.shp | Download | Per-watershed phosphorus totals |
| watershed_results_ndr_n.csv | Table | Tabular nitrogen results per watershed |
| watershed_results_ndr_p.csv | Table | Tabular phosphorus results per watershed |

### Domain knowledge — understanding NDR outputs

**How nutrient delivery works**:
- Each pixel generates a nutrient load (from biophysical table: load_n/load_p in kg/ha/yr)
- As nutrient moves downslope toward the stream, vegetation on each pixel removes a fraction (efficiency eff_n/p)
- The NDR (Nutrient Delivery Ratio) is the fraction of the load that actually reaches the stream
- n_export = load × NDR — accounting for both surface and subsurface flow pathways

**n_export.tif / p_export.tif**:
- High values: high-load land uses (fertilized cropland, urban) with high connectivity to streams
- Low values: forests, wetlands, or upslope positions far from streams
- Hotspots near streams are most directly controlled by riparian buffer restoration

**n_retention / p_retention**:
- Shows where the landscape naturally filters nutrients before they reach waterways
- High retention in forested riparian zones — these areas provide the greatest water quality service
- Reducing this retention (e.g. by clearing riparian forest) directly increases downstream nutrient loads

**Watershed summary (CSV)**:
- total_load: total nutrient inputs to the watershed (no retention)
- total_export: nutrient reaching the outlet after landscape filtration
- total_retention_eff: watershed-level retention efficiency (%)
- A watershed with low efficiency is dominated by poorly buffered high-load areas

**Calibration**:
- Calibrate against observed stream nutrient concentrations at gauging stations
- Adjust load_n/load_p in the biophysical table if modeled exports are systematically high or low

### Suggested next steps
- Download n_export.tif to identify nutrient pollution hotspots
- Review watershed_results_ndr_n.csv to rank watersheds by nitrogen load for targeted intervention
- Install riparian buffers in high-export areas near streams to reduce downstream loads
- Combine with Annual Water Yield (run_annual_water_yield) for integrated water quantity + quality assessment
- Combine with SDR (run_sdr) for simultaneous sediment and nutrient management planning
