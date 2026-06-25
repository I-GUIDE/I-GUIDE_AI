"""Build the LOCAL agent-KB for the GeoPathfinder eval from REAL prod elements.

Downloads each notebook's source via the public element API (agent_runtime.element_resolver),
ingests it through the notebook extractor into the local agent-KB (AGENT_KB_BACKEND forced
to 'local' — the production cluster is never touched), then reports per-notebook extraction
results and a grounding/precision check for the eval cases.

Usage:
    python scripts/build_eval_kb.py                 # download (cached) + ingest + check
    python scripts/build_eval_kb.py --force         # re-download sources
    python scripts/build_eval_kb.py --skip-datasets # notebooks only
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# LOCAL agent-KB before importing anything that reads the backend.
os.environ["AGENT_KB_BACKEND"] = "local"
os.environ.setdefault("AGENT_FILE_STORAGE_ROOT", str(REPO / "agent_chat_files"))

from agent_runtime.element_resolver import download_element_source

# --- eval corpus (real PROD element_ids) ----------------------------------- #
NOTEBOOKS = [
    ("cca9b545-8416-45a3-9267-122ce6ce9991", "crime anchor"),
    ("bb14c9ea-9b19-49e1-8d5f-a6ea0cbdd921", "heat anchor (City-level Analysis at Chicago)"),
    ("df24faf3-608b-4f50-839d-0da5f9db765d", "Choropleth Maps (classification)"),
    ("c310aac7-a648-4081-95cc-a82e41073f9d", "Community Map Demo (Folium)"),
    ("4a06e4a1-5308-4d2c-9e17-7803ae5dc7f0", "Social Media Socioeconomic Analysis"),
    ("21788323-9782-4219-8065-dbf53fb157c8", "Data Collection"),
    ("6c518fed-0a65-4858-949e-24ee8dc4d85b", "National-level Analysis (constraint)"),
    ("791fa878-e227-4953-b07d-1fbb5176ace5", "CDC Social Vulnerability Index"),
    # new-domain anchors (reproducible Python; distinct data-acquisition patterns)
    ("afbee4bd-f88e-4507-8738-3b33e2ab65b1", "NEW-DOMAIN: weather (Open-Meteo REST API)"),
    ("8a77279b-d8e4-4475-b3ff-b49320ce2b58", "NEW-DOMAIN: OSM street network (OSMNX)"),
    ("d8926bb3-864d-4542-8027-02fc6edc868f", "NEW-DOMAIN: GWR spatial regression (bundled data)"),
    ("5278e805-168c-448e-95b8-056ff3b6b8c3", "DISTRACTOR: FireABM"),
    ("bf9466a0-dab4-4d44-af12-8f18bfbcdf02", "DISTRACTOR: Agent-based Land Market"),
    ("3b8c4c57-40c9-4bbe-9517-cd646541ae9d", "DISTRACTOR: dam failures"),
]
DATASETS = [
    ("1efb4820-548c-49b5-8a91-8474f201588b", "Chicago Communities"),
    ("1dc95b98-e09d-4938-97b3-adec779014de", "Census-tract Chicago boundary"),
    ("1628189a-0654-4031-b7e6-b6568011e341", "Intermediate Results (heat)"),
    ("c790a8f2-ab81-4e42-8671-5c44922c29e5", "Historic Redlining Scores"),
]
EXPECT = {  # query -> expected notebook element_ids to ground on (agent-KB has notebooks only)
    "1 exact-reuse (heat)": ("map heat exposure sentiment by chicago census tract 2021-09-25",
                             ["bb14c9ea"]),
    "1b exact-reuse (crime)": ("show a heat map of violent crime cases in chicago",
                               ["cca9b545"]),
    "2 parametric": ("classify chicago heat sentiment choropleth with jenks natural breaks",
                     ["bb14c9ea", "df24faf3"]),
    "3 compositional": ("show chicago heat sentiment as an interactive folium map",
                        ["bb14c9ea", "c310aac7"]),
    "4 cross-contribution": ("chicago tracts with high heat sentiment and low income",
                             ["bb14c9ea", "4a06e4a1"]),
    "6 hidden-constraint": ("map national heat exposure sentiment for september 2021",
                            ["6c518fed"]),
    "W weather (new domain)": ("fetch and plot recent daily temperature time series weather open-meteo",
                               ["afbee4bd"]),
    "O osm (new domain)": ("build and summarize a street road network for a place with osmnx",
                           ["8a77279b"]),
    "G gwr (new domain)": ("geographically weighted regression on an example spatial dataset",
                           ["d8926bb3"]),
}
DISTRACTORS = {"5278e805": "FireABM", "bf9466a0": "ALMA", "3b8c4c57": "dam-failures"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download sources")
    ap.add_argument("--skip-datasets", action="store_true")
    args = ap.parse_args()

    from extractors.submission import Submission
    from extractors.ingest import ingest_submission

    nb_dir = Path(os.environ["AGENT_FILE_STORAGE_ROOT"]) / "eval_notebooks"
    nb_dir.mkdir(parents=True, exist_ok=True)
    print(f"agent-KB (local): {os.environ['AGENT_FILE_STORAGE_ROOT']}/agent_kb")
    print(f"notebook downloads: {nb_dir}\n")

    ingested = []
    for eid, role in NOTEBOOKS:
        info = download_element_source(eid, nb_dir, force=args.force)
        path = info.get("path")
        title = info.get("title") or "<no metadata>"
        print(f"-> {eid[:8]} [{role}]  '{title[:48]}'")
        if not path:
            print(f"     SKIP (no source): {info.get('note')}")
            continue
        fields = {"title": info.get("title") or "", "tags": info.get("tags") or [],
                  "authors": info.get("authors") or [], "abstract": info.get("abstract") or ""}
        try:
            manifest = ingest_submission(Submission.from_payload({
                "element_id": eid, "element_type": "notebook",
                "source": {"github_url": str(nb_dir), "notebook_file": Path(path).name},
                "fields": fields, "targets": ["opensearch"],
            }))
            d = manifest.to_dict()
            assets = d.get("assets") or []
            has_wf = any(a.get("runnable") for a in assets)
            warns = d.get("warnings") or []
            print(f"     blocks={len(assets)} workflow={'yes' if has_wf else 'no'} warnings={len(warns)}")
            for w in warns[:2]:
                print(f"       · {w}")
            ingested.append(eid)
        except Exception as exc:
            print(f"     EXTRACTION FAILED: {type(exc).__name__}: {exc}")

    if not args.skip_datasets:
        ds_dir = Path(os.environ["AGENT_FILE_STORAGE_ROOT"]) / "eval_datasets"
        print(f"\nData dependencies -> {ds_dir}")
        for eid, name in DATASETS:
            info = download_element_source(eid, ds_dir, force=args.force)
            print(f"  {eid[:8]} {name:<32} -> {Path(info['path']).name if info.get('path') else info.get('note')}")

    if not ingested:
        print("\nNo notebooks ingested.")
        return 1

    from rag_pipeline.search.agent_kb import agent_kb_search
    print("\n" + "=" * 72)
    print("GROUNDING + PRECISION (agent_kb_search over the local agent-KB)")
    print("=" * 72)
    for label, (q, expected) in EXPECT.items():
        hits = agent_kb_search(q, size=6).get("documents", [])
        got = [h.get("doc_id", "") for h in hits]
        hit_expected = [e for e in expected if any(g.startswith(e[:8]) for g in got)]
        distractor_hits = [DISTRACTORS[d] for d in DISTRACTORS if any(g.startswith(d[:8]) for g in got)]
        print(f"\n[{label}]  q={q!r}")
        print(f"   grounded on expected: {len(hit_expected)}/{len(expected)} {hit_expected}")
        print(f"   distractors retrieved: {distractor_hits or 'none'}")
        for h in hits[:4]:
            print(f"     [{float(h.get('score', 0)):.2f}] {str(h.get('doc_id',''))[:8]} {(h.get('title') or '')[:46]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
