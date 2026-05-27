---
name: run-routedem
description: "Run InVEST RouteDEM to compute flow direction, flow accumulation, streams, and slope from a DEM."
allowed-tools:
  - run_routedem
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Routedem

Workflow guidance for the `run_routedem` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first.

**Required parameters**:
- dem_path: digital elevation model raster (projected CRS recommended; unit: meters)

**What to compute** (ask the user which outputs they need; defaults are flow direction + accumulation):
- calculate_flow_direction: direction each pixel drains (required for most downstream analyses)
- calculate_flow_accumulation: number of upstream pixels draining into each pixel (defines stream network)
- calculate_stream_threshold: extract stream network where accumulation ≥ threshold_flow_accumulation
- calculate_slope: slope raster in degrees
- calculate_stream_order: Strahler stream order (requires stream threshold)
- calculate_downstream_distance: distance from each pixel to the stream outlet

**algorithm**:
- 'D8' (default): each pixel drains to exactly one of 8 neighbors — simpler, faster, standard for most analyses
- 'MFD': multiple flow direction — flow is split among multiple downslope neighbors — better for flat/complex terrain

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| flow_direction.tif | Download | Flow direction raster (D8 encoded as integer 1–128, or MFD) |
| flow_accumulation.tif | Download | Upstream contributing area (number of pixels draining to each cell) |
| stream.tif | Download | Binary stream network (1=stream, 0=non-stream); present if calculate_stream_threshold=True |
| slope.tif | Download | Slope in degrees; present if calculate_slope=True |
| stream_order.tif | Download | Strahler stream order; present if calculate_stream_order=True |
| downstream_distance.tif | Download | Distance to nearest downstream outlet (pixels); present if calculate_downstream_distance=True |

### Domain knowledge — understanding RouteDEM outputs

**Flow direction (D8)**:
- Each pixel is assigned an integer (powers of 2: 1, 2, 4, 8, 16, 32, 64, 128) encoding which of 8 neighbors it drains to
- Required by SDR, NDR, and DelineateIt as an intermediate hydrological layer

**Flow accumulation**:
- Value at each pixel = number of upstream pixels — a proxy for streamflow volume
- Large values (high accumulation) mark stream channels; small values mark ridges/hillslopes
- Used to define stream networks by applying a threshold (e.g., accumulation ≥ 1000 = stream)

**Choosing threshold_flow_accumulation**:
- Lower threshold (e.g. 100): denser, fine-scale stream network
- Higher threshold (e.g. 5000): sparse, main-channel only network
- Calibrate against observed stream maps or DEM resolution (30m DEM: ~1000 is typical starting point)

**D8 vs MFD**:
- D8 is appropriate for most watershed analyses and is compatible with DelineateIt/SDR/NDR
- MFD better represents diffuse flow in gentle terrain (e.g. broad floodplains) but is not used by other InVEST models as input

**RouteDEM as a preprocessing tool**:
- This tool is primarily used to inspect DEM routing before running SDR, NDR, or DelineateIt
- The other watershed models run their own routing internally — RouteDEM gives you the intermediate layers for QA/QC

### Suggested next steps
- Download flow_accumulation.tif and stream.tif to verify stream network matches reality before running watershed models
- If stream network looks incorrect, adjust threshold_flow_accumulation and re-run
- Use flow_direction and flow_accumulation as reference when troubleshooting SDR or NDR results
