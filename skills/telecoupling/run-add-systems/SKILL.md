---
name: run-add-systems
description: "Create point features for telecoupling systems (sending/receiving/spillover) from a CSV with Name and coordinates."
allowed-tools:
  - run_add_systems_interactively
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Add Systems

Workflow guidance for the `run_add_systems_interactively` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- input_csv: path to the CSV file containing system location data
- x_field: column name for the X coordinate (longitude) of each system's representative point
- y_field: column name for the Y coordinate (latitude) of each system's representative point

**Optional parameters**:
- name_field: column name for system names or labels (e.g., "Sending System", "Receiving System") (default: 'Name')
- crs: coordinate reference system of the input coordinates (default: 'EPSG:4326' for WGS84 lat/lon)

**Key notes**:
- Systems in the telecoupling framework are the coupled human-nature systems involved in telecoupling: sending systems (where flows originate), receiving systems (where flows arrive), and spillover systems (indirectly affected)
- Each row in input_csv becomes one system point feature
- Trigger words: add systems, telecoupling systems, sending system, receiving system, spillover system, coupled systems

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| systems.geojson | Download | GeoJSON Point features; one point per system with name attribute |
| systems.shp | Download | Shapefile equivalent; suitable for GIS analysis |
| systems_table.csv | CSV | Tabular copy of all systems with coordinates and name attributes |

### Suggested next steps
- Visualize systems.geojson on the map to confirm the geographic positions of all telecoupling systems
- Overlay system points with flow lines from run_draw_radial_flows or run_commodity_trade
- Combine with agents (run_add_agents) and causes (run_add_causes) to build a complete telecoupling diagram
- Use systems_table.csv for telecoupling framework documentation and case study reporting
