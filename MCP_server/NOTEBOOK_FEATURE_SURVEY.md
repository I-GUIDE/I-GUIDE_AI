# Notebook Feature Survey — Toward a Robust Notebook Workflow Builder

Status: planning artifact. Inputs: the HAND flood notebook (canonical hard case) +
17 real notebooks fetched & inspected across the I-GUIDE / CyberGIS / geospatial
ecosystem (hydrology·terrain·flood, pure-Python geospatial, ML/remote-sensing,
data-access·viz·HPC). Goal: enumerate the feature space a notebook→tool/workflow
extractor must handle, and the gap to close in
`MCP_server/notebook_workflow_builder.py`.

---

## 0. Canonical hard case — `HAND_py3.ipynb`

CyberGIS-Jupyter-for-Water HAND flood workflow (Onion Creek, TX). Defining traits:
shell/CLI orchestration (`!gdal*`, `!ogr2ogr`, `!mpirun … <TauDEM>`, `find_inlets_mr`);
**file-based dataflow** (DEM→fel→ang/p→ssa→src→hand→depth→addresses, implicit through
the filesystem); environment-bound (TauDEM/GDAL/MPI/`cybergis`/`/home/jovyan`);
IPython syntax that breaks `ast.parse` (`values=!cmd`, bare `ls`, `\`-continued `!`
lines, `exec(para)` dynamic vars); side-effect outputs (Floret maps + files); rich
markdown per step. Every one of these recurs in the broader corpus below.

---

## 1. Surveyed notebooks (all fetched & verified)

I-GUIDE org notebooks are marked ★.

| Notebook | Category | Orchestration | Dataflow | AST-breakers | Outputs |
|---|---|---|---|---|---|
| HAND_py3 | hydro/flood | shell CLI + MPI | file-based | `var=!`, bare `ls`, `\`-cont, `exec` | Floret maps, files |
| ★ WRF-Hydro `iguide_wrfhydro` | hydrology | CyberGIS-Compute ×3 + heavy `!sed/!wget` | file-based | many `!` (incl. `! cmd`), `!ls {var}` | plots, NetCDF, HTML banners |
| ★ Aging dams `iguide_agingdams` | flood | CyberGIS-Compute (`show_ui`) | mixed | **none** (pure Python) | choropleths, GeoDataFrames |
| ★ Pop. vuln. dam failure `Local_Analysis_Multi_Scenario` | flood/terrain | `subprocess` GDAL + `!pip` in try/except | mixed (file rasters + var) | indented `!pip` | maps, rasters, LISA stats |
| SUMMA ensemble HPC | hydrology | CyberGIS-Compute + `summa.exe` | mixed/file | 6× `!`, `%matplotlib`, `!{var}` | plots, NetCDF |
| rioxarray reproject_match | raster | none (lib calls) | **variable** (in-mem DataArrays) | `%matplotlib` | inline plots, return value |
| xarray-spatial Surface | terrain | none | **variable** (synthetic) | none | inline plots |
| leafmap 13_geopandas | vector/viz | none | mixed/variable | `!pip install` | **interactive map widget** |
| torchgeo trainers | ML | Lightning Trainer | file (ckpts) + var | `!uv pip`, `%tensorboard "$var"`, `%load_ext` | `.ckpt`, TB logs, metrics |
| geoai landcover seg | ML/RS | high-level API call | file-based (`.pth`) | `%pip` | model file, prediction `.tif` |
| geoai workshop 2025 | ML/RS | API + STAC | mixed/file | `%pip`, embedded `conda`/`git` | model, rasters, maps |
| Prithvi exploration (NASA) | ML/RS | `git lfs clone` weights | file + var | `!git lfs`, `!git clone`, `!mv`, `%pip` | reconstruction plots |
| CyberGIS-Compute RunExamples | HPC | `show_ui()` widget → SLURM | mixed | `!` + `{var}` interpolation | **widget UI**, SLURM logs, image |
| Earthdata-cloud-clinic | data access/viz | Harmony async API | API/streaming (S3) | `%matplotlib`, **raw YAML cell** | **hvplot/Bokeh widgets** |
| earthaccess access-local | data access | none | file-based (download) | **none** (pure Python) | HDF5 files, dask dataset |
| leafmap COG/STAC | data access/viz | TiTiler endpoint | API/URL | (commented `!pip`) | **interactive map widget** |
| leafmap intro | viz | none | variable | (commented `!pip`) | **interactive map widget** |

---

## 2. The feature space (dimensions a builder must handle)

### 2.1 Cell & syntax forms that break `ast.parse`
A plain `ast.parse` fails on the majority of notebooks. Observed offenders:
- `!cmd` shell bangs — incl. **`! cmd` with a space**, and **indented bangs inside
  `try/except`/`if`** (pop-vuln, WRF-Hydro).
- `var = !cmd` capture syntax (HAND); bare **auto-magics** (`ls`, `cd`) (HAND).
- line/cell magics `%matplotlib`, `%%time`, `%load_ext`, `%tensorboard`, `%pip`,
  `%%bash`; shell-var interpolation inside magics/bangs: `%tensorboard "$var"`,
  `!ls {pyvar}` (CyberGIS, WRF-Hydro).
- embedded non-Python: `conda create`/`mamba install`/`git clone` lines in code
  cells (geoai workshop, Prithvi); **raw cells with Quarto YAML front-matter**
  (NASA cookbook).
- **Implication:** never feed raw cell source to `ast.parse`. Use IPython's
  `InputTransformerManager` (`get_ipython().input_transformer_manager().transform_cell`)
  or `nbconvert`, *and* classify each transformed construct (see 2.5).

### 2.2 Orchestration model (no single shape)
- **Flat cell sequences with 0–1 functions** (HAND, WRF-Hydro, SUMMA, most ML &
  data-access notebooks). The current builder's "find a `run_workflow`/`main`
  entrypoint" assumption fails here.
- **Top-level functions as clean tool seams** (aging-dams ×4, pop-vuln ×6).
- **Shell-CLI pipelines** (HAND, WRF-Hydro `sed/wget`, pop-vuln `subprocess` GDAL).
- **HPC / remote-submission as a step**: CyberGIS-Compute `show_ui()` /
  `create_job_by_ui(defaultJob=…, input_params=…)` (4 notebooks), SLURM behind it,
  Harmony `submit→wait_for_processing→result_urls`. `defaultJob` is effectively the
  tool name; `input_params`/`defaultDataFolder` are its inputs. **Results land
  out-of-band** at `cybergis.recentDownloadPath` / `/home/jovyan/globus_download_{jobid}`,
  set only by user widget clicks — invisible to static analysis.

### 2.3 Dataflow style (must be inferred, not assumed)
- **File-based** (HAND, WRF-Hydro, SUMMA, geoai, earthaccess-local): steps
  communicate via named files; build the DAG from producer→consumer path matching,
  often through f-string/brace-templated paths keyed on job IDs.
- **Variable-based** (rioxarray, xarray-spatial, leafmap): in-memory objects
  (`gdf`, `xds`, `terrain`, `m`) threaded cell-to-cell; build the DAG via AST
  variable def/use analysis, distinguishing **in-place mutation** (`m.add_gdf(...)`)
  from **functional returns** (`xds.rio.reproject_match(...)`).
- **API/streaming** (earthaccess `open`→S3→xarray, COG/STAC over HTTP+TiTiler,
  Harmony): no local artifact; the edge is a live request.
- **Mixed / hand-off points** (pop-vuln, SUMMA): GDAL writes a file, then
  `gpd.read_file(intermediate)` turns a file edge into a variable edge. The builder
  must model **both edge types and the read-back seams**.

### 2.4 External data + auth/secrets
- Sources: HydroShare (`hs_restclient`), USACE FIM API, US Census ACS (API key),
  NASA Earthdata (`earthaccess` + `.netrc` → **temporary AWS creds**), Planetary
  Computer STAC, Overture Maps, HuggingFace datasets/weights (incl. git-LFS),
  remote GitHub release assets, TiTiler.
- Auth is **implicit & assumed-present**: `.netrc`, embedded demo creds
  (SUMMA `HydroShareAuthBasic("cybergis","demo")`), pasted API-key placeholders,
  AWS session tokens, CyberGISX identity.
- **Region/env locks**: Earthdata cloud clinic requires AWS **us-west-2**.
- **Implication:** detect auth touchpoints (`earthaccess.login`, `s3fs.S3FileSystem`,
  `HydroShareAuthBasic`, CyberGIS client init, `api.census.gov?...key=`) and
  externalize them as typed **secret parameters** — never inline captured tokens.

### 2.5 Environment & dependencies (mostly undeclared)
- **System binaries / CLI**: GDAL (`gdalinfo/gdalwarp/gdal_calc.py`), TauDEM,
  `mpirun`, `summa.exe`, `find_inlets_mr`, `wget/sed/grep/unzip`, `git lfs`.
- **Runtime installs**: `!pip install` / `%pip` / `!uv pip` at top *and* mid-notebook,
  sometimes in `try/except` — meaning the dependency set is not statically declared.
- **GPU/CUDA**: ML notebooks assume accelerators (metadata `GPU Class: standard`,
  `Execution Timeout: 1200s`), unpinned versions, large downloads.
- **Implication:** treat `!pip/%pip` as **dependency declarations** (not runtime
  logic); scan `import` + inline installs + `subprocess`/bang arg-0 for system-tool
  deps; capture GPU/runtime budgets; emit a container/env spec.

### 2.6 Outputs (heterogeneous; often non-serializable)
- **Return values / in-mem objects** (rioxarray DataArray, GeoDataFrames).
- **Files** (NetCDF, GeoTIFF, `.pth`/`.ckpt`, GeoJSON, shapefiles).
- **matplotlib plots** (side-effect rendering).
- **Interactive widgets** — ipyleaflet/leafmap Maps, hvplot/Bokeh sliders, geoviews,
  CyberGIS `show_ui()` Tab widget — **stateful, browser-side, non-serializable**,
  displayed via bare last-expression / display hook (not `print`/`return`).
- **`display(HTML(...))` status banners** that gate downstream cells.
- **Implication:** the builder must *define an explicit output contract* per tool
  (which file/object/figure is "the result"), and in a headless context either drop,
  snapshot (PNG/HTML), or expose-as-endpoint the interactive widgets.

### 2.7 Reproducibility hazards (pervasive)
Hardcoded paths (`/home/jovyan`, `./sample_data`, `../../test/...`); demo/placeholder
creds; live-API & time-dependent queries (STAC date ranges); no random seeds (MAE
masking, training) + GPU nondeterminism; no version pins; deprecated deps (`pygeos`);
`strict=False` weight loads that swallow mismatches; ephemeral `tempfile` artifacts;
interactive-only state (`show_ui()`, ROI selection); region locks.

---

## 3. Current builder vs. requirements

`MCP_server/notebook_workflow_builder.py` today:
1. reads cells via nbformat;
2. `_sanitize_line` **comments out** any line starting with `%%`/`%`/`!`/`?`;
3. concatenates all cells into one module, `ast.parse`es it;
4. finds top-level functions; picks entrypoint (`run_workflow`/`main`/`run` or first
   public fn); `mode="function"` else `"script"`;
5. records entrypoint params (fn args) + assigned-var "output candidates"; writes a
   manifest + source `.py`.

### Gap analysis (mapped to §2)
| Gap | Evidence in corpus | §ref |
|---|---|---|
| **Loses all shell/CLI work** (`!`/`%` commented out) | HAND, WRF-Hydro, SUMMA, pop-vuln, ML notebooks | 2.1, 2.2 |
| **Crashes on un-prefixed IPython syntax** (only catches lines *starting* with `!/%`; misses `var=!cmd`, bare `ls`, `!ls {var}` interpolation, raw YAML cells) | HAND, CyberGIS, NASA cookbook | 2.1 |
| **Assumes function-defined entrypoint**; flat notebooks degrade to opaque "script" | HAND, WRF-Hydro, SUMMA, ML, data-access | 2.2 |
| **No dataflow DAG** (neither file-based nor variable-based) | all | 2.3 |
| **No HPC/submission awareness** (CyberGIS-Compute/Harmony are the real steps) | 4 notebooks | 2.2 |
| **No auth/secret externalization** | Census, Earthdata, HydroShare | 2.4 |
| **No env/system-dep capture**; `!pip` discarded instead of promoted to deps | all shell notebooks | 2.5 |
| **Output = guessed var names**; ignores files/widgets/plots | all | 2.6 |
| **Ignores markdown** (loses ready-made descriptions) | all | — |
| **No reproducibility scaffolding / hazard flags** | all | 2.7 |

---

## 4. Requirements for a robust builder

**R1. IPython-aware front end.** Transform each cell with IPython's
`InputTransformerManager` (or nbconvert) before analysis; classify constructs:
`!pip/%pip/!uv pip`→**dependency**, `!wget/git clone/git lfs`→**data/model
acquisition step**, `!gdal*/!mpirun/subprocess([...])`→**CLI step** (capture arg-0 as
a system dep), magics→drop/translate, raw non-Python cells→ignore-with-note. Never
`ast.parse` raw source.

**R2. Dual dataflow model.** Build a DAG with **two edge types**: (a) file edges by
matching command/`subprocess`/path producers→consumers (incl. templated paths), and
(b) variable edges via AST def/use across cells (with mutation-vs-return awareness).
Model read-back seams (`gpd.read_file(intermediate)`) where a file edge becomes a
variable edge. Don't assume `def`s — synthesize step boundaries from cell ranges.

**R3. Recognize submission/remote-compute primitives.** First-class handling of
CyberGIS-Compute (`show_ui`/`create_job_by_ui(defaultJob, input_params, defaultDataFolder)`)
and Harmony (`submit/wait_for_processing/result_urls`) as **async job steps**:
`defaultJob`→tool, `input_params`→inputs, results at `recentDownloadPath`. Surface the
widget-set parameters so the step is executable headlessly.

**R4. Parameterize literals.** Promote hardcoded constants to typed params with
defaults: flood depth (`A<=5`), thresholds, CRS/EPSG, bbox, date windows, tile zoom,
paths, endpoints, `num_channels`/`num_classes` (tie to input band schema).

**R5. Externalize auth/secrets** (R2.4) as typed secret inputs; detect login/credential
touchpoints; never inline tokens.

**R6. Capture environment.** Emit a dependency manifest from imports + inline installs
+ CLI arg-0 + git/LFS; record GPU/CUDA + runtime/timeout budgets; produce a
container/conda spec. The env is the #1 reproducibility blocker (esp. HAND, SUMMA).

**R7. Define the output contract.** Per tool, pick/declare the result (final
file/object); for interactive widgets in headless mode, snapshot to PNG/HTML or expose
as endpoint; treat `display(HTML)` checkpoints as no-ops.

**R8. Mine markdown** for tool/step descriptions and parameter documentation (the
notebooks are well-documented — free, high-quality descriptions).

**R9. Reproducibility hardening.** Flag/inject: seeds, version pins, region/env
preconditions, live-API vs cacheable distinction (snapshot file-based inputs; mark
streaming/time-dependent steps non-deterministic), and side-effect temp-file
management.

**R10. Validation.** Where feasible, re-run the extracted unit on original inputs and
diff outputs (behavioral check) — optionally confirm the file-dataflow DAG via an
execution/file-access trace.

### Emit (per notebook)
1. typed **MCP/LangChain tools** (whole-workflow + step granularity) with schemas,
   defaults, descriptions, and a sandboxed body;
2. an **env/container spec**;
3. a **provenance/workflow object** (CWL/Snakemake/RO-Crate) + **Neo4j graph** nodes
   (steps, I/O, datasets, derived-from) for agent retrieval & "how was this made?"
   reasoning;
4. a **SKILL.md** from the markdown narrative.

---

## 5. Suggested implementation phasing
1. **R1 IPython front end** — unblocks every shell/ML/data notebook (today they're
   lost or crash). Highest leverage, contained change.
2. **R2 dual dataflow DAG** + R8 markdown descriptions + R4 literal params.
3. **R3 submission primitives** + R5 auth + R6 env capture (makes I-GUIDE/CyberGIS
   notebooks actually runnable as tools).
4. **R7 output contract** + R9 reproducibility + R10 validation.
5. Emit tools/provenance/skills and wire into the agent (MCP registration + RAG index
   + Neo4j).

(Source notebooks verified June 2026; see commit for raw URLs.)
