# Knowledge-Element Extractors — Design

Status: **partially implemented**. Done: interfaces, unified manifest, fileclass,
deterministic id derivation, CLI/MCP/webhook entry points, the **NotebookExtractor**,
and **all three emitters** (OpenSearch → agent-only indices; MCP manifest writer +
generic-executor lookup; SKILL.md writer). Still stubs: **code / dataset / publication**
extractors, the search-peer agent-index wiring, and live-cluster/exec validation.
This document remains the contract for those.

## 0. Extractor reference (inputs / outputs)

All four extractors implement `base.Extractor.extract(path, ctx) -> ExtractionResult`
and are reached via `ingest_submission` (webhook `POST /ingest`). Every derived
`doc_id` is anchored on the platform `element_id`; form `fields` are inherited into
`source_fields`. KB writes default to the **local** file store (no real DB).

| Extractor | Trigger / source | Input formats | What it does (method) | Output: resource-type + key fields | Runnable? | Emit targets |
|---|---|---|---|---|---|---|
| **Notebook** | GitHub URL (`element_type=notebook`, `notebook_file`) | `.ipynb` | IPython-aware transform (R1) per cell → classify constructs, resolve tools, file I/O; build module source | **NotebookBlock** per cell: `contents` (code+markdown), `block.{resolved_tools, imports, file_io, constructs}`; + 1 **workflow** asset `runnable.{workflow_id, runnable_tool, mode, entrypoint, module_source}`; + **SkillSpec** | ✅ workflow promoted (if all cells parse) | opensearch + mcp + skill |
| **Code** | GitHub URL (`element_type=code`) | `.py` | AST → API surface (funcs/classes/methods), imports; detect entry points | **CodeAsset** per symbol: `block.{qualified_name, signature, docstring, imports}`; + runnable asset per entry point (`main`/`run`/argparse/`__main__`) | ✅ entry points promoted | opensearch + mcp |
| **Dataset** | webhook file (`element_type=dataset`) | raster (`.tif/.nc/.hdf/.grib/.zarr/.img/.vrt`), vector (`.shp/.geojson/.gpkg/.gdb/.kml/.fgb/.parquet`), tabular (`.csv/.tsv/.xlsx`), `.zip` | per-family handler → CRS, bounds→bbox, resolution/bands/variables (raster), schema/geometry/feature_count/layers (vector), columns/row_count+lat-lon bbox (tabular); GIS libs optional, degrade with note | **Dataset**: `spatial.{crs, bounds, spatial-bounding-box-geojson, schema, resolution, feature_count, variables}` + `extracted.{format, family, size}` | ❌ index-only | opensearch |
| **Publication** | webhook file (`element_type=publication`) | `.tex/.txt/.md/.rst`, `.pdf` (pypdf), `.docx` | extract text → LLM (`llm_utils.call_llm`) → method/workflow JSON; degrade to text-excerpt + note if no LLM | **PublicationMethodSpec**: `contents` (summary+steps), `extracted.{steps, datasets_referenced, tools_referenced, params}` + `DESCRIBES_METHOD`/`USES` edges | ❌ index-only | opensearch |

Common to all: hits link back to the original element via `element_id` (parent), so
`agent_kb_search` cites the original knowledge element; runnable units carry a
`[runnable: mcp_run_<wid>]` marker in `contents` for the code peer.

## 1. Overview

Given submitted assets, extract reusable knowledge and route it to three targets so
the I-GUIDE agent (supervisor-over-peers: search / analyze / code / synthesize,
`agent_runtime/supervisor_graph.py`) can **find**, **ground on**, and **run** it:

- **OpenSearch** — searchable blocks/metadata → the **search peer**'s evidence.
- **MCP manifests** — runnable functions/workflows, executed via **one generic
  executor** (not per-manifest tools).
- **SKILL.md** — the overall pipeline as agent guidance.

### Single webhook entrance (form submission)

