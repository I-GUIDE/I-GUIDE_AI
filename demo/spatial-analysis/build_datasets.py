#!/usr/bin/env python3
"""Rebuild the large demo datasets from public sources.

    python3 build_datasets.py            # everything that can be fetched
    python3 build_datasets.py --illinois # just the Illinois tract set

Only the Illinois data is fetchable: TIGER geometry and TIGERweb population are both public
and need no API key. The Chicago files are not — see README.md for their provenance.

Note the Census *data* API (api.census.gov) now rejects keyless requests with "Missing Key",
so population comes from TIGERweb's REST service instead, which still serves POP100 openly.
The total is asserted against the published state figure: a join key that silently half-matches
is the failure mode this guards against.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

TIGER_TRACTS = "https://www2.census.gov/geo/tiger/TIGER2020/TRACT/tl_2020_17_tract.zip"
TIGERWEB = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
            "tigerWMS_Census2020/MapServer/6/query")
ILLINOIS_2020_POPULATION = 12_812_508      # published 2020 census total, used as a checksum


def _download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest
    print(f"  fetching {dest.name} …")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as r, dest.open("wb") as fh:
        fh.write(r.read())
    print(f"  wrote {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def fetch_tract_population() -> list[dict]:
    """Every Illinois tract's GEOID/NAME/POP100, paging until TIGERweb stops truncating."""
    rows: list[dict] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({
            "where": "STATE='17'", "outFields": "GEOID,NAME,POP100",
            "returnGeometry": "false", "f": "json",
            "resultOffset": offset, "resultRecordCount": 2000,
        })
        with urllib.request.urlopen(f"{TIGERWEB}?{query}", timeout=180) as r:
            payload = json.load(r)
        feats = payload.get("features", [])
        rows += [f["attributes"] for f in feats]
        if not feats or not payload.get("exceededTransferLimit"):
            break
        offset += len(feats)
    total = sum(int(r.get("POP100") or 0) for r in rows)
    print(f"  {len(rows)} tracts, population {total:,}")
    if total != ILLINOIS_2020_POPULATION:
        raise SystemExit(
            f"population total {total:,} != published {ILLINOIS_2020_POPULATION:,} — the "
            "TIGERweb response is incomplete, so the demo set would be wrong")
    return rows


def build_illinois() -> None:
    print("Illinois census tracts:")
    zip_path = _download(TIGER_TRACTS, DATA / "il_tracts.zip")
    rows = fetch_tract_population()

    csv_path = HERE / "illinois_tract_population.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["GEOID", "tract_name", "population"])
        for r in rows:
            writer.writerow([r["GEOID"], r["NAME"], int(r.get("POP100") or 0)])
    print(f"  wrote {csv_path.name}")

    try:
        import geopandas as gpd
        import pandas as pd
    except ImportError:
        print("  (geopandas not installed — skipping the pre-joined GeoJSON)")
        return

    gdf = gpd.read_file(f"zip://{zip_path}")
    pop = pd.DataFrame(rows)
    pop["population"] = pd.to_numeric(pop["POP100"], errors="coerce").fillna(0).astype(int)
    merged = gdf.merge(pop[["GEOID", "population"]], on="GEOID", how="left")
    missing = int(merged["population"].isna().sum())
    if missing:
        raise SystemExit(f"{missing} tracts got no population — check the GEOID join")
    merged["population"] = merged["population"].astype(int)
    merged["area_km2"] = merged.to_crs(merged.estimate_utm_crs()).area / 1e6

    out = merged[["GEOID", "NAMELSAD", "population", "area_km2", "geometry"]].to_crs("EPSG:4326")
    # ~20 m simplification: keeps every boundary recognisable and roughly halves the upload.
    out["geometry"] = out.geometry.simplify(0.0002)
    dest = DATA / "illinois_tracts_population.geojson"
    out.to_file(dest, driver="GeoJSON")
    print(f"  wrote {dest.name} ({dest.stat().st_size / 1e6:.1f} MB), "
          f"{len(out)} tracts, {int(out.population.sum()):,} people")
    print(f"  max regions at a 1,000,000 floor: {int(out.population.sum()) // 1_000_000}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--illinois", action="store_true", help="only the Illinois tract set")
    ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    build_illinois()
    print("\nThe Chicago files cannot be fetched here — see README.md for where they come from.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
