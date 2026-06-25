---
name: chicago-crime-analysis
description: Use for Chicago crime maps, statistics, and community-area aggregation by reusing the contributed Chicago crime notebook from the knowledge base.
allowed-tools:
  - agent_kb_search
  - get_kb_block
  - execute_code
tags:
  - chicago
  - crime
  - geospatial
  - analysis
---

# Chicago Crime Analysis

Use this skill when the user asks about Chicago crime incidents, crime-type summaries, counts by community area, or crime maps. It reuses the contributed Chicago crime notebook's data-access and analysis code from the knowledge base. **Do NOT import any tool name as a Python module** (there is no `chicago_crime_analysis` package); reuse the real source via the knowledge-base tools below.

## Workflow — reuse the contributed code via the knowledge base

1. Call `agent_kb_search("load chicago crime data community areas")` to find the contributed Chicago crime blocks; note the cited `element_id`.
2. Call `get_kb_block(<element_id or block doc_id>)` to read the FULL source of the loader/analysis functions — typically `load_chicago_crime_data`, `load_chicago_community_areas`, `filter_dataframe_by_value`, `spatial_join_and_count`. These already contain the real data URLs/APIs (the City of Chicago Socrata API for incidents and the community-area boundary GeoJSON).
3. In `execute_code`, paste the reused function source verbatim, call it to load the data, then:
   - **crime-type filter:** keep rows whose category matches the requested type(s);
   - **counts by community area:** spatially join incidents to community polygons and count;
   - **map — match the user's words:** a "heat map" / "hotspot" / "density" request → a hexbin or KDE **point-density** map of the incident points (not a choropleth); a "choropleth" / "by community area" / "by region" request → **shaded polygons** of counts per area. Produce the type asked for — do not substitute one for the other — and SAVE it with `plt.savefig('result.png', bbox_inches='tight')` (the sandbox is headless — never rely on `plt.show()`).
4. Report the result and cite the source `element_id`.

## Answer requirements

- State the time window the loaded data covers if the source exposes one, and clarify that incidents are reported records, not all crimes that occurred.
- Reuse the contributed loader verbatim — do not invent data URLs, file ids, counts, or community rankings.
- If the knowledge base has no Chicago crime source and the user did not upload a dataset, say so plainly instead of fabricating a result.
