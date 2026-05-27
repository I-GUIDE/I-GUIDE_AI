---
name: run-add-agents
description: "Create a point feature layer for telecoupling agents from a CSV with Name, X (lon), Y (lat) columns."
allowed-tools:
  - run_add_agents_interactively
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Add Agents

Workflow guidance for the `run_add_agents_interactively` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- input_csv: path to the CSV file containing agent location data
- x_field: column name for the X coordinate (longitude) of each agent
- y_field: column name for the Y coordinate (latitude) of each agent

**Optional parameters**:
- name_field: column name for agent names or labels displayed on the map (default: 'Name')
- text_field: column name for additional descriptive text or notes associated with each agent (optional; omitted if not provided)
- crs: coordinate reference system of the input coordinates (default: 'EPSG:4326' for WGS84 lat/lon)

**Key notes**:
- Agents in the telecoupling framework are the people, organizations, or entities involved in telecoupling interactions (e.g., farmers, government agencies, corporations, NGOs)
- Each row in input_csv becomes one agent point feature
- Trigger words: add agents, telecoupling agents, agent points, sending system agents, stakeholders, actors

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| agents.geojson | Download | GeoJSON Point features; one point per agent with name and optional text attributes |
| agents.shp | Download | Shapefile equivalent; suitable for GIS analysis or further overlay |
| agents_table.csv | CSV | Tabular copy of all agents with coordinates and attributes |

### Suggested next steps
- Visualize agents.geojson on the map to confirm spatial distribution of agents
- Overlay agent points with sending/receiving system boundaries (run_add_systems or run_draw_systems_from_table)
- Combine with radial flows (run_draw_radial_flows) to show which agents drive flows between systems
- Use agents_table.csv to document stakeholder locations for reporting or telecoupling case study analysis
