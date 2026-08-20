# Spatial analysis demo set

The datasets and prompts used to exercise the agent's spatial-analysis toolkit end to end,
with the results each one actually produced. Every number below was observed in the running
prototype, not estimated — if a run gives you something different, that is a signal.

Use them to demo the agent, to check a change did not break delivery, or as a starting point
when adding a tool.

## Files

Small enough to keep in the repo:

| File | Size | What it is |
|---|---|---|
| `illinois_tract_population.csv` | 118 KB | 3,265 Illinois census tracts: `GEOID`, `tract_name`, `population` (2020 census, via TIGERweb `POP100`). Totals **12,812,508** — the exact state figure, which is how you know a join on `GEOID` worked. |

Too large to commit; `data/` is gitignored. Rebuild with `python3 build_datasets.py`, or copy
them from the deployed VM at `~/Documents/i-guide-platform-flask-servers/demo/spatial-analysis/data/`:

| File | Size | What it is | Provenance |
|---|---|---|---|
| `data/chicago_incidents_full.csv` | 35 MB | 128,886 Chicago incident records with lat/lon | Chicago Data Portal, Crimes export |
| `data/incidents_subset.csv` | 8.8 MB | First 32,000 of the above | `head -32001` — under the 10 MB browser-upload cap |
| `data/chicago_areas_with_incident_counts.geojson` | 3.5 MB | 801 Chicago census tracts carrying environmental-justice attributes (`CEJI`, `treeCCov17`, `ndvi`, `leadPoisonR`, `socvlnIndx`, `urbanFlood`, …) plus an `incident_count` the agent joined on | I-GUIDE Chicago EJ layer + a spatial join against the incidents |
| `data/il_tracts.zip` | 9.3 MB | TIGER 2020 Illinois tract shapefile, **geometry only** | `https://www2.census.gov/geo/tiger/TIGER2020/TRACT/tl_2020_17_tract.zip` |
| `data/illinois_tracts_population.geojson` | 3.0 MB | The two Illinois files pre-joined, ready for regionalization | `build_datasets.py` |

`il_tracts.zip` carries no population on purpose: pairing it with the CSV is what makes the
join a real test rather than a formality.

## Prompts, and what they produced

Attach the named file(s) in the prototype and send the prompt.

**Point density — `data/incidents_subset.csv`** (or the full file)
> Show me a heat map of these incident locations on the map.

31,977 points as a density surface (128,855 for the full file, served unsampled at 15.8 MB).

**Choropleth — `data/chicago_areas_with_incident_counts.geojson`**
> Make a choropleth of incident_count.

801 areas, `incident_count` 1–1,661, total 128,465.

**Grid aggregation + buffer — `data/incidents_subset.csv`**
> Aggregate these incidents into a 1 km hex grid, then buffer the busiest cell by 2 km.

708 cells; the buffer measured **20.32 km²** against 20.37 expected for a 2 km radius — the
metric-CRS check, since computing that in degrees would be visibly wrong.

**Spatial statistics — `data/chicago_areas_with_incident_counts.geojson`**
> Run a LISA cluster analysis on incident_count in this file and show the clusters on the map.

801 tracts, queen weights, **0 islands**, mean 6.58 neighbours. Moran's I **0.4364**
(z = 22.6, p = 0.001), Geary's C 0.6551 — three statistics agreeing on strong positive
clustering. Classes: 90 High-High, 109 Low-Low, 27 Low-High, 6 High-Low, 569 not significant;
232 significant. Renders as a categorical layer with a five-entry legend — red hot spots on
the West and South sides.

**Attribute join + regionalization — `data/il_tracts.zip` *and* `illinois_tract_population.csv`**
> The zip is the Illinois census tract shapefile (geometry only) and the CSV has population per
> tract. Join the population onto the tracts on GEOID, then use the regionalize tool with
> method maxp, bound_column population and min_bound 1000000 to build contiguous regions, and
> show them on the map.

3,265 tracts matched with no misses, then **9 contiguous regions** in ~20 s, every one above
the 1,000,000 floor (the first: 1,433,433 people across 491 tracts). Twelve is the theoretical
ceiling at that floor; contiguity costs the other three. The map should show tiny dense regions
around Chicago and large sparse ones downstate — if they are not contiguous blocks, something
is wrong.

**Satellite embeddings — no file needed**
> Compute a satellite embedding for the bounding box [-87.5253, 40.9775, -87.5110, 40.9855] for
> June to September 2022 using the GSE model, and show it on the map.

A PCA-RGB raster layer in ~9 s. Needs the `rs-embed` service running and Earth Engine
credentials. The colours are a three-component PCA — *not* land-cover classes, and not
comparable between runs.

## Two things worth knowing

The join prompt needs `execute_code`, because there is **no attribute-join tool**:
`vector_spatial_join` joins by geometry, not by a shared key. The agent tries the spatial join
first, then falls back to code. That is the single clearest gap this set exposes.

Several of these datasets were the ones that caught real bugs — a flat LISA map whose legend
never reached the client, max-p crashing when the bound column was also a clustering variable,
an 89 MB intermediate written every turn. Keeping them around is cheaper than rediscovering
those cases.