A user submits a knowledge element through the platform form; the platform assigns
an **`element_id`** and POSTs a **submission** to the ingestion webhook
(`metadata-extraction-server/minio_webhook.py` `POST /ingest`). One entrance for all
four extractors; `element_type` routes to the right one. CLI (`extractors.cli`) and
the MCP `ingest_github_repo` tool are secondary/batch paths that converge on the same
`ingest_submission`.

**Submission payload** (`extractors/submission.py`):
```jsonc
{
  "element_id": "...",                       // platform-assigned; the doc_id ANCHOR
  "element_type": "notebook|code|dataset|publication",
  "source": { "github_url":"...", "ref":"...", "notebook_file":"...",  // notebook/code
              "file_path":"...", "bucket":"...", "key":"..." },          // dataset/publication
  "fields": { "title","authors","tags","contributor","abstract", ... }, // inherited into docs
  "targets": ["opensearch","mcp","skill"]
}
```

```mermaid
flowchart TD
    FORM["Platform form\n(assigns element_id + fields)"] --> WH["webhook POST /ingest"]
    WH --> SUB["Submission.from_payload"] --> IS{"ingest_submission\nroute by element_type"}
    IS -->|notebook| NB["NotebookExtractor\n(clone notebook_file)"]
    IS -->|code| CO["CodeExtractor (clone repo)"]
    IS -->|dataset| DS["DataExtractor (file)"]
    IS -->|publication| PUB["PublicationExtractor (file)"]
    NB & CO & DS & PUB --> UM["UnifiedManifest\n(element_id-anchored)"]
    UM --> OE["opensearch_emitter"] --> OS[("AGENT-ONLY indices\n(indices.index_for)")]
    UM --> ME["mcp_emitter"] --> MAN[("manifests → generic executor")]
    UM --> SE["skill_emitter"] --> SK[("skills/<slug>/SKILL.md")]
    OS -->|search peer (agent indices only)| AG["agent"]
    MAN -->|run_notebook_workflow / run_code_element| AG
    SK -->|load_skill| AG
```

### Storage backend — LOCAL by default (nothing hits the real DB)

`extractors/kb_store.py` selects the agent-KB backend. **Default is `local`** (a
file-backed store under `storage_root()/agent_kb/<index>.json`); the real OpenSearch
cluster is used **only** when `AGENT_KB_BACKEND=opensearch` (or a client is injected).
So by default the whole pipeline — ingest → emit → `agent_kb_search` — runs offline on
the filesystem and **never writes to the production OpenSearch/Neo4j**. (The MCP +
SKILL emitters already write only to the filesystem; no Neo4j emitter exists yet.)
This also makes the extracted artifacts **locally testable by the agent's retrieval
path** with no cluster. The full supervisor agent additionally needs an LLM endpoint,
but the KB layer (ingest, retrieve, skill discovery, executor lookup) is fully local.

### Agent-only indices (not discoverable by general search)

Extracted contents live in **separate OpenSearch indices** from the general platform
index (`OPENSEARCH_INDEX`), under `AGENT_KB_INDEX_PREFIX` (default `iguide_agent_`),
one per resource-type (`indices.py`): `…notebook_blocks`, `…code_assets`,
`…datasets`, `…publication_methodspecs`. General platform search never sees them;
**the agent's search peer must additionally query `indices.all_agent_indices()`**
(a search-side change — the granular backends today read only `OPENSEARCH_INDEX`).
This keeps fine-grained blocks/method-specs available to agents without polluting
end-user platform search.

### element_id anchoring + inherited fields

All derived `doc_id`s anchor on the platform `element_id` (not a URL hash):
a notebook element's blocks are `{element_id}::block::{order}`; a code element's
assets are `{element_id}::code::{path}::{qual}`; a dataset is `{element_id}`; a
publication method-spec is `{element_id}::methodspec`. The form `fields`
(`title, authors, tags, contributor, abstract`) are inherited into each doc's
`_source` (`AssetRecord.source_fields`), so extracted docs carry real platform
metadata rather than synthesized values.

## 2. Unified manifest schema (`manifest.py`)

