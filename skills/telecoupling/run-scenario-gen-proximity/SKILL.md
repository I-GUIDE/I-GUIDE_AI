---
name: run-scenario-gen-proximity
description: "Run InVEST Scenario Generator: Proximity-Based to generate LULC conversion scenarios."
allowed-tools:
  - run_scenario_gen_proximity
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Scenario Gen Proximity

Workflow guidance for the `run_scenario_gen_proximity` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- base_lulc_path: baseline land use / land cover raster (the map to modify)
- replacement_lucode: the LULC code of the new land cover type (e.g. 1 for forest if 1=forest in the legend)
- area_to_convert: total area to convert (hectares)
- focal_landcover_codes: space-separated list of LULC codes defining the 'edge' to convert near (e.g. "1 2" to convert land near forest and shrubland)
- convertible_landcover_codes: space-separated list of LULC codes that are eligible to be converted (e.g. "3 4" for cropland and grassland only)

**Conversion direction** (ask user which scenario):
- convert_nearest_to_edge: True → converts eligible pixels closest to the focal class boundary first (simulates edge expansion — e.g. deforestation encroaching on forest from the outside)
- convert_farthest_from_edge: True → converts pixels farthest from the focal class (simulates core area loss, or conversion of isolated patches)
- Both can be True simultaneously to generate two scenarios in one run

**Optional**:
- aoi_path: restrict conversion to a specific sub-region polygon
- n_steps: number of incremental scenario outputs (1 = single final scenario; >1 = intermediate steps useful for pathway analysis)

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| nearest_to_edge.tif | Download | Scenario LULC raster with pixels nearest focal edges converted; present if convert_nearest_to_edge=True |
| farthest_from_edge.tif | Download | Scenario LULC raster with pixels farthest from focal edges converted; present if convert_farthest_from_edge=True |
| nearest_to_edge_<step>.tif | Download | Incremental step outputs (if n_steps > 1) |
| farthest_from_edge_<step>.tif | Download | Incremental step outputs (if n_steps > 1) |

### Domain knowledge — understanding Scenario Generator outputs

**What proximity-based scenario generation does**:
- This tool modifies an existing LULC map by converting a specified area of eligible pixels to a new land cover type
- The spatial allocation of conversion is driven purely by proximity to the focal class edge — no economic or suitability model is involved
- The result is a 'what-if' future LULC scenario ready for use as input to other InVEST models

**nearest_to_edge scenario (edge expansion)**:
- Converts pixels that are geographically closest to the boundary of the focal class
- Simulates: deforestation from forest margins, urban sprawl expanding from existing urban edges, agricultural encroachment on forest borders
- Produces a compact, edge-following conversion pattern — realistic for many deforestation processes

**farthest_from_edge scenario (core area loss)**:
- Converts pixels that are deepest inside patches, farthest from any edge
- Simulates: selective logging in forest interiors, conversion of large isolated habitat blocks
- Results in fragmented patch structures with hollowed-out cores — tests impacts on interior-sensitive species

**Typical workflow**:
1. Generate a scenario LULC (nearest or farthest)
2. Use the output raster as lulc_fut_path in Carbon Storage, Habitat Quality, or Annual Water Yield
3. Compare future vs. baseline outputs to quantify ecosystem service change

**focal_landcover_codes vs convertible_landcover_codes**:
- Focal codes define WHERE conversion happens (near these classes)
- Convertible codes define WHAT gets converted (only these pixel types change)
- Example: focal="1" (forest), convertible="3 4" (cropland, grassland) → crops/grassland nearest to forest are converted to replacement_lucode — simulating agricultural expansion into forest margins

**n_steps > 1**:
- Generates multiple incremental scenario rasters showing the conversion pathway step by step
- Useful for analyzing at what point (how much area converted) ecosystem services cross a threshold

### Suggested next steps
- Download nearest_to_edge.tif or farthest_from_edge.tif and run Carbon Storage (run_carbon_storage) with it as lulc_fut_path to quantify carbon impact
- Run Habitat Quality (run_habitat_quality) with the scenario as lulc_fut_path to assess biodiversity change
- Compare multiple scenarios (near vs. far) to identify which conversion pattern causes more ecosystem service loss
- Use n_steps > 1 to identify tipping points in ecosystem service delivery as land conversion progresses
