"""Preliminary example (executable): extraction → agent KB → reuse/gap plan.

Ingests a notebook into the LOCAL agent KB, runs the agent's KB search for a user
query, discovers the callable functions the extraction captured, and derives — from
those real functions — what the agent can REUSE vs the piece it must WRITE.

Run:
    python -m extractors.examples.crime_heatmap_demo [notebook.ipynb] ["user query"]

Defaults to ~/Downloads/code_agent_analysis.ipynb and the violent-crime heat-map query.
Everything is local: no OpenSearch/Neo4j, no LLM required.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Dict, List

# Self-contained local KB store unless the caller set one.
os.environ.setdefault("AGENT_FILE_STORAGE_ROOT", "/tmp/iguide_kb_demo")
os.environ.setdefault("AGENT_GENERATED_SKILLS_ROOT", "/tmp/iguide_kb_demo/skills")
os.environ.pop("AGENT_KB_BACKEND", None)        # force local
os.environ.pop("OPENSEARCH_NODE", None)

ELEMENT_ID = "ke_crimeagent"
DEFAULT_NB = str(Path.home() / "Downloads" / "code_agent_analysis.ipynb")
DEFAULT_QUERY = "show me a heat map of violent crime cases in chicago"


def _discover_functions(manifest) -> Dict[str, str]:
    """name -> first docstring line, from every extracted NotebookBlock's code."""
    fns: Dict[str, str] = {}
    for asset in manifest.to_dict().get("assets", []):
        code = (asset.get("block") or {}).get("code") or ""
        if "def " not in code:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = (ast.get_docstring(node) or "").strip().splitlines()
                fns[node.name] = doc[0] if doc else ""
    return fns


def _find(fns: Dict[str, str], *, name_has=(), doc_has=()) -> List[str]:
    out = []
    for n, d in fns.items():
        hay = (n + " " + d).lower()
        if all(t in n.lower() for t in name_has) and all(t in hay for t in doc_has):
            out.append(n)
    return out


def _plan(fns: Dict[str, str], query: str):
    """Derive reuse vs write from the real extracted functions + the query."""
    reuse, write, notes = [], [], []

    loaders = _find(fns, name_has=("load",))
    crime_loader = [f for f in loaders if "crime" in f.lower()]
    if crime_loader:
        reuse.append((crime_loader[0], "loads the source data (URL/API + GeoDataFrame) — reuse verbatim"))
    else:
        write.append(("data loader", "no extracted loader for this data"))

    filters = _find(fns, name_has=("filter",))
    if filters:
        write.append((f"category filter (adapts {filters[0]})",
                      f"{filters[0]} matches ONE exact value; 'violent crime' is a CATEGORY "
                      "(HOMICIDE/BATTERY/ASSAULT/ROBBERY/…) → write set-membership filter"))

    heat = _find(fns, doc_has=("heat",)) + _find(fns, doc_has=("density",)) + \
           _find(fns, name_has=("hexbin",)) + _find(fns, name_has=("kde",))
    choropleth = _find(fns, name_has=("choropleth",)) or _find(fns, name_has=("map",))
    if heat:
        reuse.append((heat[0], "renders a heat map — reuse"))
    else:
        gap = "no heat-map/density tool was extracted"
        if choropleth:
            gap += f" (only {choropleth[0]}, an area choropleth ≠ a point heat map)"
        write.append(("heat-map render (hexbin/KDE)", gap + " → write the point-density plot"))

    return reuse, write, notes


def main(argv: List[str]) -> int:
    nb = argv[1] if len(argv) > 1 else DEFAULT_NB
    query = argv[2] if len(argv) > 2 else DEFAULT_QUERY
    if not Path(nb).exists():
        print(f"notebook not found: {nb}\nUsage: python -m extractors.examples.crime_heatmap_demo [nb] [query]")
        return 1

    from extractors.submission import Submission
    from extractors.ingest import ingest_submission
    from rag_pipeline.search.agent_kb import agent_kb_search

    print("=" * 72)
    print("ACT 1 — submission → extraction (local agent KB)")
    print("=" * 72)
    m = ingest_submission(Submission.from_payload({
        "element_id": ELEMENT_ID, "element_type": "notebook",
        "source": {"github_url": str(Path(nb).parent), "notebook_file": Path(nb).name},
        "fields": {"title": "Chicago Crime Code Agent", "tags": ["chicago", "crime", "geospatial"],
                   "abstract": "A code agent that analyzes Chicago crime with geopandas spatial tools."},
        "targets": ["opensearch", "mcp", "skill"],
    }))
    d = m.to_dict()
    blocks = [a for a in d["assets"] if not a.get("runnable")]
    print(f"element_id={d['element_id']}  blocks={len(blocks)}  "
          f"workflow={sum(1 for a in d['assets'] if a.get('runnable'))}  skill={ (d.get('skill') or {}).get('name') }")
    print("kb:", [w for w in d["warnings"] if w.startswith("[kb")])

    fns = _discover_functions(m)
    print("\nfunctions captured in the extracted blocks:")
    for n, doc in fns.items():
        print(f"   • {n}() — {doc}")

    print("\n" + "=" * 72)
    print(f"ACT 2 — user asks: {query!r}")
    print("=" * 72)
    r = agent_kb_search(query, size=5)
    print(f"agent_kb_search → backend={r['backend']} count={r['count']} "
          f"cites element(s)={sorted(set(r['citation_ids']))}")
    for doc in r["documents"]:
        print(f"   hit {doc['doc_id']}  (from: {(doc.get('element') or {}).get('title')})")

    print("\n" + "=" * 72)
    print("ACT 3 — reuse vs. write (derived from the extracted functions)")
    print("=" * 72)
    reuse, write, _ = _plan(fns, query)
    print("\n♻️  REUSE (grounded on the extracted notebook):")
    for name, why in reuse:
        print(f"   ✓ {name} — {why}")
    print("\n✍️  WRITE (the code peer fills these):")
    for name, why in write:
        print(f"   ✎ {name} — {why}")
    print("\nUpstream data/shaping is reused (highest hallucination risk); the agent "
          "writes only the query-specific logic. Answer cites the original element "
          f"'{ELEMENT_ID}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