One `UnifiedManifest` per ingest run. Each asset is an `AssetRecord` (`base.py`).
The `runnable.*` sub-block mirrors `MCP_server/notebook_workflow_builder.py:224-237`
so `generated_notebook_tools._run_generated_manifest` consumes it unchanged.

```jsonc
{
  "schema_version": 1,
  "repo_id": "repo_<sha1[:12]>",        // or "" for upload path
  "source_url": "...", "commit_sha": "...", "cloned_at": "ISO-8601",
  "extractors_run": ["notebook","code"],
  "assets": [{
    "asset_id": "...", "kind": "notebook_block|code_block|dataset|publication",
    "resource_type": "NotebookBlock|CodeAsset|Dataset|PublicationMethodSpec",
    "doc_id": "...", "emit_targets": ["opensearch","mcp","skill"],
    "source_rel_path": "...", "title": "...", "contents": "...",
    "block":    { "code","markdown_context","constructs","resolved_tools","file_io","imports" },
    "spatial":  { "crs","bounds","resolution","schema","spatial-bounding-box-geojson", ... },
    "runnable": { "workflow_id","mode","entrypoint","entrypoint_parameters",
                  "source_path","manifest_path","runnable_tool" },   // only if promoted
    "extracted": { ...additive fields mirrored into the OpenSearch `extracted` object... }
  }],
  "provenance_edges": [{"src":"...","rel":"INCLUDES|DEFINES|IMPLEMENTED_BY|USES|HAS_WORKFLOW|DESCRIBES_METHOD","dst":"...","detail":{}}],
  "skill": { "name","description","allowed_tools","tags","ordered_steps" }
}
```

## 3. OpenSearch document model

