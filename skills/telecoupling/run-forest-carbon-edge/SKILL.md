---
name: run-forest-carbon-edge
description: "Run InVEST Forest Carbon Edge Effect to estimate above-ground carbon in tropical forests accounting for edge effects."
allowed-tools:
  - run_forest_carbon_edge_effect
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Forest Carbon Edge

Workflow guidance for the `run_forest_carbon_edge_effect` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- lulc_raster_path: land use / land cover raster (pixel values = land class codes)
- biophysical_table_path: CSV with columns lucode (int), is_tropical_forest (0/1), c_above (Mg C/ha — used only for non-forest classes; forest edge effect overrides forest pixels)
- tropical_forest_edge_carbon_model_vector_path: the global regression model vector shipped with InVEST — ask the user to locate it in their InVEST installation (typically `natcap/invest/forest_carbon_edge_effect/core_data/`)

**Optional parameters**:
- aoi_vector_path: area of interest polygon to clip the analysis
- pools_to_calculate: 'all' (above + below + soil + dead) or 'above_ground' only (faster)
- compute_forest_edge_effects: default True; set False to use only biophysical table values (ignores edge proximity)
- n_nearest_model_points: default 10; controls interpolation smoothness (higher = smoother but slower)
- biomass_to_carbon_conversion_factor: default 0.47 (IPCC standard); adjust if using a region-specific factor

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| carbon_map.tif | Download | Total carbon stock per pixel (Mg C/pixel), incorporating edge effect for forest |
| aggregated_carbon_stocks.shp | Download | Carbon totals aggregated to AOI (if aoi_vector_path provided) |

### Domain knowledge — understanding Forest Carbon Edge outputs

**Why forest edges matter for carbon**:
- Tropical forest carbon stocks decline significantly near forest edges due to higher tree mortality, microclimate changes, and wind exposure
- Edge effects can reduce carbon stocks by 20–50% within 1–2 km of the forest boundary
- This tool applies a spatially explicit edge-effect correction based on a global regression model fitted from pantropical biomass plots

**How the edge effect is computed**:
- The model calculates each forest pixel's distance to the nearest non-forest edge
- It then applies a regression equation (from Chaplin-Kramer et al. or the bundled model) relating distance-to-edge to above-ground biomass
- Pixels far from edges retain full interior carbon values; pixels near edges receive reduced carbon estimates

**carbon_map.tif interpretation**:
- Non-forest pixels: carbon from biophysical table (c_above + below + soil + dead)
- Forest interior pixels (far from edge): high carbon density
- Forest edge pixels: lower carbon density — the key output of this model
- Unit: Mg C per pixel; pixel area must be known to convert to per-ha density

**When to use this model vs. basic Carbon Storage**:
- Use Carbon Storage (run_carbon_storage) for multi-scenario comparisons with tabular carbon pools
- Use Forest Carbon Edge when spatial accuracy of forest carbon matters (e.g. REDD+ MRV, carbon credit verification)
- Edge effects are most significant in heavily fragmented tropical forest landscapes

**pools_to_calculate='above_ground' vs 'all'**:
- 'above_ground': faster, uses only AGB regression — suitable for remote sensing validation or when soil data are unavailable
- 'all': more complete accounting, necessary for full carbon budget analysis

### Suggested next steps
- Download carbon_map.tif to identify forest interior carbon hotspots vs. degraded edge zones
- Compare with Carbon Storage (run_carbon_storage) output to quantify how much edge effect reduces total landscape carbon
- Overlay with Habitat Quality to identify patches with co-benefits for carbon and biodiversity
