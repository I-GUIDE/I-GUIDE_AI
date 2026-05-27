---
name: run-habitat-quality
description: "Run InVEST Habitat Quality to map habitat degradation and quality from threats."
allowed-tools:
  - run_habitat_quality
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Habitat Quality

Workflow guidance for the `run_habitat_quality` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- lulc_cur_path: current land use / land cover raster
- threats_table_path: CSV defining each human threat (columns: threat name, max distance km, weight 0-1, decay type 'linear'/'exponential'); each threat also needs a corresponding raster
- sensitivity_table_path: CSV mapping each LULC class to habitat sensitivity for each threat (must use 'lulc' as the index column, NOT 'lucode')

**Important — threat rasters**: For each row in threats_table_path, a corresponding threat raster (named threat_<name>_c.tif for current LULC) must exist in the same directory as lulc_cur_path. Ask the user to confirm threat raster files are co-located.

**Optional parameters** (ask only if user mentions future/baseline scenarios or protected areas):
- lulc_fut_path: future LULC raster → produces quality_f.tif and deg_sum_f.tif
- lulc_bas_path: baseline LULC raster → produces quality_b.tif and deg_sum_b.tif (for REDD comparisons)
- access_vector_path: vector of legal protected status (0=no protection, 1=full protection)
- half_saturation_constant: default 0.5; smaller values make the model more sensitive to low degradation

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| quality_c.tif | Download | Habitat quality (current LULC); 0–1 scale (1 = highest quality) |
| deg_sum_c.tif | Download | Total threat degradation (current LULC); higher = more degraded |
| quality_f.tif | Download | Habitat quality (future LULC); present if lulc_fut_path provided |
| deg_sum_f.tif | Download | Degradation (future LULC) |
| quality_b.tif | Download | Habitat quality (baseline LULC) |
| deg_sum_b.tif | Download | Degradation (baseline LULC) |

### Domain knowledge — understanding Habitat Quality outputs

**What habitat quality (0–1) means**:
- 1.0 = pristine habitat, far from all threats, with full legal protection if access layer provided
- 0.0 = completely degraded — near high-intensity threats with no protection
- The score is a relative index, not an absolute biodiversity count; it reflects the potential to support native biodiversity given land cover and threat exposure

**Degradation index (deg_sum)**:
- Represents cumulative threat exposure weighted by threat sensitivity and distance decay
- Useful for identifying the most degraded patches that could benefit most from restoration
- Higher deg_sum does not always mean lower quality — habitat type also matters (forests tolerate more degradation than grasslands in the model)

**Interpreting spatial patterns**:
- Quality drops near urban areas, roads, and agricultural land — depending on threats defined
- Riparian corridors often show high quality hotspots if threats decay quickly with distance
- Protected areas with access=1 show elevated quality even when nearby threats are present

**Scenario comparison**:
- Compare quality_c vs quality_f to visualize biodiversity impact of future land use change
- If quality_f < quality_c → net habitat loss under the future scenario
- quality_b is useful as a historical reference point (what habitat looked like before conversion)

### Suggested next steps
- Download deg_sum_c.tif to identify restoration priority areas (highest degradation)
- Overlay quality_c.tif with Carbon Storage (tot_c_cur.tif) to find biodiversity–carbon co-benefit zones
- If a future LULC scenario exists, run again with lulc_fut_path to quantify habitat change
