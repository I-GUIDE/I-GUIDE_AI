# Development log — extraction restructure → deployable beta

One entry per step, appended in the **same commit** as the change it describes.
Plan: `~/.claude/plans/based-on-our-discussion-lazy-cray.md`.

Entry format:

```markdown
## YYYY-MM-DD · <milestone id> · <short title>
**Change** what changed, with file:line
**Why** what was wrong before — the defect, not the intention
**Measured** the number that moved: before -> after
**Surprised by** anything the change revealed that we did not expect (omit if nothing)
**Next** what this unblocks or what it forces
```

`Measured` makes each milestone's exit criterion auditable. `Surprised by` is where
findings get captured before they are forgotten — it is also the raw material for the
SIGSPATIAL methods and results sections.

Every LLM-touching entry records the model used. A recall number produced under `opus`
is not comparable to one produced under a self-hosted 7B.

---

## 2026-08-07 · M0.1 · Baseline measured before any change

**Change** Added this log. No code touched.

**Why** The plan's exit criteria are all expressed as numbers that must move. Those
numbers are worthless without a recorded starting point, and several of them were
measured during planning against live infrastructure that will drift.

**Measured** — baseline, all verified directly rather than inferred:

*Corpus and retrieval*
- `new-opensearch-index`: **619 docs** — publication 180, map 160, notebook 133,
  dataset 87, oer 32, code 27.
- Live platform catalog (`backend.i-guide.io/api/elements`): **750 elements**. The
  index is therefore **131 elements (17%) stale**. `iguide-feb12` exists on the
  cluster and is fresher.
- BM25 recall over the benchmark's **full** expected sets (37 ids across 11 tasks),
  each task's verbatim prompt as the query:
  | k | recall |
  |---|---|
  | 8 (current effective window) | **22/37 (59%)** |
  | 20 | **29/37 (78%)** |
  | 50 | 33/37 (89%) |
  | 100 | 34/37 (92%) |
- Three expected elements are unreachable by BM25 at **any** k — `afbee4bd` (T6,
  Open-Meteo notebook), `643aaea1` (T9), `de05a428` (T10). These are genuine indexing
  gaps, not window problems.
- Per-task at k=8 → k=20: T8 **3/7 → 7/7**, T7 1/3 → 2/3, T5 2/3 → 3/3, T1 2/3 → 2/3.
- Semantic search was **not** exercised: `FLASK_EMBEDDING_URL` in `.env` points at
  `149.165.159.254:5000`, which refuses connections. All figures above are BM25 only,
  so the real ceiling is higher.

*Agent-side*
- `iguide_agent_*` indices on the live cluster: **0 of 4 exist**.
- Agent KB: **290 blocks from 14 of 133 notebooks (10.5%)**, in a single local JSON at
  `agent_chat_files/agent_kb/iguide_agent_notebook_blocks.json`.
- Publications with reachable full text (`pdf_chunks`): **34 of 180**, and **no search
  tool queries them**.
- `opengeodata_search` calls across 11 recorded eval runs: **0**.
- MCP tools reaching a model in a default supervisor run: **4** of 16 native
  (`default_search_fn` defaults `include_mcp_tools=False` at `supervisor/graph.py:1428`;
  `default_analyze_fn` scopes to `spatial_analysis_tools` at `:1658`).

*Quality gates*
- Tests importing anything from `extractors/` or the emitters: **0**, across ~3,250 LOC.
- `_AUDIT_FLAG_SEVERITIES = {"high"}` (`supervisor/graph.py:418`); severities ever
  emitted across all eval records are `low` ×4 and `none` ×40. The user-visible
  grounding caveat has fired **zero times**, so "0 hallucinations in 21 runs" is the
  null output of a detector that has never triggered.
- `retrieval_success` is pinned at **10/10** by construction: `scripts/eval_common.py`
  scores it against `TASK_META[tid]["primary_ids"]` (a subset) while computing
  `full_exp` on the line above.
- Dev/prod dependency drift: dev runs langchain **1.2.10** / core **1.4.9** / langgraph
  **1.0.10**; `pip install -r requirements.txt` resolves to **1.3.14 / 1.5.3 / 1.2.10**.
  Repo suite against the prod-resolved set: **550 passed, 9 failed, 1 skipped** (5 are
  `tests/live/*` needing env; substantive: `test_state_uniformity.py` ×2,
  `test_langchain_geo_tools.py::test_spatial_join`).

*Execution*
- Sandbox: `python:3.11-slim`, **512m / 1.0 cpu / 60s / 256 pids**, `--network none`.
- Workspace lifetime: **destroyed after every call** —
  `code_execution.py:279` `tempfile.mkdtemp()` and `:309` `shutil.rmtree(work)` in a
  `finally`. Step 2 cannot read step 1's output.
- Median recorded turn: **37s** (range 12–232s).

*Access*
- Reachable: platform API, OpenSearch, GitHub raw (8/12 notebooks on a naive
  `main`/`master` probe), MinIO `storage-dev.i-guide.io` (200).
- Not reachable from a dev machine: Neo4j `10.0.147.52:7687` (private subnet), the
  embedding server (stale `.env` host).

**Surprised by** Two findings inverted planned work. (1) `AGENT_ALLOW_WORKFLOW_EXEC=1`
routes ingested third-party notebook source into a bare
`exec(compile(...), namespace, namespace)` **in the MCP server process**
(`MCP_server/tools/generated_notebook_tools.py:43-47`), which has network, the shared
`agent_chat_files` volume, and cluster credentials — so the flag stays `0` and the
`mcp_run_nbwf_*` path gets deleted rather than enabled. (2) The prod mapping for
`spatial-bounding-box-geojson` is `{type: geo_shape, ignore_malformed: true}`, so the
dataset native-CRS bounds bug **silently drops the field** instead of failing the write;
that is why only 182/619 docs carry a bbox.

**Next** M0.2 (fail-closed auth) — nothing else is deployable until the unauthenticated
`/query` route is closed.
