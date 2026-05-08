---
name: chicago-crime-analysis
description: Use for Chicago crime statistics, community-area aggregation, and crime map workflows.
allowed-tools:
  - mcp_load_chicago_community_areas
  - mcp_load_chicago_crime_data
  - mcp_get_crime_statistics
  - mcp_count_crimes_per_community
  - mcp_generate_crime_map
tags:
  - chicago
  - crime
  - geospatial
  - analysis
---

# Chicago Crime Analysis

Use this skill when the user asks about recent Chicago crime incidents, crime type summaries, arrest rates, crime counts by community area, or map outputs for crime patterns.

## Tool Workflow

Load this skill at most once per user request. After the required tool outputs are available, stop calling tools and produce the final answer.

For summary statistics:

1. Call `mcp_load_chicago_crime_data`.
2. Call `mcp_get_crime_statistics`.
3. If the user asks about one crime type, pass `crime_type` using the uppercase Chicago `primary_type` value, for example `THEFT`, `BATTERY`, `ROBBERY`, or `NARCOTICS`.

For counts by community area:

1. Call `mcp_load_chicago_community_areas`.
2. Call `mcp_load_chicago_crime_data`.
3. Call `mcp_count_crimes_per_community`.
4. If the user asks about one crime type, pass the same `crime_type` to `mcp_count_crimes_per_community`.
5. Report the top communities, total counted incidents, and whether a crime type filter was applied.

For maps:

1. If the user asks for an all-crime map, call `mcp_generate_crime_map` directly.
2. If the user asks for a map filtered to one crime type, first run the filtered community count workflow with `mcp_count_crimes_per_community(crime_type=...)`, then call `mcp_generate_crime_map` with a title that states the filter.
3. Return the generated `file_id` and `download_url` when available.

## Answer Requirements

- State the time window represented by the crime data if the loader returns a `date_range`.
- Clarify that incidents are reported crime records from the loaded Chicago data, not all crimes that occurred.
- Preserve exact community names from tool output.
- Do not invent map files, file ids, download URLs, counts, or community rankings.
- If a tool returns an error saying data is not loaded, call the required loader tool and retry the failed step once.
- If the available tool output already contains the requested ranking, count, statistic, `file_id`, or `download_url`, answer directly instead of calling another agent or repeating tools.
- Keep the final response concise unless the user asks for a full report.

## Common Requests

- "Which Chicago community has the most theft?" Use the filtered community count workflow with `crime_type="THEFT"`.
- "Show a map of recent crime by community area." Use `mcp_generate_crime_map` directly.
- "What is the arrest rate for battery?" Use `mcp_load_chicago_crime_data`, then `mcp_get_crime_statistics(crime_type="BATTERY")`.
