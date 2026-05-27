---
name: run-seasonal-water-yield
description: "Run InVEST Seasonal Water Yield model to estimate quickflow, baseflow, and local recharge."
allowed-tools:
  - run_seasonal_water_yield
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Seasonal Water Yield

Workflow guidance for the `run_seasonal_water_yield` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: If any spatial inputs (shapefiles, rasters, CSVs) or directories have been uploaded or their paths provided, extract them directly — do not ask again. Only ask for genuinely missing inputs.

**Parameters to collect**:
- aoi_path: watershed area of interest shapefile path
- lulc_raster_path: land use / land cover raster path
- dem_raster_path: Digital Elevation Model raster path
- soil_group_path: hydrologic soil group raster path (classes A/B/C/D)
- biophysical_table_path: biophysical parameters CSV path
- precip_dir: **directory** containing 12 monthly precipitation rasters (not a single file)
- et0_dir: **directory** containing 12 monthly ET0 rasters (not a single file)
- rain_events_table_path: rain events CSV path (columns: month, events)
- threshold_flow_accumulation: integer, default 1000 — only ask if user wants to change
- Optional (defaults provided, only ask if user wants to adjust): alpha_m=0.083333, beta_i=1.0, gamma=1.0

**Key notes**:
- precip_dir and et0_dir must be directory paths, not individual file paths

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| QF_*_preview.png | Preview image | Quickflow (surface runoff) spatial distribution |
| QF_*.tif | Download | Quickflow raster; unit: mm/year |
| B_*_preview.png | Preview image | Baseflow (groundwater recharge contribution) spatial distribution |
| B_*.tif | Download | Baseflow raster; unit: mm/year |
| L_avail_*_preview.png | Preview image | Available local recharge spatial distribution |
| L_*_preview.png | Preview image | Local recharge spatial distribution |
| P_*_preview.png | Preview image | Precipitation spatial distribution |
| aggregated_results_swy_*_preview.png | Preview image | Sub-watershed aggregated results map |
| aggregated_results_swy_*.shp | Download | Sub-watershed summary shapefile with hydrological statistics |

### Domain knowledge — understanding SWY outputs

**Three core water fluxes — what they mean**:

- **Quickflow (QF)**: Surface runoff generated during or shortly after rain events. High QF = water that bypasses the soil and drains rapidly to streams. This is unproductive water — it does not recharge groundwater, it contributes to flood peaks, and it carries soil erosion. Urban, bare, or compacted soils generate high QF.

- **Baseflow (B)**: The slow, sustained release of stored water to streams between rain events. This is the water that keeps rivers flowing during dry seasons and supports agriculture, drinking water supply, and aquatic ecosystems. High B areas = landscapes with good infiltration and deep-rooted vegetation that retains and slowly releases water.

- **Local recharge (L)**: Precipitation minus quickflow minus evapotranspiration. Positive L = the pixel is producing water available for downslope use and groundwater recharge. Negative L = the pixel consumes more water than it receives (common in high-ET crops or dense canopy in dry conditions).

**How to interpret the spatial patterns**:
- Dense forest / vegetated uplands typically show **high B, low QF** — they are the water towers of a watershed
- Urban areas / bare land show **high QF, low B** — they shed water quickly and contribute to downstream flood risk
- Irrigated agriculture can show **negative L** — it extracts more water than falls as rain
- Watershed headwaters with high B are critical for downstream dry-season water security — protecting these areas preserves water supply

**NRCS Curve Number (CN) method — why soil type matters**:
- The model uses the NRCS CN method to estimate quickflow, combining LULC class and soil hydrologic group (A/B/C/D)
- Soil Group A (sandy, high infiltration) = low CN → low QF; Soil Group D (clay, low infiltration) = high CN → high QF
- Vegetation on Group D soils generates much more runoff than on Group A soils — the soil group map is critical input quality

**Aggregated shapefile — key fields**:
- `qb`: mean baseflow index per sub-watershed (mm) — direct indicator of dry-season water supply potential
- `vri_sum`: total recharge contribution per sub-watershed (mm) — measures each watershed's contribution to downstream recharge

**Scenario analysis — how to use this tool for policy**:
- Replace forests with agriculture in the LULC raster → re-run → observe QF increase and B decrease
- This quantifies the hydrological cost of land use change in mm of water and directly informs water fund and watershed PES scheme design

### Suggested next steps
- Download aggregated_results_swy_*.shp and overlay with land use maps in GIS to identify priority conservation sub-watersheds
- Modify the LULC raster to simulate land use change scenarios (deforestation, reforestation, urban expansion) and re-run
- Combine with Tool 5 or Tool 6 (Crop Production) to analyze agricultural water trade-offs and food-water nexus
