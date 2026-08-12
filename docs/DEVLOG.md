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
  **1.0.10**; `pip install --dry-run -r requirements.txt` resolves to
  **1.3.14 / 1.5.3 / 1.2.10**. The unbounded `>=1.0` pins are real and confirmed by the
  resolver report.
  > ⚠️ **RETRACTED (see M0.5):** this entry originally claimed "repo suite against the
  > prod-resolved set: 550 passed, 9 failed". That measurement was invalid — the venv it
  > ran in had no `pyvenv.cfg`, `sys.prefix` was `/opt/anaconda3`, and its interpreter
  > resolved langchain from `~/.local` at **1.2.10**. It measured the dev stack, not the
  > prod stack. M0.5 re-measures with a verified-isolated venv.

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

---

## 2026-08-07 · M0.7 · Stop advertising a tool that cannot exist

**Change** `extractors/doc_ids.py`: `mcp_tool_name_for` now returns the real executor
name (`mcp_run_notebook_workflow` / `mcp_run_code_element`) instead of
`mcp_run_<workflow_id>`; added `run_invocation_for()` returning tool **and** argument,
since a name alone is not actionable for these executors. Both extractors' `contents`
markers changed from `[runnable: mcp_run_<wid>]` to `[workflow <wid>] … Not directly
callable; reuse the extracted functions.` `SkillSpec.allowed_tools` is now empty, and
`skill_emitter._render` emits an explicit "no single tool runs this" Run section instead
of silently omitting it. Hand-corrected the one shipped
`.agents/skills/ai-agent-for-chicago-crime-analysis/SKILL.md`.

**Why** `generic_executor_tools` registers exactly two fixed tools that take the workflow
id as an *argument*; it does not register one tool per workflow. So every
`mcp_run_nbwf_*` name ever emitted named a tool that could not exist — and one shipped
into a SKILL.md `allowed-tools` list, i.e. the model was explicitly told to invoke a
fiction. Worse, the docstring at `doc_ids.py:84` asserted the false belief
("The executor registers `run_<workflow_id>`"), so the bug was documented as intended
behavior.

The executors stay unreachable in beta regardless: `AGENT_ALLOW_WORKFLOW_EXEC` remains 0
and `generic_executor_tools` stays out of `DEFAULT_MCP_MODULES`, because the execution
body is a bare `exec()` of ingested source in the MCP server process. Hence "not directly
callable" rather than a corrected tool name — a truthful name for a gated tool would still
waste a turn.

**Measured** Emitted names that cannot resolve: **1 per promoted workflow → 0**.
Shipped SKILL.md files advertising a nonexistent tool: **1 → 0**. Suite unchanged
(501 passed, same 3 pre-existing failures).

**Next** M0.5, now materially larger — see the pyarrow finding below.

---

## 2026-08-07 · M0.5a · pyarrow is an undeclared dependency, and two geo tools are broken in prod

**Change** None yet — this entry records the finding, because it is a production defect
found while building the lock file and it deserves its own record.

**Why it matters** `pyarrow` appears in **no** requirements file, yet three code paths
call `to_parquet`:
- `agent_runtime/langchain_geo_tools.py:436` — `vector_reproject`
- `agent_runtime/langchain_geo_tools.py:476` — `vector_spatial_join`
- `extractors/geo_handles.py:42` — the GeoDataFrame file-handle mechanism (pickle fallback)

The dev machine has `pyarrow 24.0.0` from anaconda. A clean `pip install -r
requirements.txt` does not install it. So in the deployed image the two vector tools
return `{"ok": false, "error": "Missing optional dependency 'pyarrow.parquet'"}` and
`geo_handles` silently degrades to pickle — while both pass in dev.

**Measured** In a verified-isolated venv built only from `requirements.txt`:
`vector_spatial_join` → `ok: false`. That is the single extra failure in the prod-stack
suite run (**502 passed, 2 failed** vs dev's **503 passed, 1 failed**).

**Surprised by** How wrong my earlier framing was. I had recorded this as *LangChain*
version drift. It is not: the langchain triple moves nothing here. The real drift is the
scientific stack — prod resolves **pandas 3.0.5** against dev's **2.2.3** (a major
version), numpy 2.1.3 → 2.5.2, geopandas 1.1.2 → 1.1.4, and `fiona` appears from nowhere
at 1.10.1. For a geospatial platform a silent major pandas bump is a far larger risk than
a langchain minor, and neither was bounded. Also worth noting the resolver moved *within
this session*: the dry-run hours ago gave langchain 1.3.14, the real install gave 1.3.15.

**Next** M0.5 proper: add `pyarrow`, then build and *test* a constraints file rather than
freezing whatever resolves today.
