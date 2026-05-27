---
name: run-urban-mental-health
description: "Run Urban Mental Health model (extended Urban Nature Access) to estimate mental health benefits from urban greenspace."
allowed-tools:
  - run_urban_mental_health
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Urban Mental Health

Workflow guidance for the `run_urban_mental_health` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Use this tool when the user asks about**:
- Mental health benefits of urban nature
- Psychological wellbeing from nearby green spaces
- Nature visible or encountered in daily routines (not just "can I reach a park")
- Stress reduction, restorative experience, or mental health equity from urban greenery

**Do NOT use this tool for**: measuring whether residents can physically travel to a park for recreation — use run_urban_nature_access for that.

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- lulc_raster_path: land use / land cover raster
- lulc_attribute_table: CSV mapping LULC codes to nature classification (columns: lucode, urban_nature 0/1)
- population_raster_path: population count raster (people per pixel)
- admin_boundaries_vector_path: administrative unit polygon shapefile for zonal reporting

**Search radius** (mental health context uses small radii — nature you can see or pass by):
- search_radius: distance in meters within which nature contributes to mental health exposure
  - Typical range: **100–300 m** (visible from window or reachable on foot in minutes)
  - Default: **300 m** (5-minute walk — nature reliably encountered in daily movement)
  - Ask the user if they want a different radius (e.g. 100 m for "nature visible from home")

**Optional parameters**:
- search_radius_mode: default 'uniform radius'; alternatives: 'radius per population group', 'radius per urban nature class'
- decay_function: how benefit declines with distance (default 'gaussian'; options: 'exponential', 'linear', 'dichotomy')
- population_group_radii_table: required only if search_radius_mode = 'radius per population group'

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| accessible_urban_nature.tif | Download | Nature area exposed to each pixel (m²) within the search radius |
| urban_nature_supply_percapita.tif | Download | Per-capita nature exposure (m²/person) |
| urban_nature_balance_percapita.tif | Download | Exposure surplus/deficit vs. a per-capita standard (m²/person) |
| urban_nature_balance_totalpop.tif | Download | Total population-weighted balance per pixel |
| admin_boundaries.gpkg | Download | Per-administrative unit mental health exposure summary |
| admin_boundaries.csv | Table | Tabular results per admin unit |

### Domain knowledge — understanding Urban Mental Health outputs

**Mental health exposure vs. physical access**:
- Urban Nature Access (run_urban_nature_access) asks: "Can you travel to a park?"
- Urban Mental Health (this tool) asks: "How much nature do you encounter in your immediate environment?"
- The key difference is radius: 300 m captures nature you see through windows, pass on walks, or encounter in your neighborhood — the scale associated with restorative stress reduction in the literature

**Evidence base**:
- Research consistently shows that even brief exposure to nature (views, sounds, nearby greenery) reduces stress hormones (cortisol), improves mood, and reduces symptoms of anxiety and depression
- The 300 m scale aligns with studies on daily nature contact and mental health outcomes (Gascon et al. 2015, Marselle et al. 2020)
- Green views from home windows are associated with improved wellbeing even without physical access

**urban_nature_balance_percapita.tif**:
- Positive: residents have sufficient nearby nature for mental health benefits
- Negative: nature deficit — associated with higher mental health burden, particularly in dense urban cores
- Use this layer to identify mental health equity hotspots: areas where low-income or vulnerable residents face both nature deficits and elevated health risks

**Admin boundary results (CSV)**:
- Fraction of population with adequate nearby nature exposure per district
- Useful for mental health policy reports and equity assessments
- Combine with socioeconomic data to identify environmental justice priorities

**Comparing to Urban Nature Access**:
- Run both tools with the same inputs but different search_radius (300 m vs 1000 m)
- Areas that have good scores on nature access but poor scores on mental health are served by distant parks, not nearby greenery — these need pocket parks or street trees, not large parks
- Areas poor on both need comprehensive green infrastructure investment

### Suggested next steps
- Download urban_nature_balance_percapita.tif to map mental health nature equity across the city
- Review admin_boundaries.csv to rank districts by nature deficit for mental health policy prioritization
- Compare results with run_urban_nature_access (1000 m) to distinguish "accessible parks" vs "everyday nature contact"
- Overlay with Urban Cooling (run_urban_cooling) results to identify areas where green infrastructure can simultaneously improve mental health and reduce heat stress
