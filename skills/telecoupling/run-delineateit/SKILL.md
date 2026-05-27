---
name: run-delineateit
description: "Run InVEST DelineateIt to delineate watersheds from a DEM and outlet points."
allowed-tools:
  - run_delineateit
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Delineateit

Workflow guidance for the `run_delineateit` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- dem_path: digital elevation model raster (any unit; must be projected in meters for accurate area)
- Outlet specification — one of:
  - outlet_vector_path: point or polygon shapefile of watershed outlet locations (pour points)
  - detect_pour_points: set to true to have the model automatically detect pour points from the DEM

**Optional parameters** (ask only if user mentions snapping or stream alignment):
- snap_points: set true to snap outlet points to the nearest stream pixel (recommended when points are hand-digitized)
- flow_threshold: minimum flow accumulation to define a stream (default 1000 pixels upstream); lower = denser stream network
- snap_distance: maximum snapping distance in pixels (default 20)

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| watersheds.gpkg | Download | Delineated watershed polygons (one per outlet point) |
| snapped_outlets.gpkg | Download | Snapped outlet locations (present if snap_points=True) |
| detected_outlets.gpkg | Download | Auto-detected pour points (present if detect_pour_points=True) |

### Domain knowledge — understanding DelineateIt outputs

**What watershed delineation produces**:
- watersheds.gpkg contains one polygon per outlet point, representing the contributing drainage area upstream of that point
- All pixels inside a watershed drain to the corresponding outlet via the D8 flow direction algorithm
- Each watershed polygon is a self-contained hydrological unit — precipitation falling within the polygon flows to the outlet

**Choosing between outlet methods**:
- detect_pour_points: best for exploratory analysis or when you want natural watershed boundaries based on the DEM topology alone
- outlet_vector_path: use when you have known gauge stations, dam locations, or specific points of interest (more control)

**Snap points recommendation**:
- Hand-digitized points are rarely exactly on the stream centerline — even 1-pixel offset can produce incorrect delineation
- Enable snap_points=True with a conservative flow_threshold (e.g. 500–1000) and snap_distance (10–30 pixels) to align points to the modeled stream network
- Review snapped_outlets.gpkg to verify points landed on the intended stream

**DEM quality affects results**:
- Sinks (local depressions) in the DEM are filled before routing — may alter watershed boundaries slightly
- Use a hydrologically conditioned DEM (burned streams) for best accuracy in flat terrain
- Resolution matters: a 30m DEM gives coarser boundaries than a 10m DEM

**Downstream use**:
- watersheds.gpkg is the standard input for watershed-level models: Annual Water Yield, SDR, NDR
- Ensure the 'ws_id' field in the output matches the schema expected by those models

### Suggested next steps
- Download watersheds.gpkg to review delineated boundaries in GIS
- Use the delineated watersheds as input to run Annual Water Yield (run_annual_water_yield), SDR (run_sdr), or NDR (run_ndr)
- If boundaries look incorrect, try adjusting snap_distance or flow_threshold
