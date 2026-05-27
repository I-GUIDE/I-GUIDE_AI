---
name: run-urban-stormwater
description: "Run InVEST Urban Stormwater Retention model to estimate runoff retention and recharge."
allowed-tools:
  - run_urban_stormwater_retention
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Urban Stormwater

Workflow guidance for the `run_urban_stormwater_retention` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- lulc_path: land use / land cover raster
- soil_group_path: SCS hydrological soil group raster (A=1, B=2, C=3, D=4)
- precipitation_path: annual precipitation raster (mm/year)
- biophysical_table: CSV with retention and infiltration coefficients per LULC × soil group combination (RC = stormwater retention coefficient, IR = infiltration ratio, ET = evapotranspiration coefficient)

**Optional parameters**:
- adjust_retention_ratios: set True to account for road drainage effects on adjacent pixels (improves accuracy in urban grids)
  - retention_radius: radius (meters) within which roads reduce retention of nearby pixels (required if adjust_retention_ratios=True)
  - road_centerlines_path: road network shapefile for adjustment
- aggregate_areas_path: polygon shapefile (e.g. neighborhoods, parcels) for aggregated zonal statistics
- replacement_cost: cost ($/m³) of providing equivalent retention by grey infrastructure — enables economic valuation of retention service

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| retention_ratio.tif | Download | Fraction of annual precipitation retained per pixel (0–1) |
| retention_volume.tif | Download | Annual stormwater retention volume per pixel (m³/yr) |
| infiltration_ratio.tif | Download | Fraction of precipitation infiltrating to groundwater |
| infiltration_volume.tif | Download | Annual infiltration volume per pixel (m³/yr) |
| ET_volume.tif | Download | Annual evapotranspiration volume per pixel (m³/yr) |
| retention_value.tif | Download | Economic value of retention service ($/yr); present if replacement_cost provided |
| aggregate_results.shp | Download | Zonal aggregation by area polygons (if aggregate_areas_path provided) |
| aggregate_results.csv | Table | Tabular zonal results |

### Domain knowledge — understanding Urban Stormwater outputs

**What stormwater retention measures**:
- Retention = total annual precipitation minus stormwater runoff (not discharged to the drainage system)
- High retention: parks, forests, wetlands, permeable pavements, green roofs
- Low retention: impervious surfaces (rooftops, parking lots) with direct drainage connections

**retention_ratio.tif**:
- 0 = all precipitation runs off (fully impervious, no storage)
- 1 = all precipitation retained (fully permeable, no runoff)
- Typical urban values: 0.1–0.4 for dense built areas; 0.6–0.9 for parks and forests

**retention_volume.tif**:
- Total annual volume of stormwater managed by each pixel (m³/yr)
- High-volume pixels are your most productive natural stormwater infrastructure
- Sum across a neighborhood = total stormwater services provided to the drainage system

**Infiltration vs. evapotranspiration split**:
- infiltration_volume: water recharging groundwater — benefits baseflow and aquifer levels
- ET_volume: water returning to atmosphere — does not recharge groundwater but reduces surface runoff
- The split matters for groundwater-dependent ecosystems and aquifer management

**Retention value (economic)**:
- replacement_cost × retention_volume gives the cost of providing the same service with grey infrastructure (e.g. storage tanks, constructed wetlands)
- Typical replacement costs: $0.50–5.00/m³ depending on region and infrastructure type
- Valuation supports arguments for green infrastructure financing and ecosystem service payments

**Road drainage adjustment**:
- In urban areas, roads drain adjacent land directly to the sewer — reducing effective retention of nearby permeable surfaces
- enable adjust_retention_ratios=True for urban-scale analyses where road drainage is significant

### Suggested next steps
- Download retention_volume.tif to map stormwater management hotspots
- If replacement_cost was provided, download retention_value.tif for economic mapping
- Review aggregate_results.csv to compare stormwater performance across neighborhoods or land use zones
- Combine with Urban Flood Risk Mitigation (run_urban_flood) for complementary flood volume analysis
- Target low-retention zones (high impervious cover) for green infrastructure retrofit prioritization
