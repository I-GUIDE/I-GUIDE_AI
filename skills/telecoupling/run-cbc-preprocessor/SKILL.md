---
name: run-cbc-preprocessor
description: "Run InVEST Coastal Blue Carbon Preprocessor to identify LULC transitions between time periods."
allowed-tools:
  - run_coastal_blue_carbon_preprocessor
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Cbc Preprocessor

Workflow guidance for the `run_coastal_blue_carbon_preprocessor` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: If snapshot CSV or LULC lookup table have been uploaded, extract their paths directly — do not ask for them again. Only ask for genuinely missing inputs.

**Parameters to collect**:
- landcover_snapshot_csv: path to snapshot CSV with two columns — snapshot_year (integer year) and raster_path (path to the LULC raster for that year)
- landcover_lookup_table: path to LULC lookup CSV with columns — lucode (integer), lulc-class (name), is_coastal_blue_carbon_habitat (true/false)

**Key notes**:
- If the user already has a manually edited transitions CSV from a previous run, skip this tool and proceed directly to Tool 3

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| transitions_*.csv | Table | LULC transition matrix — **requires manual editing before use in Tool 3** |
| carbon_pool_transient_template_*.csv | Table | Carbon pool template for entering carbon stock parameters per land class |
| aligned_lulc_*_preview.png | Preview image | Aligned land use raster overlaid on satellite basemap |
| aligned_lulc_*.tif | Download | Aligned LULC raster file |

### Domain knowledge — coastal blue carbon ecosystems

**Why coastal blue carbon matters**:
Coastal ecosystems — mangroves, salt marshes, and seagrasses — sequester carbon 3–5× faster per hectare than tropical forests, and store it for centuries to millennia. They are among the most carbon-dense ecosystems on Earth. When destroyed, they release vast amounts of stored carbon, making their conservation and restoration a high-priority climate strategy.

**Three carbon pools tracked by the model**:
- **Biomass**: Above-ground living plant tissue. Gained quickly when habitat recovers, lost rapidly when disturbed.
- **Soil**: The dominant pool in mangroves (50–90% of total carbon). Carbon accumulated over centuries in waterlogged, anoxic sediments. Released slowly after disturbance.
- **Litter**: Dead organic matter on the surface. Smaller pool, intermediate turnover rate.

**Understanding the transition matrix (what this tool produces)**:
- Each cell in transitions_*.csv represents a LULC-to-LULC change (e.g. mangrove → aquaculture)
- The value tells the model how to respond to that change: `accumulation` (carbon gain), `disturb` (carbon release), or `NCC` (no carbon change)
- The preprocessor fills in `disturb` as a placeholder — you must refine it to specify the **intensity** of disturbance

**Disturbance intensity — what each level means**:
- `low-impact-disturb`: Minimal biomass/soil disturbance (e.g. selective logging, light grazing). Small fraction of carbon released.
- `med-impact-disturb`: Moderate disturbance (e.g. partial clearing, seasonal flooding). Intermediate carbon release.
- `high-impact-disturb`: Complete habitat destruction (e.g. conversion to aquaculture, urban development). Maximum carbon release from all three pools.

### ⚠️ Critical warning — must tell the user
**transitions_*.csv requires manual editing before it can be used in Tool 3.**

The user must open the file and change transition type values from `disturb` to one of:
- `low-impact-disturb`
- `med-impact-disturb`
- `high-impact-disturb`

Choosing the right intensity level significantly affects carbon emission estimates. When in doubt, use published literature for the specific habitat conversion type in your study region.

### Suggested next steps
1. Download and manually edit transitions_*.csv — assign appropriate disturbance intensity for each transition
2. Fill in carbon stock values in carbon_pool_transient_template_*.csv (use field measurements or published biophysical parameters)
3. Run Tool 3 (Coastal Blue Carbon) once editing is complete
