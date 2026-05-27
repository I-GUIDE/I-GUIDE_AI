---
name: run-habitat-risk-assessment
description: "Run InVEST Habitat Risk Assessment (HRA) to evaluate cumulative risk to habitats from multiple stressors."
allowed-tools:
  - run_habitat_risk_assessment
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Habitat Risk Assessment

Workflow guidance for the `run_habitat_risk_assessment` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Use this tool when the user asks about**:
- Cumulative risk to habitats or species from multiple human activities
- Marine/coastal spatial planning and stressor mapping
- Which habitats are most at risk and from which stressors
- Trade-offs between ecosystem services under alternative management scenarios

**Required parameters**:
- info_table_path: CSV listing each habitat and stressor with their spatial data files. Columns:
  - `name`: unique name (must match names in criteria_table_path)
  - `path`: path to raster (value=1 for presence) or vector spatial file
  - `type`: "habitat" or "stressor"
  - `stressor buffer (meters)`: buffer distance for stressors (blank for habitats, 0 for no buffer)
- criteria_table_path: the criteria scoring CSV — complex format (see sample data); rows are criteria grouped by habitat-stressor pair, columns are exposure/consequence scores
- resolution: spatial resolution in meters for output rasters (start with 1000 m for testing, refine later)
- max_rating: maximum score value used in the criteria table (e.g. 3 if ratings are 0–3)
- risk_eq: 'Euclidean' (default, more conservative) or 'Multiplicative' (amplifies combined high scores)
- decay_eq: how stressor effects decay in buffer zones — 'exponential', 'linear', or 'none'
- n_overlapping_stressors: the maximum number of stressors expected to overlap in any one area (affects risk classification thresholds)
- aoi_vector_path: polygon shapefile of planning subregions (summary statistics reported per subregion)

**Optional**:
- visualize_outputs: set True to generate GeoJSON output compatible with the HRA web visualization app

**Important data preparation note**: The criteria scores table is the most complex input — direct the user to InVEST sample data for the correct format before running.

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| RECLASS_RISK_<habitat>.tif | Download | Risk classification map per habitat (High/Medium/Low = 3/2/1) |
| RISK_<habitat>.tif | Download | Continuous risk score per habitat |
| EXPOSURE_<habitat>_<stressor>.tif | Download | Exposure score of each habitat to each stressor |
| CONSEQUENCE_<habitat>_<stressor>.tif | Download | Consequence score of each habitat from each stressor |
| ECOSYSTEM_RISK.tif | Download | Combined ecosystem-level risk map |
| SUMMARY_STATISTICS.csv | Table | Mean risk, exposure, consequence per habitat per subregion |

### Domain knowledge — understanding HRA outputs

**The exposure–consequence framework**:
- **Exposure** = how much a habitat is physically exposed to a stressor (proximity, intensity, frequency, duration)
- **Consequence** = how much impact the stressor has IF the habitat is exposed (sensitivity, recovery time)
- **Risk** = combination of exposure and consequence — high risk requires BOTH high exposure AND high consequence

**Risk equation effects**:
- Euclidean: `Risk = √(E² + C²)` — a habitat can have high risk from high exposure alone OR high consequence alone
- Multiplicative: `Risk = E × C` — risk is only high when BOTH exposure AND consequence are high; a habitat with high consequence but protected from stressors has low risk

**RECLASS_RISK maps (High/Medium/Low)**:
- The classification threshold depends on n_overlapping_stressors — more stressors = higher maximum possible risk = wider classification bands
- High-risk pixels are priority targets for stressor reduction or habitat protection
- Compare across habitats to see which are most vulnerable overall

**SUMMARY_STATISTICS.csv**:
- Mean risk per habitat per AOI subregion — use this to rank planning areas by cumulative habitat risk
- Identifies which stressor drives the most risk for each habitat — guides targeted management

**Decay equation choice**:
- 'exponential': stressor impact drops off rapidly with distance from its footprint — realistic for most localized stressors (e.g. trawling, aquaculture)
- 'linear': gradual, steady decay — appropriate for diffuse stressors (e.g. noise, nutrient runoff)
- 'none': stressor has full impact throughout its buffer zone — conservative approach, worst-case

**Resolution guidance**:
- Start at 1000 m to verify inputs run correctly and check spatial patterns
- Refine to 100–250 m for final analysis if habitat/stressor data supports it
- Finer resolution = longer runtime (quadratic scaling)

### Suggested next steps
- Download RECLASS_RISK maps to identify high-risk habitat patches
- Review SUMMARY_STATISTICS.csv to rank subregions by cumulative risk
- Run scenarios with reduced stressor buffers or removed stressors to test management effectiveness
- Combine with Habitat Quality (run_habitat_quality) for a complementary terrestrial habitat assessment
