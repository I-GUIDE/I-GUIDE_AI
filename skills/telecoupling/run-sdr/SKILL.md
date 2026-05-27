---
name: run-sdr
description: "Run InVEST Sediment Delivery Ratio (SDR) to estimate soil erosion and sediment export."
allowed-tools:
  - run_Sediment_Delivery_Ratio_SDR
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Sdr

Workflow guidance for the `run_Sediment_Delivery_Ratio_SDR` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- dem_path: digital elevation model raster (meters, projected CRS)
- erosivity_path: rainfall erosivity raster (R factor; MJ·mm/ha·h·yr) — derived from precipitation intensity data
- erodibility_path: soil erodibility raster (K factor; t·ha·h/ha·MJ·mm) — from soil texture/organic matter maps
- lulc_path: land use / land cover raster
- watersheds_path: watershed boundary shapefile (must have 'ws_id' integer field)
- biophysical_table_path: CSV with columns lucode (int), usle_c (cover-management factor 0–1), usle_p (support practice factor 0–1)
- threshold_flow_accumulation: integer defining minimum upstream pixels to be a stream (e.g. 1000)

**Optional calibration parameters** (use defaults unless user has calibration data):
- k_param: Borselli k, controls connectivity index (default 2)
- sdr_max: maximum fraction of eroded sediment that can be delivered (default 0.8)
- ic_0_param: calibration offset (default 0.5)
- l_max: maximum USLE slope length in meters (default 122)
- drainage_path: raster of roads/ditches as artificial drainage channels

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| sed_export.tif | Download | Sediment exported to streams per pixel (t/ha/yr) |
| usle.tif | Download | Gross soil erosion per pixel (t/ha/yr; before delivery) |
| rkls.tif | Download | RKLS raster (erosion potential without cover/practice management) |
| sed_retention.tif | Download | Sediment retained on the landscape (t/ha/yr) |
| sed_retention_index.tif | Download | Relative sediment retention value (higher = more valuable for water quality) |
| watershed_results_sdr.shp | Download | Per-watershed summary: total sed_export, usle, retention (tonnes/year) |
| watershed_results_sdr.csv | Table | Tabular watershed summary |

### Domain knowledge — understanding SDR outputs

**The USLE → SDR → sed_export workflow**:
1. usle.tif = R × K × LS × C × P — gross erosion potential at each pixel
2. The SDR (Sediment Delivery Ratio) adjusts for connectivity: sediment eroded upslope may be deposited before reaching the stream
3. sed_export = usle × SDR — only the fraction that actually reaches the stream network

**usle.tif**:
- High values: steep slopes, highly erodible soils, bare land (C=1, P=1)
- Low values: forests, grasslands with high C×P reduction, gentle terrain
- Represents potential erosion regardless of whether sediment reaches a stream

**sed_export.tif**:
- The policy-relevant output — shows actual sediment delivery to waterways
- Hotspots indicate areas where erosion AND connectivity both are high (steep land near streams)
- Reducing sed_export requires either reducing erosion (revegetation) or reducing connectivity (buffer strips, check dams)

**sed_retention_index.tif**:
- Identifies which landscape positions provide the most sediment trapping service
- High values near streams: these areas filter sediment before it enters the channel — high priority for conservation
- Use this layer to site riparian buffers or conservation easements for maximum water quality benefit

**Watershed summary (CSV)**:
- sed_export (tonnes/year): total sediment load reaching the watershed outlet
- Compare across watersheds to prioritize intervention areas
- Divide by watershed area to get mean export density (t/ha/yr) for cross-watershed comparisons

**Calibration guidance**:
- If observed suspended sediment data are available at a gauge, calibrate by adjusting k_param and sdr_max
- Typical k_param range: 0.5–5; sdr_max range: 0.6–0.95

### Suggested next steps
- Download sed_export.tif to map sediment delivery hotspots
- Review watershed_results_sdr.csv to prioritize watersheds for erosion control
- Download sed_retention_index.tif to identify where to site riparian buffers for maximum impact
- Combine with NDR (run_ndr) to simultaneously analyze sediment and nutrient pollution
