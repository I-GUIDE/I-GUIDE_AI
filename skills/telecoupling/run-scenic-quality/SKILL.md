---
name: run-scenic-quality
description: "Run InVEST Scenic Quality to compute viewshed visibility from structure points."
allowed-tools:
  - run_scenic_quality
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Scenic Quality

Workflow guidance for the `run_scenic_quality` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Use this tool when the user asks about**:
- Visual impact of offshore or nearshore development (wind turbines, aquaculture, wave energy facilities)
- Viewshed analysis — which areas can see a proposed structure
- Scenic amenity mapping for coastal or marine planning
- Property value impacts from visual intrusion of infrastructure

**Required parameters**:
- aoi_vector_path: polygon defining the study area (coastline zone of interest)
- structure_vector_path: point shapefile of structures whose visual impact is being assessed (e.g. wind turbines, offshore platforms) — must share the same projection as the DEM; optional fields in shapefile: `radius` (max viewing distance m), `weight` (importance coefficient), `height` (structure height above ground m)
- dem_path: digital elevation model (meters, land and bathymetry merged)

**Optional parameters**:
- refractivity_coefficient: default 0.13 (standard atmospheric refraction); adjust only with specific atmospheric data
- do_valuation: set True to compute a visual impact value index; requires:
  - valuation_function: 'linear', 'logarithmic', or 'exponential' (describes how impact decreases with distance)
  - a_coef, b_coef: function parameters (see InVEST documentation for guidance)
  - max_valuation_radius: maximum distance from structure to include in valuation (meters)

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| vshed.tif | Download | Binary viewshed — 1 = can see at least one structure, 0 = not visible |
| vshed_qual.tif | Download | Weighted viewshed quality score (higher = more visual impact from structures) |
| vshed_stats.csv | Table | Summary statistics of viewshed by AOI polygon |

### Domain knowledge — understanding Scenic Quality outputs

**What the viewshed represents**:
- vshed.tif shows every pixel within the AOI that has a direct line of sight to at least one structure
- The model computes a separate viewshed for each structure point and combines them, weighted by the structure's `weight` field (if provided)
- vshed_qual.tif shows cumulative visual impact — areas that can see multiple high-weight structures have higher scores

**Interpreting vshed_qual**:
- High values: strongly impacted scenic areas — visible from many or heavy structures
- Low values / 0: visually shielded by terrain or beyond viewing range
- The quality score is relative within the study area, not absolute — compare pixels within the run, not across different runs

**Refractivity coefficient**:
- The default 0.13 accounts for the fact that light bends slightly downward through the atmosphere, allowing observers to see slightly further than pure geometric line-of-sight
- A value of 0 means no atmospheric correction (pure geometric viewshed); values up to ~0.2 represent different atmospheric conditions

**Valuation output** (if do_valuation=True):
- The valuation function maps visual impact to a damage score as a function of distance from the structure
- 'exponential' decay is most common for scenic amenity studies (nearby = high impact, far = near-zero)
- a_coef controls the magnitude; b_coef controls the rate of decay with distance

**Common use cases**:
- Offshore wind siting: identify areas where turbines would be visible from populated coastlines
- Aquaculture permitting: assess visual footprint of net-pen facilities on coastal tourism areas
- Conservation planning: protect views of natural seascapes by excluding development from viewshed zones

### Suggested next steps
- Download vshed.tif to map the visual impact zone of proposed structures
- Overlay vshed_qual.tif with population or tourism density to quantify how many people are affected
- Use the output in stakeholder engagement to show community-visible vs. screened siting alternatives
- If do_valuation was used, compare vshed_stats.csv across alternative siting scenarios
