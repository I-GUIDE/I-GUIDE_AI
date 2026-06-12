"""Geospatial analysis as EXECUTED tool steps, with GeoDataFrames passed by file_id.

Demonstrates the file-handle pattern: the extracted Chicago spatial functions (pulled
from the agent KB via get_kb_block) are wrapped as file-handle tools and CHAINED — each
step persists its (Geo)DataFrame to a file and passes the file_id to the next. This is
the "geospatial flavor in execution steps" (no in-memory passing, no code-gen): the
chain doubles as a data-lineage trail.

Chain:  load_chicago_crime_data() → select_by_category(violent) → point_hotspot_map (heat map)
        + reuse the extracted spatial_join_and_count to also aggregate per community area.

Run:  python -m extractors.examples.geo_toolchain_demo
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("AGENT_FILE_STORAGE_ROOT", "/tmp/iguide_kb_demo")
os.environ.pop("AGENT_KB_BACKEND", None)
os.environ.pop("OPENSEARCH_NODE", None)

NB = str(Path.home() / "Downloads" / "code_agent_analysis.ipynb")
ELEMENT_ID = "ke_crimeagent"
VIOLENT = "HOMICIDE,BATTERY,ASSAULT,ROBBERY,CRIMINAL SEXUAL ASSAULT"


def _load_extracted_functions():
    """Pull the extracted block code from the KB and exec the spatial functions live."""
    from extractors.submission import Submission
    from extractors.ingest import ingest_submission
    from rag_pipeline.search.agent_kb import agent_kb_search, get_kb_block

    if Path(NB).exists():
        ingest_submission(Submission.from_payload({
            "element_id": ELEMENT_ID, "element_type": "notebook",
            "source": {"github_url": str(Path(NB).parent), "notebook_file": Path(NB).name},
            "fields": {"title": "Chicago Crime Code Agent", "tags": ["chicago", "crime"]},
            "targets": ["opensearch"]}))
    # find the block that defines the spatial tools and fetch its FULL code
    code = ""
    for hit in agent_kb_search("load chicago crime spatial join choropleth", size=8).get("documents", []):
        full = get_kb_block(hit["doc_id"])
        c = ((full.get("source") or {}).get("extracted") or {}).get("block", {}).get("code", "") if full.get("found") else ""
        if "def load_chicago_crime_data" in c:
            code = c
            break
    ns: dict = {}
    exec("import pandas as pd\nimport geopandas as gpd\nimport matplotlib\nmatplotlib.use('Agg')\n"
         "import matplotlib.pyplot as plt\n" + _strip_tool_decorators(code), ns)
    return ns


def _strip_tool_decorators(code: str) -> str:
    """Drop the @tool decorator lines so the plain functions exec without smolagents."""
    return "\n".join(l for l in code.splitlines() if l.strip() != "@tool")


def main() -> int:
    import geopandas as gpd  # noqa: F401
    from extractors.geo_handles import make_file_handle_tool

    ns = _load_extracted_functions()

    # --- analysis tools written as reusable, type-hinted functions (the "gap"), file-handle wrapped ---
    import geopandas as gpd
    def select_by_category(df: gpd.GeoDataFrame, column: str, values_csv: str) -> gpd.GeoDataFrame:
        """Keep rows whose `column` is in the comma-separated `values_csv` (case-insensitive)."""
        vals = {v.strip().upper() for v in values_csv.split(",")}
        return df[df[column].astype(str).str.upper().isin(vals)]

    def point_hotspot_map(gdf: gpd.GeoDataFrame, gridsize: int, title: str) -> None:
        """Render a hexbin point-density HEAT MAP of the geometry points."""
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 9))
        hb = ax.hexbin(gdf.geometry.x, gdf.geometry.y, gridsize=gridsize, cmap="inferno", mincnt=1)
        fig.colorbar(hb, ax=ax, label="violent crime cases")
        ax.set_title(title); ax.set_xlabel("longitude"); ax.set_ylabel("latitude")

    # build the tool set (extracted + analysis), all file-handle adapted
    T = {name: make_file_handle_tool(ns[name]) for name in
         ("load_chicago_crime_data", "load_chicago_community_areas", "spatial_join_and_count")}
    T["select_by_category"] = make_file_handle_tool(select_by_category)
    T["point_hotspot_map"] = make_file_handle_tool(point_hotspot_map)

    print("=" * 74)
    print("EXECUTED tool chain (GeoDataFrames passed by file_id)")
    print("=" * 74)
    steps = []

    r1 = T["load_chicago_crime_data"]()                                # extracted
    crime_fid = r1["file_id"]; steps.append(("load_chicago_crime_data [extracted]", r1))
    print(f"1. load_chicago_crime_data() → {crime_fid}  ({r1['rows']} rows)")

    r2 = T["select_by_category"](df=crime_fid, column="primary_type", values_csv=VIOLENT)  # analysis tool
    violent_fid = r2["file_id"]; steps.append(("select_by_category(violent)", r2))
    print(f"2. select_by_category(crime, violent) ← {crime_fid} → {violent_fid}  ({r2['rows']} violent)")

    r3 = T["point_hotspot_map"](gdf=violent_fid, gridsize=30, title="Violent crime density — Chicago (tool chain)")
    png_fid = r3.get("png_file_id"); steps.append(("point_hotspot_map [HEAT MAP]", r3))
    print(f"3. point_hotspot_map(violent) ← {violent_fid} → PNG {png_fid}")

    # reuse the extracted spatial GIS op as another executed step (per-community counts)
    rp = T["load_chicago_community_areas"](); comm_fid = rp["file_id"]
    rj = T["spatial_join_and_count"](gdf_polygons=comm_fid, gdf_points=violent_fid)  # extracted GIS
    counts_fid = rj["file_id"]
    print(f"4. load_chicago_community_areas() → {comm_fid}  ({rp['rows']} areas)")
    print(f"5. spatial_join_and_count(areas, violent) [extracted] ← {comm_fid},{violent_fid} → {counts_fid}")

    # report top communities from the counts file
    counts = __import__("extractors.geo_handles", fromlist=["read_geodata"]).read_geodata(counts_fid)
    top = counts.sort_values("crime_count", ascending=False).head(5)
    print("\ntop-5 community areas by violent crime:")
    for _, row in top.iterrows():
        print(f"   {row.get('community','?'):<22} {int(row['crime_count'])}")

    print("\n=== lineage (every step is a persisted artifact) ===")
    print(f"  crime={crime_fid} → violent={violent_fid} → heatmap_png={png_fid}")
    print(f"  communities={comm_fid} + violent={violent_fid} → counts={counts_fid}")
    from agent_runtime.file_store import resolve_file_id
    print(f"\n✅ heat map artifact: {resolve_file_id(png_fid) if png_fid else '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
