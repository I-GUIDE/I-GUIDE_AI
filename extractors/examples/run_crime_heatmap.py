"""REAL, reproducible end-to-end run of the preliminary example.

Pipeline (all local; no cluster, no LLM required):
  1. ingest code_agent_analysis.ipynb -> local agent KB
  2. agent_kb_search("heat map of violent crime")  -> find the relevant block
  3. get_kb_block(doc_id)                           -> pull the FULL block code
  4. REUSE: define & call the extracted `load_chicago_crime_data` VERBATIM
            (live Chicago Socrata API; falls back to the bundled sample for
             reproducibility when offline)
  5. WRITE: violent-crime category filter + a matplotlib hexbin HEAT MAP
  6. save the PNG and print a provenance ledger (reused vs written, cites element)

Run:
    python -m extractors.examples.run_crime_heatmap
    CRIME_DEMO_OFFLINE=1 python -m extractors.examples.run_crime_heatmap   # force the fixture
"""

from __future__ import annotations

import ast
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("AGENT_FILE_STORAGE_ROOT", "/tmp/iguide_kb_demo")
os.environ.setdefault("AGENT_GENERATED_SKILLS_ROOT", "/tmp/iguide_kb_demo/skills")
os.environ.pop("AGENT_KB_BACKEND", None)
os.environ.pop("OPENSEARCH_NODE", None)

HERE = Path(__file__).resolve().parent
NB = str(Path.home() / "Downloads" / "code_agent_analysis.ipynb")
FIXTURE = HERE / "sample_chicago_crime.csv"
OUT = HERE / "output" / "violent_crime_heatmap.png"
ELEMENT_ID = "ke_crimeagent"
QUERY = "show me a heat map of violent crime cases in chicago"
VIOLENT = {"HOMICIDE", "BATTERY", "ASSAULT", "ROBBERY", "CRIMINAL SEXUAL ASSAULT"}


def _extract_func_source(block_code: str, fname: str) -> str | None:
    try:
        tree = ast.parse(block_code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fname:
            return ast.get_source_segment(block_code, node)
    return None


def main() -> int:
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from extractors.submission import Submission
    from extractors.ingest import ingest_submission
    from rag_pipeline.search.agent_kb import agent_kb_search, get_kb_block

    ledger = {"reused": [], "written": [], "source": None, "cite": ELEMENT_ID}

    # 1. ingest
    if Path(NB).exists():
        ingest_submission(Submission.from_payload({
            "element_id": ELEMENT_ID, "element_type": "notebook",
            "source": {"github_url": str(Path(NB).parent), "notebook_file": Path(NB).name},
            "fields": {"title": "Chicago Crime Code Agent", "tags": ["chicago", "crime", "geospatial"]},
            "targets": ["opensearch"],
        }))

    # 2. retrieve + 3. fetch the full block that defines the loader
    loader_src = None
    loader_block_id = None
    hits = agent_kb_search(QUERY, size=8).get("documents", [])
    for h in hits:
        full = get_kb_block(h["doc_id"])
        code = ((full.get("source") or {}).get("extracted") or {}).get("block", {}).get("code", "") if full.get("found") else ""
        src = _extract_func_source(code, "load_chicago_crime_data")
        if src:
            loader_src, loader_block_id = src, h["doc_id"]
            break

    # 4. REUSE — define & call the extracted loader verbatim (live; fallback to fixture)
    gdf = None
    if loader_src and not os.getenv("CRIME_DEMO_OFFLINE"):
        ns: dict = {}
        try:
            import geopandas as gpd  # noqa: F401
            exec("import pandas as pd\nimport geopandas as gpd\n" + loader_src, ns)
            with ThreadPoolExecutor(max_workers=1) as ex:
                gdf = ex.submit(ns["load_chicago_crime_data"]).result(timeout=60)
            ledger["source"] = f"LIVE Chicago Socrata API (reused {loader_block_id}::load_chicago_crime_data verbatim)"
            ledger["reused"].append(f"load_chicago_crime_data() — from {loader_block_id}")
        except Exception as exc:
            gdf = None
            ledger["reused"].append(f"load_chicago_crime_data() extracted from {loader_block_id} (live call failed: {type(exc).__name__}) → fixture")

    if gdf is None:
        gdf = pd.read_csv(FIXTURE)
        ledger["source"] = ledger["source"] or f"OFFLINE fixture {FIXTURE.name} (reproducible)"
        if not ledger["reused"]:
            ledger["reused"].append(f"(loader source located in {loader_block_id})" if loader_block_id else "(loader not found)")

    # normalize the columns we need
    df = pd.DataFrame({"primary_type": gdf["primary_type"].astype(str),
                       "lon": pd.to_numeric(gdf["longitude"], errors="coerce"),
                       "lat": pd.to_numeric(gdf["latitude"], errors="coerce")}).dropna(subset=["lon", "lat"])

    # 5. WRITE — violent-crime category filter (the gap the notebook didn't cover)
    v = df[df["primary_type"].str.upper().isin(VIOLENT)]
    ledger["written"].append(f"violent-crime category filter → {len(v)}/{len(df)} cases "
                             f"({sorted(set(v['primary_type'].str.upper()) & VIOLENT)})")

    # 5b. WRITE — the heat map (no heat tool was extracted; only a choropleth)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 9))
    hb = ax.hexbin(v["lon"], v["lat"], gridsize=30, mincnt=1, cmap="inferno")
    fig.colorbar(hb, ax=ax, label="violent crime cases")
    ax.set_title("Heat map of violent crime — Chicago")
    ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
    fig.savefig(OUT, dpi=130, bbox_inches="tight")
    ledger["written"].append(f"hexbin heat-map render → {OUT}")

    # 6. provenance ledger
    print("=" * 72)
    print("REAL RUN — provenance ledger")
    print("=" * 72)
    print(f"data source : {ledger['source']}")
    print(f"cites element: {ledger['cite']}")
    print("\n♻️  REUSED (extracted, executed verbatim):")
    for r in ledger["reused"]:
        print("   ✓", r)
    print("\n✍️  WRITTEN (the code peer filled these):")
    for w in ledger["written"]:
        print("   ✎", w)
    print(f"\n✅ heat map saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
