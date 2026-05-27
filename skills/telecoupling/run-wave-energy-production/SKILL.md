---
name: run-wave-energy-production
description: "Run InVEST Wave Energy Production to estimate wave energy potential."
allowed-tools:
  - run_wave_energy_production
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Wave Energy Production

Workflow guidance for the `run_wave_energy_production` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Use this tool when the user asks about**:
- Ocean wave energy potential or resource mapping
- Siting wave energy conversion (WEC) facilities
- Renewable energy from ocean waves
- Trade-offs between wave energy and marine conservation/fisheries

**Required parameters**:
- analysis_area: select from the following options:
  - "Australia"
  - "East Coast of North America and Puerto Rico"
  - "Global"
  - "North Sea 10 meter resolution"
  - "North Sea 4 meter resolution"
  - "West Coast of North America and Hawaii"
- machine_perf_path: CSV performance matrix (Hs wave height × Tp wave period → power kW); sample data provided with InVEST
- machine_param_path: CSV of device parameters (CapMax, HsMax, TpMax); sample data provided

**Server-provided defaults — DO NOT ask the user for these paths**:
- wave_base_data_path: large pre-packaged WaveData directory (~811 MB). It is pre-installed on the server. Omit this parameter entirely unless the user explicitly supplies their own wave base data — the tool will use the built-in default and notify the user.
- bathymetry_path: global ocean bathymetry DEM. Pre-installed on the server. Omit this parameter unless the user explicitly supplies their own DEM — the tool uses the built-in default and notifies the user.

**Optional parameters**:
- aoi_vector_path: polygon to clip the analysis to a specific coastal region (recommended for large analysis areas to reduce runtime)
- do_valuation: set True to compute net present value (NPV) of a WEC facility; requires:
  - grid_points_path: CSV of electricity grid connection and land points (lat, long, type GRID/LAND, location name)
  - machine_econ_path: CSV of economic parameters (capital cost, O&M cost, electricity price, discount rate, etc.)
  - number_of_machines: number of WEC devices in the farm (default 28)

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| wp_kw.tif | Download | Wave power potential per pixel (kW/m of wave front) |
| capwe_mwh.tif | Download | Captured wave energy per WEC device per year (MWh/yr) |
| wp_rc.tif | Download | Wave power ranked by percentile (1=lowest, 5=highest) |
| capwe_rc.tif | Download | Captured energy ranked by percentile |
| npv_usd.tif | Download | Net present value per WEC device (USD); present if do_valuation=True |
| npv_rc.tif | Download | NPV ranked by percentile; present if do_valuation=True |
| capwe_mwh.csv | Table | Summary statistics of captured energy by AOI polygon |
| wp_kw.csv | Table | Summary statistics of wave power by AOI polygon |

### Domain knowledge — understanding Wave Energy outputs

**Wave power potential (wp_kw.tif)**:
- Measures the energy flux in the wave front — energy per unit width of wave crest
- Unit: kW per meter of wave front
- High values: exposed coasts with strong, consistent swell (e.g. western coasts at mid-latitudes)
- Low values: sheltered bays, enclosed seas, low-latitude regions with weak swell

**Captured wave energy (capwe_mwh.tif)**:
- Accounts for device limitations: the machine shuts down above HsMax (wave height) and TpMax (wave period) for safety
- More directly policy-relevant than raw wave power — shows realistic annual energy yield
- Unit: MWh per device per year; multiply by number_of_machines for total farm output

**Ranked maps (wp_rc, capwe_rc)**:
- Classify pixels into 5 bins by percentile within the study area
- Useful for relative siting comparisons without needing to interpret absolute values
- High percentile = best candidate sites for WEC installation

**Net present value (npv_usd.tif)**:
- Economic value of building and operating a WEC farm over its lifetime at each location
- Accounts for: capital cost, O&M costs, electricity revenue, cable/mooring costs (distance-dependent via bathymetry)
- Positive NPV = profitable site; negative NPV = not economically viable under given parameters
- Sensitivity: strongly influenced by electricity price (p) and discount rate (r) in machine_econ_path

**Analysis area selection guidance**:
- 'Global' covers the entire ocean but has coarser data — use for initial screening only
- Regional datasets (e.g. 'North Sea 4 meter resolution') have higher spatial resolution
- Always provide aoi_vector_path to clip large analysis areas and reduce runtime

**Device parameterization**:
- The default device specs (CapMax=750 kW, HsMax=10 m, TpMax=20 sec) represent a generic WEC
- Real devices vary substantially — consult device manufacturer datasheets for accurate performance tables

### Suggested next steps
- Download wp_rc.tif to identify high-potential wave energy zones in the study area
- Download capwe_mwh.tif to estimate actual harvestable energy at candidate sites
- If valuation was run, download npv_usd.tif to identify economically viable locations
- Overlay results with marine protected areas, shipping lanes, or fishing grounds to assess siting conflicts
- Compare with Offshore Wind Energy (run_offshore_wind_energy) to assess complementarity of marine renewable resources
