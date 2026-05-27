---
name: run-recreation-tourism
description: "Run InVEST Visitation: Recreation and Tourism (recmodel_client) to estimate photo-user-days (PUD/TUD) for an area of interest, optionally with regression against local predictors."
allowed-tools:
  - run_recreation_tourism
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Recreation Tourism

Workflow guidance for the `run_recreation_tourism` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Use this tool when the user asks about**:
- Recreation or tourism value of natural areas
- Which parts of a landscape attract the most outdoor visitors
- What environmental features (forests, beaches, trails) drive recreational visits
- How habitat loss or land-use change would affect tourism and recreation
- Photo-user-days (PUD) or proxy visitation mapping

**Important note to user**: This tool connects to NatCap's remote server to fetch
historical Flickr/Twitter photo location data (2005–2017). It requires internet
access from the server. Results may take several minutes for large study areas.

**Required parameters**:
- aoi_path: polygon shapefile of the study area — must be in a projected coordinate system (not WGS84 lat/lon); the AOI defines where the model counts photo-user-days
- start_year: start of analysis period (integer, 2005–2017)
- end_year: end of analysis period (integer, 2005–2017, must be ≥ start_year); for best results use 2012–2017 (highest Flickr coverage)

**Optional parameters**:
- grid_aoi: set True to subdivide the AOI into a regular grid of cells — strongly recommended for polygon AOIs covering large areas; without gridding, results are aggregated per polygon feature
  - grid_type: 'hexagon' (recommended, avoids edge artifacts) or 'square'
  - cell_size: size of each grid cell in AOI projection units (e.g. meters if UTM); start large (10,000–50,000 m) to check data density, then refine
- compute_regression: set True to run a linear regression explaining visitation using local environmental predictor variables; requires:
  - predictor_table_path: CSV with columns id, path, type — maps predictor names to spatial files (vectors or rasters) and their metric type (point_count, raster_mean, polygon_percent_coverage, etc.)
- scenario_predictor_table_path: same format as predictor_table_path but for a future/alternative scenario — model predicts how visitation would change

**Cell size guidance**:
- Too small → most cells have zero photos → regression is unreliable
- Start at 10–50 km for national/regional scale
- Go down to 1–5 km only for local studies with dense photo coverage

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| pud_results.shp / .gpkg | Download | AOI polygons/grid cells with PUD_YR_AVG and monthly PUD attributes |
| tud_results.shp / .gpkg | Download | Same but Twitter-user-days (TUD) |
| PUD_monthly_table.csv | Table | Total photo-user-days per cell per month across the date range |
| TUD_monthly_table.csv | Table | Twitter-user-days per cell per month |
| regression_data.gpkg | Download | Grid with predictor values and avg_pr_UD (if compute_regression=True) |
| regression_coefficients.csv | Table | β coefficients for each predictor variable |
| regression_summary.txt | Download | Full regression output including server ID hash for reproducibility |
| scenario_results.gpkg | Download | Predicted visitation under alternative scenario (if scenario table provided) |

### Domain knowledge — understanding Recreation outputs

**Photo-user-days (PUD)**:
- PUD_YR_AVG = average number of unique Flickr users who took photos per year in each cell
- This is a *proxy* for visitation — areas with more photos = more visitors
- PUD is most reliable in areas with high Flickr uptake (North America, Europe, Australia)
- In regions with low Flickr use (parts of Africa, Southeast Asia), PUD underestimates real visitation

**Interpreting spatial patterns**:
- High PUD cells: popular recreation hotspots — beaches, viewpoints, iconic wildlife sites
- Low PUD cells: remote or less-visited areas — not necessarily low ecological value
- Seasonal patterns (PUD_JAN, PUD_FEB, …): shows when visitors come — crucial for tourism planning

**Regression coefficients (if compute_regression=True)**:
- Positive β: feature *attracts* visitors (e.g. proximity to beach, forest cover)
- Negative β: feature *deters* visitors (e.g. distance from road)
- Use to rank which environmental features matter most for recreation value

**Scenario results**:
- pr_UD_est = predicted proportion of visits under the new scenario
- Compare against regression_data.gpkg to see where visitation would increase or decrease
- Use to quantify the recreational cost of habitat loss or the benefit of restoration

**Limitations**:
- Data is 2005–2017 only — does not reflect post-COVID or recent tourism trends
- Flickr use has declined since ~2015 — later years may have lower photo counts
- Model captures day-trip and international tourism but misses local recreation by non-Flickr users

### Suggested next steps
- Download pud_results.gpkg and visualize in QGIS to see recreation hotspots
- Review PUD_monthly_table.csv to identify peak visitor seasons
- If regression was run, review regression_coefficients.csv to rank which habitat features drive visitation
- Run a scenario with reduced habitat (e.g. deforested patches) to quantify recreation value at risk
- Overlay with Habitat Quality (run_habitat_quality) to identify areas with both high biodiversity and high recreation value
