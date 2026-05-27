---
name: run-carbon-storage
description: "Run InVEST Carbon Storage and Sequestration."
allowed-tools:
  - run_carbon_storage
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Carbon Storage

Workflow guidance for the `run_carbon_storage` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Minimum required parameters**:
- lulc_cur_path: current land use / land cover raster (pixel values = land class codes)
- carbon_pools_path: CSV mapping land class codes to carbon pool densities (columns: lucode, c_above, c_below, c_soil, c_dead; unit: Mg C/ha)

**Optional — scenario analysis** (ask only if user mentions future scenarios or REDD):
- lulc_fut_path: future LULC raster → enables carbon sequestration output (delta_cur_fut)
- lulc_redd_path: REDD/alternative future LULC raster → enables REDD scenario comparison

**Optional — economic valuation** (ask only if user wants monetary values):
- do_valuation: set true to compute NPV of sequestration
- lulc_cur_year: year of current LULC map (e.g. 2010)
- lulc_fut_year: year of future LULC map (e.g. 2030)
- price_per_metric_ton_of_c: carbon price (USD per Mg C)
- discount_rate: annual discount rate as percentage (e.g. 5 for 5%)
- rate_change: annual rate of change in carbon price as percentage

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| tot_c_cur.tif | Download | Total carbon stock (current LULC); unit: Mg C/pixel |
| tot_c_fut.tif | Download | Total carbon stock (future LULC); present if lulc_fut_path provided |
| delta_cur_fut.tif | Download | Carbon sequestration (future − current); positive = gain, negative = loss |
| tot_c_redd.tif | Download | Total carbon stock (REDD scenario); present if lulc_redd_path provided |
| delta_cur_redd.tif | Download | Carbon change under REDD vs. current |
| npv_fut.tif | Download | Net present value of sequestration (USD); present if do_valuation=true |

### Domain knowledge — understanding Carbon Storage outputs

**What tot_c_cur represents**:
- Sums four carbon pools per pixel: above-ground biomass (c_above), below-ground biomass (c_below), soil organic carbon (c_soil), and dead organic matter (c_dead)
- Unit is Mg C per pixel; multiply by pixel area in ha to get per-hectare density
- High-carbon areas typically include old-growth forests, peatlands, and wetlands

**Interpreting delta_cur_fut (sequestration map)**:
- Positive values: land converted to higher-carbon cover (e.g. cropland → forest) = carbon sink
- Negative values: carbon loss (e.g. deforestation) = carbon source / emission
- Use this layer to identify which land transitions drive the largest carbon fluxes

**REDD scenario interpretation**:
- Compares an alternative conservation scenario (lulc_redd_path) against the baseline future
- delta_cur_redd shows avoided emissions: positive = REDD conserves more carbon than the baseline future

**Economic valuation**:
- npv_fut is the discounted value of sequestration over the period [lulc_cur_year, lulc_fut_year]
- Useful for cost-benefit analysis of land restoration or REDD+ programs

### Suggested next steps
- Download tot_c_cur.tif to visualize current carbon hotspots across the landscape
- If future LULC is available, compare delta_cur_fut to quantify net emissions or sequestration
- Combine with Habitat Quality (run_habitat_quality) to identify areas with co-benefits for carbon and biodiversity
- Combine with SDR (run_sdr) to assess trade-offs between carbon storage and sediment delivery
