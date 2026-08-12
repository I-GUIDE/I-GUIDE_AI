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

---

## 2026-08-07 · M0.2 · Auth fails closed; CORS scoped

**Change** `api/server.py`: `_require_agent_chat_api_key` now raises `RuntimeError`
when `AGENT_CHAT_API_KEY` is unset, unless `AGENT_CHAT_AUTH_OPTIONAL=1` is set
explicitly. Added a `require_api_key` decorator and applied it to the four routes that
had no auth at all: `/query`, `/query/batch`, `/agent/files/upload`,
`/agent/files/<id>/download`. Replaced `CORS(app)` with `CORS(app, origins=_cors_origins())`,
reading `AGENT_CORS_ORIGINS` (comma-separated) and falling back to the existing
`ALLOWED_DOMAIN_LIST` JSON array. New `rag_pipeline/tests/test_api_auth.py` (24 cases).

**Why** Two independent holes. `_require_agent_chat_api_key` returned early on an unset
key — so auth was disabled by *omission*, which is the failure mode that never shows up
in testing because the tests also omit the key. And `/query`, the full RAG pipeline, had
only two auth call sites in the whole file (`:1286`, `:1781`) and was not one of them:
it was reachable unauthenticated from any origin.

The fail-closed contract was already designed and just never implemented — the
agent-chat routes at `:1289` already catch `RuntimeError` and return
*"Server misconfiguration: API key not set"*. Making the helper raise it made the
existing handlers correct without touching them.

**Measured** Unauthenticated data-bearing routes: **4 → 0**. Wildcard CORS: **1 → 0**.
Auth tests: **0 → 24**, all passing.

`rag_pipeline/tests/` — two runs, because the environment changes the answer:
- Without `.env` (this worktree's default): 501 passed, **3 failed**.
- With the main checkout's `.env` exported: **503 passed, 1 failed** in 151s.

So two of the three were purely `Missing required environment variable:
OPENSEARCH_NODE`, not defects. The remaining failure is
`test_spatial_routing_e2e::test_spatial_routing_to_generation_e2e`, an e2e test against
real backends, in a file this change does not touch. It also failed in the pre-change
prod-pinned run recorded in M0.1.

**Surprised by** Three existing fixtures relied on the fail-open behavior:
`test_download_route.py:23` even documented it — `monkeypatch.delenv("AGENT_CHAT_API_KEY")
# no auth in test`. That is the clearest possible evidence the hole was load-bearing in
practice rather than theoretical. All three now set `AGENT_CHAT_AUTH_OPTIONAL=1`
explicitly, which is the behavior they actually wanted.

`/health` and `/agent/dashboard` are deliberately left open — the first is the container
healthcheck, the second a static HTML page with no data. Both are asserted in the new
suite so a future change to either has to be deliberate.

**Next** M0.3, the eval metric fix. Until `retrieval_success` scores against the full
expected sets, no later milestone can show an improvement.

---

## 2026-08-07 · M0.3 · retrieval_success scores the full expected set

**Change** `scripts/eval_common.py:342`: `retr` now scores against `full_exp` /
`full_cov` instead of `meta["primary_ids"]`. Added `retrieval_recall` ("N/M") and kept
the old value as `primary_retrieval_success`. `task_status` deliberately untouched, so
this change moves exactly one metric and nothing else.

**Why** `retrieval_success` was computed from a hand-picked subset of each task's
expected elements, and `full_exp`/`full_cov` were already computed on the two lines
above and simply unused. The metric therefore read "yes" on every recorded run and was
structurally incapable of registering a retrieval improvement — which is the metric all
of M1.2, M3 and M4 are supposed to move.

**Measured** Re-scoring all 11 committed records:

| | before | after |
|---|---|---|
| `retrieval_success == "yes"` | **11/11** | **5/11** |
| aggregate recall over full expected sets | not reported | **25/37 (68%)** |

Per task, worst first: T8 **2/7**, T5 1/3, T4 3/5, T1 2/3, T10 2/3, T9 4/5, then
T7 3/3, T2 4/4, T3 2/2, T6 1/1, CRIME_HEATMAP 1/1.

**Surprised by** 25/37 here versus the **22/37** BM25 figure recorded in M0.1. Both are
correct and they measure different things: 22/37 is a single BM25 query per task at k=8,
25/37 is what the full agent pipeline actually grounded on (several retrieval methods,
query refinement, and the direct sweep). Worth keeping distinct — the first is the
retrieval ceiling for one method, the second is end-to-end behavior. `scripts/eval_retrieval.py`
(M0.4) will report the first properly across methods and k.

Also: no test imports `eval_common`, so this metric had no regression net at all. The
new `retrieval_recall` field is what M1.2's exit criterion will be read from.

**Next** M0.4 — `scripts/eval_retrieval.py`, so recall@k is measurable per method
without an LLM in the loop.

---

## 2026-08-07 · M0.4 · scripts/eval_retrieval.py — the retrieval instrument

**Change** New `scripts/eval_retrieval.py`. Deterministic, no LLM: recall@k,
precision@k and MRR over the full expected sets, for arbitrary `--k` across arms
`keyword | semantic | agent_kb | union | union+agent_kb`. Union arms fuse with RRF
(k=60), matching the agent's own reranker so a union figure is comparable to what the
agent would actually see. Writes JSON with `--json`.

**Why** `run_eval_cases.py` measures end-to-end agent behavior, where a retrieval
regression can hide behind a good answer and a retrieval gain can be masked by the model
failing to use it. M1.2, M3 and M4 all claim to move retrieval, so they need an
instrument that isolates it and costs nothing to re-run.

**Measured** Baseline against the live cluster, `keyword` arm, all 11 tasks / 37 ids:

| k | recall | |
|---|---|---|
| 8 (current effective window) | **22/37** | 59.5% |
| 20 | **29/37** | 78.4% |
| 50 | 33/37 | 89.2% |
| 100 | 34/37 | 91.9% |

This **independently reproduces** the M0.1 baseline through a different code path,
which is the result I most wanted from this step — the 22/37 and 29/37 figures the whole
M1.2 case rests on are now produced by committed, re-runnable code rather than a
throwaway script.

Missed by every arm even at k=100 — genuine indexing gaps, not window problems:
`afbee4bd` (T6), `643aaea1` (T9), `de05a428` (T10). Exactly 3, matching M0.1.

**Surprised by** Two things worth keeping. First, `semantic` reports **unavailable**
rather than scoring 0/37, because `semantic_search` returns `[]` both when the embedder
is down and when nothing matched — collapsing those would have silently understated
every union arm and made the embedding outage look like a retrieval quality problem.
Second, my initial "unreachable at every k" label was wrong: it listed 8 elements when
only k=8 and k=20 had been tested, 5 of which are recoverable at k=50. Fixed to "missed
at every k *tested*", with a pointer to raise `--k` to separate a ranking problem from
an indexing gap. A metric that overstates the size of a problem is as unhelpful as one
that hides it.

Note `outputs/` is gitignored (`.gitignore:63`), so the JSON is not committed —
regenerate with:
`python scripts/eval_retrieval.py --k 8,20,50,100 --methods keyword --json outputs/retrieval_$(date +%F).json`

**Next** M0.5 — `constraints.txt`, so the dev/prod version drift stops making every
later measurement ambiguous.
