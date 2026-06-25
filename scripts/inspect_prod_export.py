"""Characterize a prod index JSONL export (tolerant to record shape) and
compare against the dev index manifest already exported.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

PROD = Path("/Users/yfkang/Downloads/export.jsonl")
DEV_MANIFEST = Path("/Users/yfkang/i-guide-platform-flask-servers/outputs/iguide-platform-embeddings-dev.manifest.csv")
BULK_ACTIONS = {"index", "create", "update", "delete"}


def iter_docs(path: Path):
    """Yield (id, source) tolerant to {_id,_source} / flat / ES-bulk formats."""
    pending_action = False
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and len(obj) == 1 and set(obj) <= BULK_ACTIONS:
                pending_action = True
                continue
            if "_source" in obj and isinstance(obj["_source"], dict):
                yield obj.get("_id"), obj["_source"]
            else:
                yield obj.get("id") or obj.get("_id"), obj
            pending_action = False


def rtype(s: dict) -> str:
    return str(s.get("resource-type") or s.get("element_type") or s.get("resourceType") or "?")


def text(s: dict) -> str:
    parts = [str(s.get("title") or ""), str(s.get("contents") or s.get("abstract") or "")]
    tg = s.get("tags")
    parts.append(" ".join(tg) if isinstance(tg, list) else str(tg or ""))
    return " ".join(parts).lower()


def main() -> None:
    docs = list(iter_docs(PROD))
    ids = [d[0] for d in docs]
    none_ids = sum(1 for i in ids if not i)
    dup = {k: v for k, v in Counter(i for i in ids if i).items() if v > 1}
    print(f"records parsed: {len(docs)}   unique ids: {len(set(i for i in ids if i))}")
    print(f"  null ids: {none_ids}   ids appearing >1x: {len(dup)} (e.g. {list(dup.items())[:3]})")

    keyunion = Counter()
    for _, s in docs:
        keyunion.update(s.keys())
    print(f"\nsample _source keys (top 25): {[k for k,_ in keyunion.most_common(25)]}")

    print("\n== resource-type distribution (PROD) ==")
    rt = Counter(rtype(s) for _, s in docs)
    for k, v in rt.most_common():
        print(f"  {k:<28} {v}")

    # dev comparison
    if DEV_MANIFEST.exists():
        dev_ids, dev_rt = set(), Counter()
        with DEV_MANIFEST.open() as f:
            for row in csv.DictReader(f):
                dev_ids.add(row["id"]); dev_rt[row["resource-type"]] += 1
        prod_ids = set(i for i in ids if i)
        print(f"\n== PROD vs DEV ==\n  dev={len(dev_ids)} prod={len(prod_ids)} "
              f"overlap={len(prod_ids & dev_ids)} prod_only={len(prod_ids - dev_ids)} dev_only={len(dev_ids - prod_ids)}")

    # notebooks inventory
    nbs = [(i, s.get("title") or "") for i, s in docs if rtype(s) == "notebook"]
    print(f"\n== notebooks in PROD: {len(nbs)} ==")
    for i, t in sorted(nbs, key=lambda x: (x[1] or "").lower()):
        print(f"  {str(i)[:8]}  {t[:80]}")

    # targeted keyword scans
    queries = {
        "CRIME (paper anchor?)": ["crime"],
        "chicago": ["chicago"],
        "heat exposure / sentiment": ["heat", "exposure", "sentiment"],
        "flood": ["flood"],
        "census / acs": ["census", "acs", "tract"],
        "social vulnerability": ["social", "vulnerability"],
        "geopandas / notebook": ["geopandas", "shapely"],
    }
    print("\n== keyword scans (title+abstract+tags) ==")
    for label, terms in queries.items():
        scored = []
        for i, s in docs:
            t = text(s)
            sc = sum(t.count(term) for term in terms)
            if sc:
                scored.append((sc, i, rtype(s), s.get("title") or ""))
        scored.sort(key=lambda x: x[0], reverse=True)
        print(f"\n[{label}]  matches={len(scored)}")
        for sc, i, rt_, title in scored[:6]:
            print(f"   ({sc:>3}) {rt_:<11} {str(i)[:8]}  {title[:70]}")

    # detail on the likely paper anchor(s)
    print("\n== likely paper anchor(s): 'AI Agent' / crime notebooks ==")
    for i, s in docs:
        title = s.get("title") or ""
        if "ai agent" in title.lower() or "crime" in title.lower():
            ab = " ".join(str(s.get("contents") or "").split())[:280]
            print(f"  [{rtype(s)}] {str(i)[:8]} {title[:72]}")
            print(f"      tags={s.get('tags')}")
            print(f"      {ab}")


if __name__ == "__main__":
    main()
