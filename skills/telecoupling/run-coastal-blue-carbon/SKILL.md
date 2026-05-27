---
name: run-coastal-blue-carbon
description: "Run InVEST Coastal Blue Carbon main model for carbon stock, sequestration, and NPV."
allowed-tools:
  - run_coastal_blue_carbon
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Coastal Blue Carbon

Workflow guidance for the `run_coastal_blue_carbon` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: If snapshot CSV, transitions CSV, or biophysical table have been uploaded, extract their paths directly — do not ask for them again. Only ask for genuinely missing inputs.

**Parameters to collect**:
- landcover_snapshot_csv: path to snapshots CSV (same file used in Tool 2)
- landcover_transitions_table: path to the **manually edited** transitions CSV (output of Tool 2, edited by user)
- biophysical_table_path: path to biophysical parameters table CSV
- analysis_year: final year of analysis (optional)
- do_economic_analysis: true/false — default false; if true, also collect:
  - discount_rate, inflation_rate
  - price (single carbon price) OR price_table_path (if use_price_table=true)

**Key notes**:
- If all three required files are uploaded (snapshots CSV, transitions CSV, biophysical table) and parameters are specified, **call the tool immediately** — do not wait for additional confirmation
- Include a brief reminder note in your response that the transitions CSV should have been manually edited before running; do not block the tool call on this
- Economic analysis parameters are only needed when do_economic_analysis=true

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| carbon-stock-at-*_preview.png | Preview image | Carbon stock spatial distribution at each time point |
| carbon-stock-at-*.tif | Download | Carbon stock raster; unit: Mg CO₂e/ha |
| carbon-accumulation-*_preview.png | Preview image | Carbon accumulation (positive = sink, negative = source) |
| carbon-emissions-*_preview.png | Preview image | Carbon emissions spatial distribution |
| total-net-carbon-sequestration*_preview.png | Preview image | Total net carbon sequestration over the study period |
| net-present-value*_preview.png | Preview image | Net present value of carbon (only when economic analysis is enabled) |

### Domain knowledge — understanding CBC Main outputs

**How carbon stock is calculated**:
- Baseline year stock = sum of three initial carbon pools per pixel: **biomass** (above-ground living tissue) + **soil** (dominant pool in mangroves; 50–90% of total; centuries of accumulation) + **litter** (dead surface organic matter)
- Subsequent years: stock = previous stock + accumulation − emissions
- Units are **Mg CO₂e per hectare** — directly comparable to carbon market reporting standards

**Half-life decay model — why emissions are spread over time**:
- When a LULC transition triggers `disturb`, carbon is not released instantly — it decays exponentially based on each pool's half-life defined in the biophysical table
- Soil carbon has a very long half-life (decades), meaning disturbance effects accumulate slowly but persist for a very long time
- Biomass carbon has a shorter half-life, releasing faster after disturbance
- This is why even a small area of mangrove loss can cause significant long-run emissions

**Interpreting net sequestration maps**:
- **Positive values** (blue/green tones): carbon sink — habitat is accumulating carbon, typically intact or recovering mangrove/marsh/seagrass
- **Negative values** (red/orange tones): carbon source — habitat is losing carbon, typically disturbed or converted areas
- Areas near zero (NCC transitions) indicate stable land cover with no net carbon change
- The total-net-carbon-sequestration raster is the most policy-relevant output — it directly quantifies climate impact

**Economic valuation (NPV map)**:
- NPV = discounted sum of carbon sequestration value over the analysis period
- Calculated using: carbon price ($/Mg CO₂e) × sequestration amount, discounted at the user-specified rate
- A typical carbon price range: $15–$50/Mg CO₂e on voluntary markets; $50–$150/Mg CO₂e on compliance markets
- Positive NPV areas represent conservation/restoration investment opportunities
- Can be overlaid with land tenure data to identify priority parcels for payment for ecosystem services (PES) programs

**High-carbon-stock areas — why they matter**:
- Old-growth mangrove forests can store 500–1000 Mg CO₂e/ha — among the highest carbon densities on Earth
- Destroying 1 hectare of high-stock mangrove can release as much CO₂e as burning hundreds of tonnes of fossil fuel
- The carbon-stock-at-baseline raster identifies these critical conservation priorities

### Suggested next steps
- Download total-net-carbon-sequestration TIF for GIS overlay with administrative boundaries
- Modify the transitions CSV to simulate alternative conservation or restoration scenarios and re-run
- Enable economic analysis (do_economic_analysis=true) with a carbon price to generate NPV maps for policy presentations
- Combine with Tool 4 (Seasonal Water Yield) for a comprehensive multi-ecosystem-service assessment