**Reuse the platform `_source` fields** (`doc_id, title, contents, resource-type,
element_type, contents-embedding, spatial-bounding-box-geojson, tags, authors,
contributor, click_count, thumbnail-image`); add an **additive `extracted{}` object**
(ignored by code that doesn't know it). `element_type` is synthesized from
`resource-type` by `rag_pipeline/search/utils.py:35-46`, so any doc with
`doc_id/title/contents/resource-type(+contents-embedding)` flows through keyword,
semantic, and spatial **unchanged**.

New `resource-type` values: `NotebookBlock`, `CodeAsset`, `PublicationMethodSpec`
(Dataset reuses the existing type). **Deterministic doc_ids** (`doc_ids.py`):

| Asset | doc_id | resource-type |
|---|---|---|
| Notebook block | `{notebook_doc_id}::block::{order}` | NotebookBlock |
| Code asset | `{repo_id}::code::{path}::{qualname}` | CodeAsset |
| Dataset | platform element id / filename | Dataset |
| Publication method-spec | `{publication_doc_id}::methodspec` | PublicationMethodSpec |

Keyed on **position/path/name, not content hash**, so re-ingest upserts (record
`extracted.content_sha` separately for change detection; reconcile stale
`{parent}::block::*` via delete-by-query on `extracted.parent_doc_id`).

**Embedding**: for `NotebookBlock`/`CodeAsset`, embed the **markdown context +
docstring + signature**, NOT raw code (`contents-embedding` is one shared kNN field;
raw code retrieves poorly against NL queries). Keyword + Neo4j traversal are the
fallback for code-only cells.

## 4. Neo4j cross-links

Reuse labels `Notebook, Dataset, Publication, Code`; **add `NotebookBlock`,
`CodeAsset`** to `NEO4J_RESOURCE_LABELS` (`neo4j_graph_tools.py:41-66`); add a small
internal `Workflow` node (the find↔run bridge) to `NEO4J_INTERNAL_LABELS`.

Edges: `(Notebook)-[:INCLUDES {order}]->(NotebookBlock)`,
`(Code)-[:DEFINES]->(CodeAsset)`,
`(Publication)-[:IMPLEMENTED_BY]->(Notebook|Code)`,
`(NotebookBlock|CodeAsset|Publication)-[:USES]->(Dataset)`,
`(NotebookBlock|CodeAsset)-[:HAS_WORKFLOW {workflow_id, mcp_tool}]->(Workflow)`.

**Alignment**: write the OpenSearch `doc_id` into each Neo4j node's `id` property +
`visibility:"public"` so `_node_to_hit` (`agents.py:298`) maps node→`doc_id` and
`element_by_id`/`explore_related_by_id` (which require public visibility) can return
them. Deterministic doc_ids guarantee both records share the id. Optional: add
"notebook block"/"code asset" alternatives to the `by_resource_type` regex +
`_CYPHER_INCLUDES`/`_CYPHER_IMPLEMENTED_BY` templates if users query these by name
rather than by traversal.

## 5. Find ↔ run linkage (critical)

The code peer sees only `doc_id / title / contents[:500]` via `_format_documents`
(`agent_runtime/evidence_subgraph.py:67-74`). Therefore the run pointer must be in
those fields:

- **Prepend** `[runnable: <mcp_tool>]` to `contents` (zero code change), where
  `<mcp_tool> = doc_ids.mcp_tool_name_for(workflow_id)` = `mcp_run_<workflow_id>`.
- `extracted.runnable_tool` **must equal** the name the generic executor exposes for
  that `workflow_id` — both come from `doc_ids.mcp_tool_name_for` (single source of
  truth).

Flow: search peer retrieves the block → `contents` advertises `mcp_run_<wid>` → code
peer (which has the MCP tools) calls it. Alternative (cleaner, but touches shared
code): extend `_format_documents` to emit `extracted.runnable_tool`.

## 6. SKILL mapping

One SKILL per notebook (the overall pipeline). `skill_emitter` writes
`skills/<slug>/SKILL.md`:

| Front-matter | Source |
|---|---|
| `name` | `doc_ids.slugify(title)` — must match `^[a-z0-9][a-z0-9-]{0,63}$` |
| `description` | notebook summary ("Use for …") |
| `allowed-tools` | the promoted `mcp_run_<wid>` tool names |
| `tags` | notebook tags + detected libs |
| body | ordered steps referencing each step's workflow |

Discovered by `SkillRegistry.discover` (`agent_runtime/skills.py`); **validate
front-matter before writing** (malformed skills land silently in
`registry.errors`). Write under `REPO_ROOT/skills/` or a separate
`AGENT_SKILL_PATHS` root for generated content.

## 7. Dataset extractor — format coverage

Webhook-delivered files dispatch by extension/family (registry in
`data_extractor.HANDLERS`, families in `fileclass.py`). Every handler emits a common
record into the Dataset doc: `crs`, `spatial-bounding-box-geojson` (reprojected to
EPSG:4326 via `rag_pipeline/search/spatial.py:61-80 infer_geo_shape`), and an
`extracted{format, driver, size_bytes, …}`.

| Family | Formats | Extract | Libs |
|---|---|---|---|
| **Raster** | GeoTIFF/COG `.tif/.tiff`, NetCDF `.nc`, HDF `.hdf/.h5/.he5`, GRIB `.grib/.grb`, Zarr `.zarr`, ASCII `.asc`, IMG `.img`, VRT `.vrt` | CRS, bounds→bbox, resolution(x,y), bands+dtypes, nodata; +variables/dims/units + temporal extent (NetCDF/HDF/GRIB/Zarr) | `rasterio`; `xarray`(+`netcdf4`/`cfgrib`/`zarr`/`h5netcdf`) |
| **Vector** | Shapefile `.shp`(+`.dbf/.shx/.prj/.cpg`), GeoJSON `.geojson`, GeoPackage `.gpkg`, FileGDB `.gdb`, KML/KMZ, GML, FlatGeobuf `.fgb`, GeoParquet `.parquet` | per layer: CRS, bbox, geometry type, schema(field+type), feature count, layer list | `fiona`/`geopandas`(+`pyogrio`) |
| **Tabular** | CSV/TSV, Excel `.xlsx/.xls`, Parquet | column schema+dtype, row count; detect lat/lon or WKT → point geometry + bbox | `pandas`/`pyarrow`/`shapely` |
| **Container** | `.zip`, `.tar/.tgz` | walk members, dispatch each, aggregate (extends `extract_metadata.py:20-46`) | stdlib |
| **Sidecar/meta** | STAC item/collection JSON, ISO 19115/FGDC `.xml`, `.prj`, `.cpg` | prefer declared bbox/temporal/keywords over re-derivation | stdlib/`lxml` |

Index-only; never executable. Dataset `contents` (for embedding/keyword) =
title + description + variable/field names + format (not raw binary).

## 8. Change inventory

**Works unchanged (field-driven):** keyword + semantic retrieval of all asset types;
`neo4j_search` `by_resource_type` for new labels (matches `labels(r)`); granular-tool
hit normalization; evidence dedup/merge; code peer receiving blocks as evidence;
spatial filter mechanism (`term` on `resource-type`); `make_skill_tools` discovery;
MCP promotion path.

**Must change / add:**
1. OpenSearch mapping — add `extracted{}` sub-mapping; keep `resource-type` as `keyword`.
2. Neo4j — extend `NEO4J_RESOURCE_LABELS` (+`Workflow` internal); write `id`+`visibility`; create the 5 edge types.
3. (optional) Neo4j pattern regex + `_CYPHER_INCLUDES`/`_CYPHER_IMPLEMENTED_BY` templates.
4. (only if spatially filtering blocks) `spatial_search_tool`/`get_spatial_search_results` don't pass `element_type` — add an arg.
5. Run-pointer surfacing — prepend `[runnable: …]` to `contents` **or** extend `_format_documents`.
6. Shared `workflow_id → mcp_tool_name` (done: `doc_ids.mcp_tool_name_for`) used by both extractor and executor.
7. Disable per-manifest auto-registration in `generated_notebook_tools.py` (done) in favor of the generic executor.
8. **Agent-only indices** — extracted docs go to `indices.index_for(resource_type)` (separate from `OPENSEARCH_INDEX`). DONE: the **`agent_kb_search`** granular tool (`rag_pipeline/search/agent_kb.py`, wrapped in `agent_runtime/langchain_granular_tools.py`, registered in `graph_state.RAG_COMPONENT_TOOL_NAMES`) queries `indices.all_agent_indices()` (keyword + kNN, kNN stays within agent indices so no dim mismatch), normalizes hits with `source_index` + `parent_doc_id` + `runnable_tool`, and **resolves parent elements from the general index by `element_id`** so `citation_ids` point at the original knowledge element. The general platform search is untouched (separation preserved by which tool queries which index).

## 9. Risks

- **doc_id stability** on cell insert/reorder (order shifts) → reconcile by
  `extracted.parent_doc_id`; don't key on content hash.
- **Code embedding quality** → embed prose/signature, not raw code.
- **Arbitrary-exec safety** → the generic executor runs ingested code; sandbox +
  per-id opt-in required (promotion ≠ auto-execution).
- **SKILL name collisions** across repos → namespace the slug with `repo_id`.
- **`_format_documents` 500-char truncation** → prepend the runnable marker (or use
  the `_format_documents` extension).
- **Embedding-service / OpenSearch coupling** → index docs first, embed second.
- **Provenance cross-link fuzziness** (publication→implementation by name match) →
  store low-confidence edges; don't hard-fail.

## 10. Implementation order (follow-on branches)

1. **Notebook vertical slice** — `notebook_extractor` (wire R1 + builder, deterministic `workflow_id`) → unified manifest (dry-run JSON).
2. **Emitters** — `opensearch_emitter` (embed+index) → `mcp_emitter` (manifests) → `skill_emitter`. End-to-end on one repo (e.g. HAND).
3. **Remaining extractors** — `data_extractor` (format matrix), `code_extractor`, `publication_extractor` (+ provenance edges).
4. **Executor wiring** — implement the generic-executor registry lookup + sandbox/opt-in.
5. **Tests** — fixtures from the surveyed notebooks; golden run vs `papers/geopathfinder_sigspatial2026_short`.
