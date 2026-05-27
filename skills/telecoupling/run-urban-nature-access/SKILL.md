---
name: run-urban-nature-access
description: "Run InVEST Urban Nature Access to estimate population access to urban green space."
allowed-tools:
  - run_urban_nature_access
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Urban Nature Access

Workflow guidance for the `run_urban_nature_access` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- lulc_raster_path: land use / land cover raster
- lulc_attribute_table: CSV mapping LULC codes to nature classification (must include lucode and urban_nature columns; urban_nature=1 means that class counts as accessible nature)
- population_raster_path: population raster (people per pixel) — typically derived from census or WorldPop data
- admin_boundaries_vector_path: polygon shapefile of administrative units (e.g. districts, neighborhoods) for aggregated results

**Search radius mode** (ask user which fits their analysis):
- 'uniform radius' (default): all people use the same search distance — requires search_radius in meters (e.g. 300 m for walkable access, 1000 m for cycling)
- 'radius per population group': different population groups have different mobility — requires population_group_radii_table CSV
- 'radius per urban nature class': different park sizes have different catchment radii — radii encoded in lulc_attribute_table

**decay_function**: controls how access decreases with distance. Valid values (accept any of these):
- 'gaussian' (default): smooth, realistic decay — best for walking behavior
- 'linear': uniform linear decay
- 'exponential': rapid decay with distance
- 'dichotomy': binary — all nature within radius counted equally (classic buffer approach)

**Optional parameters**:
- urban_nature_demand: per-capita nature area standard in m² (e.g. 250 for WHO recommended 9 m²/person × some factor); used to compute supply-demand balance; default if omitted is no demand threshold
- aggregate_by_pop_group: true/false — if true, aggregate results separately for each population group defined in population_group_radii_table (only relevant when search_radius_mode='radius per population group')

**IMPORTANT**: Call the tool as soon as all required parameters are provided. Do NOT ask for optional parameters unless the user explicitly mentions them.

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| urban_nature_supply.tif | Download | Nature area accessible to each pixel (m²) within the search radius |
| urban_nature_demand.tif | Download | Nature area needed by population at each pixel (m²) |
| urban_nature_balance.tif | Download | Supply minus demand (m²); positive = surplus, negative = deficit |
| urban_nature_balance_percapita.tif | Download | Per-capita nature balance (m²/person) |
| admin_boundaries_results.shp | Download | Per-administrative unit nature access summary |
| admin_boundaries_results.csv | Table | Tabular nature access statistics per admin unit |

### Domain knowledge — understanding Urban Nature Access outputs

**What nature access measures**:
- The model asks: for each person in the city, how much urban nature (parks, forests, green spaces) is within walking/cycling distance?
- 'Supply' = total nature area reachable weighted by distance decay
- 'Demand' = nature needed to meet a per-capita standard (e.g. WHO recommends 9 m²/person minimum)

**urban_nature_balance.tif**:
- Positive values: areas where green space supply exceeds local population demand — well-served
- Negative values: nature deficits — people here lack adequate accessible green space
- This is the key equity metric: identifies underserved neighborhoods that need new parks or improved access

**urban_nature_balance_percapita.tif**:
- Normalizes by population density — useful for comparing equity across areas with different density
- High-density neighborhoods often show the largest deficits even if some parks exist nearby

**Decay function effects**:
- 'gaussian': smooth decline with distance — reflects realistic walking behavior (most use nearby parks, fewer use distant ones)
- 'linear': uniform linear decay — simpler assumption
- 'dichotomy': binary within/outside radius — all nature within radius counted equally (classic buffer approach)

**Admin boundary aggregation (CSV)**:
- Reports total population with and without adequate access per administrative unit
- Fraction underserved = policy-relevant indicator for green infrastructure equity reports
- Use to rank districts/neighborhoods by nature access deficit for prioritization

**Search radius calibration**:
- Walkable access: 300–500 m (5-minute walk)
- Cycling access: 1000–2000 m (10-minute bike)
- Motorized access: 3000–5000 m
- Match to local transportation behavior and park type (pocket park vs. regional park)

### Suggested next steps
- Download urban_nature_balance.tif to map green space equity across the city
- Review admin_boundaries_results.csv to rank districts by nature access deficit
- Identify new park locations in high-deficit areas to maximize equity improvement
- Combine with Urban Cooling (run_urban_cooling) to prioritize green investments that serve both cooling and access goals
- Overlay with socioeconomic data (income, age) from GIS to assess environmental justice dimensions
