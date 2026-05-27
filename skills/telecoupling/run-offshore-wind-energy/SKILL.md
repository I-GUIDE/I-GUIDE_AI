---
name: run-offshore-wind-energy
description: "Run InVEST Offshore Wind Energy to estimate offshore wind power potential."
allowed-tools:
  - run_offshore_wind_energy
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Offshore Wind Energy

Workflow guidance for the `run_offshore_wind_energy` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Use this tool when the user asks about**:
- Offshore wind energy potential or resource mapping
- Siting offshore wind farms within a maritime zone (EEZ)
- Energy density and harvestable energy from wind over the ocean
- Trade-offs between offshore wind energy and marine conservation or fisheries

**Required parameters**:
- wind_data_path: CSV of wind speed at hub height with columns: longitude, latitude, wind speed (m/s) percentiles — WEBPAR format; bundled with InVEST sample data under WindEnergy/input/
- aoi_vector_path: polygon of the wind farm planning area (typically an EEZ or sub-region)
- turbine_parameters_path: CSV of turbine specifications — InVEST includes 3_6_turbine.csv (3.6 MW) and 5_0_turbine.csv (5 MW) under WindEnergy/input/
- number_of_turbines: total number of turbines in the wind farm (used for calculating total farm energy and carbon offset)
- global_wind_parameters_path: global model parameters CSV (loss factors, capacity factors) bundled under WindEnergy/input/global_wind_energy_parameters.csv

**Server-provided defaults — DO NOT ask the user for these paths**:
- bathymetry_path: global ocean bathymetry DEM (~112 MB). Pre-installed on the server. Omit this parameter unless the user explicitly supplies their own DEM — the tool uses the built-in default and notifies the user.
- land_polygon_vector_path: global land polygon (~155 MB). Pre-installed on the server. Omit this parameter unless the user explicitly supplies their own land polygon — the tool uses the built-in default and notifies the user.

**Optional parameters**:
- min_depth / max_depth: depth range for viable turbine installation (default 3–60 m for fixed-bottom foundations)
- min_distance / max_distance: distance-to-shore constraints in meters (default 0–200,000 m)
- avg_grid_distance: average distance from turbine to nearest grid connection point (km; affects cable cost if valuation is run)
- valuation_container: set True to compute net present value — requires additional economic parameters

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| wind_energy_points.shp | Download | Point vector of viable turbine locations with energy density and harvest estimates |
| harvested_energy_MWhr_per_yr.tif | Download | Annual harvestable energy per pixel (MWh/yr per turbine) |
| carbon_emissions_tons.tif | Download | Carbon emissions offset per pixel (tons CO₂/yr) by replacing fossil fuel generation |
| npv_usd.tif | Download | Net present value per pixel (USD); present if valuation_container=True |

### Domain knowledge — understanding Offshore Wind Energy outputs

**Wind energy density (Dens_W/m2 field in wind_energy_points)**:
- Energy flux per unit rotor-swept area (W/m²) at hub height
- High values: exposed open-ocean sites with consistent, strong winds (offshore from mid-latitude coasts)
- Low values: sheltered inland seas, low-latitude regions, nearshore areas with topographic wind shadow

**Harvestable energy (Harv_MWhr field / harvested_energy_MWhr_per_yr.tif)**:
- Accounts for turbine power curve — energy actually converted over a year
- More policy-relevant than raw wind density — shows realistic annual yield per turbine
- Multiply by number_of_turbines for total farm output
- Typical offshore turbine (3.6 MW): 12,000–16,000 MWh/yr in a good site

**Depth and distance filtering**:
- Pixels outside min_depth–max_depth and min_distance–max_distance are masked
- Fixed-bottom foundations: cost-effective to ~60 m; floating foundations extend to 200 m+ (update max_depth accordingly)
- Minimum distance from shore (~5–10 km) is common for visual impact and shipping safety

**Carbon emissions offset (carbon_emissions_tons.tif)**:
- Estimates CO₂ avoided per year by displacing fossil-fuel generation at each location
- Useful for quantifying co-benefits of offshore wind alongside energy production

**Net present value (if valuation_container=True)**:
- Accounts for capital, O&M, cable, and decommissioning costs vs electricity revenue
- Positive NPV = economically viable site under given price and discount parameters
- Strongly sensitive to electricity price and discount rate — run sensitivity scenarios

**Turbine selection**:
- 3.6 MW (3_6_turbine.csv): common reference turbine for regulatory analysis
- 5.0 MW (5_0_turbine.csv): better represents current offshore deployments
- Use manufacturer datasheets for project-specific analysis

### Suggested next steps
- Download harvested_energy_MWhr_per_yr.tif to identify highest-yield areas in the study zone
- Download wind_energy_points.shp and filter by Harv_MWhr threshold to shortlist candidate sites
- Overlay with shipping lanes, marine protected areas, and fishing grounds to check siting conflicts
- Compare with Wave Energy (run_wave_energy_production) to evaluate complementarity of marine renewable resources
- If valuation was run, download npv_usd.tif to identify economically viable zones
