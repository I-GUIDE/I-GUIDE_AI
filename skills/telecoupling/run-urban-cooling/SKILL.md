---
name: run-urban-cooling
description: "Run InVEST Urban Cooling Island model to estimate urban heat mitigation from green spaces."
allowed-tools:
  - run_urban_cooling
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Urban Cooling

Workflow guidance for the `run_urban_cooling` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- lulc_raster_path: land use / land cover raster for the urban area
- ref_eto_raster_path: reference evapotranspiration raster (mm/day) — typically derived from weather station or gridded climate data
- aoi_vector_path: polygon defining the study area boundary
- biophysical_table_path: CSV mapping land cover classes to cooling properties (columns: lucode, kc crop coefficient, shade 0–1, albedo 0–1, green_area 0/1)
- green_area_cooling_distance: distance (meters) over which green areas cool surrounding air (typically 100–1000 m)
- t_ref: reference rural temperature (°C) — temperature in a nearby rural/non-urban area on a hot summer day
- uhi_max: maximum urban heat island intensity (°C) — difference between the hottest urban pixel and the rural reference

**Optional parameters**:
- cc_method: 'factors' (default; uses shade/albedo/ETI weights) or 'intensity' (uses vegetation intensity directly)
- building_vector_path + energy_consumption_table_path: required only if user wants energy cost savings from cooling
- avg_rel_humidity: average relative humidity (%) for calculating wet bulb temperature
- cc_weight_shade / cc_weight_albedo / cc_weight_eti: customize cooling credit weights for cc_method='factors' (must sum to 1.0)

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| hm.tif | Download | Heat mitigation index (0–1; higher = more cooling provided by that pixel) |
| uhi_results_weighted_avg.tif | Download | Air temperature (°C) after urban heat island correction |
| energy_sav.shp | Download | Energy savings per building (kWh/yr); present if building/energy inputs provided |
| buildings_with_stats.shp | Download | Building-level temperature and energy summary |

### Domain knowledge — understanding Urban Cooling outputs

**Urban Heat Island (UHI) effect**:
- Urban areas are warmer than surrounding rural areas due to dark impervious surfaces, lack of vegetation, and waste heat
- The magnitude of the UHI (uhi_max) typically ranges from 1–5°C in most cities, up to 10°C in dense tropical cities
- Green infrastructure (parks, street trees, green roofs) mitigates UHI through shade, evapotranspiration, and albedo

**Heat mitigation index (hm.tif)**:
- 0 = no cooling contribution (bare impervious surface)
- 1 = maximum cooling (e.g., large park with high shade, ETI, and albedo)
- The model blends three cooling mechanisms weighted by cc_weight parameters
- Use this to identify which land cover classes contribute most to urban cooling

**Air temperature map (uhi_results_weighted_avg.tif)**:
- Modeled air temperature (°C) on a representative hot day
- Pixels near large parks are cooler; dense built-up areas without greenery are hottest
- Useful for identifying heat vulnerability zones and equity analysis (are cooling areas near vulnerable populations?)

**Cooling mechanism weights (cc_method='factors')**:
- shade (default weight 0.6): tree canopy shade reduces surface heating — largest contributor
- albedo (0.2): reflective surfaces reduce absorbed solar radiation
- eti (0.2): evapotranspiration cools air through latent heat — especially important for irrigated greenery

**Energy savings valuation**:
- Cooler air temperatures reduce air conditioning demand
- energy_sav.shp reports estimated kWh/year savings per building from UHI mitigation by urban greenery
- Requires building footprints and an energy consumption table linking building type to cooling demand

**Key parameters to calibrate**:
- t_ref and uhi_max should come from local meteorological data — they strongly control output magnitude
- green_area_cooling_distance can be estimated from literature (typically 1–3× the park radius)

### Suggested next steps
- Download uhi_results_weighted_avg.tif to map temperature hotspots in the city
- Download hm.tif to identify which green areas provide the most cooling service
- Overlay temperature maps with population density to assess heat equity
- Combine with Urban Stormwater (run_urban_stormwater) for integrated green infrastructure planning
