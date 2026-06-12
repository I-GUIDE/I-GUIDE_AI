# Preliminary example — from extraction to agent execution

A worked story showing how an ingested notebook becomes agent-usable knowledge, and
how the agent **reuses** extracted assets vs **writes** new code for a query the
notebook never directly answered.

Source notebook: `code_agent_analysis.ipynb` — a Chicago-crime "code agent" demo that
defines five geopandas tools and runs a *theft-by-community-area choropleth* task.

---

## Act 1 — Submission → extraction

A user registers the notebook on the platform form. The platform assigns
`element_id = ke_crimeagent` and POSTs a submission to the ingestion webhook
(`element_type=notebook`, `notebook_file=code_agent_analysis.ipynb`, `fields={title,
tags, abstract}`). `ingest_submission` runs the **NotebookExtractor** → **local agent
KB** (`iguide_agent_notebook_blocks`). Verified output:

- **5 NotebookBlocks** + **1 workflow asset**, all anchored on `ke_crimeagent`:
  - `ke_crimeagent::block::3` — **DEPENDENCY**: `[smolagents, geopandas, matplotlib, geodatasets]` (the env spec).
  - `ke_crimeagent::block::7` — **the tool-definition cell**, containing the five functions:
    `load_chicago_community_areas`, `load_chicago_crime_data`,
    `filter_dataframe_by_value`, `spatial_join_and_count`, `plot_choropleth_map`
    — *with their real implementations* (the Socrata crime URL, the `points_from_xy`
    GeoDataFrame construction, the `sjoin`+groupby count, the choropleth plot).
  - `ke_crimeagent::block::9` — builds the `CodeAgent` with those tools.
  - `ke_crimeagent::block::12` — the example task (`run("...theft...top 10...map")`).
  - workflow asset `ke_crimeagent::workflow` → `mcp_run_nbwf_abb3511cb1f4fa93` (script/function-mode pointer).
- **Skill**: `chicago-crime-code-agent` (allowed-tools = the workflow run tool).

Each block is **linked to the original element** (`parent_doc_id = ke_crimeagent`) and
inherits `title/tags/abstract`, so any retrieval cites the source notebook.

> Honest caveat: at notebook granularity the five functions land in **one block**
> (the cell that defines them), not five separate callables; and the whole-notebook
> "runnable" entrypoint heuristic picked `load_chicago_community_areas` (a weak pick —
> the notebook isn't a parameterized pipeline). Neither hurts this example: the value
> here is the **block's real, working code as grounding** for the code peer.

---

## Act 2 — The query: "show me a heat map of violent crime cases"

The supervisor routes to the **search peer**, which calls `agent_kb_search("heat map of
violent crime cases in chicago")`. Verified — it returns (backend=local), all linked
to `ke_crimeagent`:

```
ke_crimeagent::block::3   (deps)
ke_crimeagent::block::7   (the five geopandas functions)   ← the gold
ke_crimeagent::block::9   (agent assembly)
ke_crimeagent::block::12  (example task)
```

So the agent now has, as **grounded evidence**, the notebook's actual data-access and
spatial code — not a hallucinated guess at how to get Chicago crime data.

---

## Act 3 — Reuse vs. write (the crux)

| Need for "heat map of violent crime" | Covered by extraction? | Action |
|---|---|---|
| Load Chicago crime incidents (points, with `primary_type`, lon/lat) | ✅ `load_chicago_crime_data()` — exact Socrata URL + GeoDataFrame build | **REUSE verbatim** |
| Load community-area boundaries | ✅ `load_chicago_community_areas()` | reuse if needed (not needed for a point heat map) |
| Select **violent** crimes | ⚠️ partial — `filter_dataframe_by_value(df, col, value)` matches **one** exact `primary_type` | **WRITE**: "violent crime" is a *category* (HOMICIDE, BATTERY, ASSAULT, ROBBERY, CRIM SEXUAL ASSAULT, …) → filter by set membership |
| **Heat map** of cases | ❌ only `plot_choropleth_map` (area-count choropleth) exists; no density/heat map | **WRITE**: the missing piece — a point-density render (hexbin / KDE / folium HeatMap) |
| Count per area | ✅ `spatial_join_and_count` | not needed for a heat map (that's the choropleth path) |
| Choropleth render | ✅ `plot_choropleth_map` | wrong visual for a "heat map" → not reused |

**The piece the code peer must write** = (1) a violent-crime **category filter**, and
(2) the **heat-map rendering** (the notebook only knows area choropleths). Everything
upstream — *getting and shaping the data* — is reused from the extracted block, which
is exactly where hallucination risk is highest (data source URLs, API params, CRS).

---

## Act 4 — Execution through the supervisor-over-peers graph

1. **supervisor → search**: `agent_kb_search` → block::7 etc. enter shared `evidence`.
2. **supervisor → code**: the code peer reads the evidence (via `_format_documents`) —
   it now *has* `load_chicago_crime_data`'s real body — and writes the gap:

   ```python
   # REUSED (grounded on ke_crimeagent::block::7): load_chicago_crime_data()
   gdf = load_chicago_crime_data()

   # WRITTEN by the code peer — violent-crime category (not one primary_type)
   VIOLENT = {"HOMICIDE","BATTERY","ASSAULT","ROBBERY","CRIMINAL SEXUAL ASSAULT"}
   v = gdf[gdf["primary_type"].str.upper().isin(VIOLENT)]

   # WRITTEN by the code peer — heat map (no such tool was extracted)
   import matplotlib.pyplot as plt
   fig, ax = plt.subplots(figsize=(10,10))
   hb = ax.hexbin(v.geometry.x, v.geometry.y, gridsize=50, mincnt=1)
   fig.colorbar(hb, ax=ax, label="violent crime cases")
   ax.set_title("Heat map of violent crime — Chicago"); ax.set_axis_off()
   ```
3. **supervisor → synthesize**: composes the answer + a grounding audit, and **cites
   the original element** `ke_crimeagent` (the source notebook), not the synthetic
   block id. If the notebook's workflow had been a clean parameterized pipeline, the
   `[runnable: mcp_run_…]` pointer would let the agent *run* it instead; here it
   adapts the reused code.

---

## Why this is a good preliminary example

- It shows the **division of labor**: extraction supplies *trusted, working
  data/spatial code* (the hard-to-guess part); the code peer supplies the *novel
  query-specific logic* (violent-category filter + heat-map render).
- It shows **grounding + citation**: the answer is anchored to a real submitted
  notebook via `element_id`.
- It shows the **honest gap** that motivates code-writing: the KB had a *choropleth*,
  the user wanted a *heat map* — so reuse gets you 80% (the data + filtering pattern)
  and the agent writes the last 20%.

(Reproduce: ingest `code_agent_analysis.ipynb` as `ke_crimeagent` into the local KB,
then `agent_kb_search("heat map of violent crime cases")`.)
