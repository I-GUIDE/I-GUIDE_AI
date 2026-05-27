---
name: run-add-media-flows
description: "Parse an HTML file for country mentions and generate flow lines from a source point to each mentioned country."
allowed-tools:
  - run_add_media_flows
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Add Media Flows

Workflow guidance for the `run_add_media_flows` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- html_file: path to an HTML file (e.g., a scraped news article or media report) to be parsed for country mentions
- source_lon: longitude of the media source location (the publication or broadcaster's geographic origin)
- source_lat: latitude of the media source location
- country_reference_csv: path to a CSV file mapping country names to geographic centroids; must contain columns: country (or name), lon, lat

**Optional parameters**:
- source_name: display name label for the media source point on the map (default: 'Source')
- min_mentions: minimum number of times a country must be mentioned in the HTML to be included as a flow (default: 1)
- crs: coordinate reference system for all coordinates (default: 'EPSG:4326' for WGS84 lat/lon)

**Key notes**:
- The tool parses the HTML file for country name mentions and counts frequency
- Each country mentioned at least min_mentions times becomes a flow line from the source to that country's centroid
- country_reference_csv must be provided — it maps country names found in the article to lat/lon coordinates
- Trigger words: media flows, information flows, media analysis, country mentions, news flows, media coverage, information telecoupling

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| media_flows.geojson | Download | GeoJSON LineString features; one line per mentioned country, flowing from source to country centroid, with mention count as attribute |
| media_flows.shp | Download | Shapefile equivalent; suitable for GIS analysis |
| media_mention_frequency.csv | CSV | Table of all countries mentioned, their mention counts, and whether they passed the min_mentions threshold |

### Suggested next steps
- Visualize media_flows.geojson on the map to see the geographic reach and focus of the media source
- Filter by mention count in GIS to highlight the most frequently referenced countries
- Adjust min_mentions to reduce noise (increase threshold) or capture more countries (decrease threshold)
- Combine with commodity trade flows (run_commodity_trade) to compare media attention with actual trade intensity
- Use media_mention_frequency.csv to tabulate and report the information flow profile of the media source
