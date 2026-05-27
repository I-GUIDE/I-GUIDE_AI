---
name: run-radial-flows
description: "Generate radial flow lines from a CSV of origin-destination coordinate pairs."
allowed-tools:
  - run_draw_radial_flows
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Radial Flows

Workflow guidance for the `run_draw_radial_flows` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- input_csv: path to the input CSV file where each row represents one flow (origin-destination pair)
- from_x_field: column name for the X coordinate (longitude) of the flow origin
- from_y_field: column name for the Y coordinate (latitude) of the flow origin
- to_x_field: column name for the X coordinate (longitude) of the flow destination
- to_y_field: column name for the Y coordinate (latitude) of the flow destination

**Optional parameters**:
- crs: coordinate reference system of the input coordinates (default: 'EPSG:4326' for WGS84 lat/lon)

**Key notes**:
- Each row in input_csv becomes one straight-line flow arc from origin to destination
- Additional columns in input_csv (e.g., flow magnitude, commodity type) are preserved in the output GeoJSON/SHP as attributes
- Trigger words: radial flows, flow lines, OD matrix, origin-destination, flow mapping, movement lines, telecoupling flows

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| radial_flows.geojson | Download | GeoJSON LineString features; one feature per flow with all original CSV attributes preserved |
| radial_flows.shp | Download | Shapefile equivalent of the GeoJSON; suitable for GIS analysis in QGIS or ArcGIS |
| radial_flows_summary.csv | CSV | Summary table with total number of flows, unique origins, unique destinations, and attribute statistics |

### Suggested next steps
- Visualize radial_flows.geojson in the map view to inspect the spatial pattern of flows
- Filter flows by magnitude or type in GIS using the preserved attribute columns
- Combine with run_add_systems or run_draw_systems_from_table to overlay sending/receiving system boundaries
- Use alongside run_commodity_trade for flows derived from bilateral trade data with country centroids
- If flows originate from a single point (hub-and-spoke), the radial pattern will clearly show destination reach
