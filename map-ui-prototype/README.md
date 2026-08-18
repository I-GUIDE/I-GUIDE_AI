# I-GUIDE Map UI — Prototype

A **chat-first** map interface for the I-GUIDE agent. The map is the canvas; you
drive it by talking to the agent. Every result — knowledge-base hits, live
OpenStreetMap features, uploaded data, analysis outputs — is a `LayerArtifact`
rendered by one thin function, exactly the contract the real agent will emit.

## Stack (matches the architecture decision)

- **MapLibre GL JS** via **react-map-gl** — embeddable base map (OSM raster tiles, no key)
- **deck.gl** (`MapboxOverlay`) — GPU rendering of large/complex layers (points, polygons, heatmaps)
- **@turf/turf** — in-browser spatial analysis (stands in for the agent's sandboxed geoprocessing)
- **Overpass API** — live OpenStreetMap queries, bounded by the drawn region or map view

## Run

```sh
cd map-ui-prototype
npm install
npm run dev        # http://localhost:5173
```

## Try it (chat)

- `find flood risk datasets` — semantic KB search
- `show cafés here` / `hospitals here` — live Overpass in the current map view
- `buffer the cafés by 2 km` · `heatmap of the cafés` · `clip the datasets to the region`
- Draw a region with **▭ Region** to bound searches; drop a **GeoJSON** file to add your own layer

## Where the real system plugs in

This prototype is deliberately structured so each mock swaps 1:1 for production:

| File | Prototype (mock) | Production |
|---|---|---|
| `src/agentBrain.ts` | keyword intent parser | the real I-GUIDE agent (LLM + tools) |
| `src/mockKb.ts` `searchKb()` | in-memory + turf filter | OpenSearch kNN + `geo_shape` filter (Contract 2) |
| `src/analysis.ts` | turf in the browser | agent's sandboxed Python (GeoPandas/rasterio) |
| `src/contracts.ts` | `LayerArtifact` / `SpatialFilter` | **unchanged** — the wire contract |
| `src/overpass.ts` | direct Overpass call | direct, or proxied via a platform service |

The KB spatial fields (`spatial-centroid`, `spatial-bounding-box`) use the same
GeoJSON shapes produced by the fixed `../embedding-server/reindex_wkt_spatial.py`.
