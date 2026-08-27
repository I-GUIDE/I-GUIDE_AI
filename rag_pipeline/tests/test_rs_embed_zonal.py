"""Zonal embedding aggregation: the parts that must be right without touching Earth Engine."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_runtime.rs_embed_zonal_worker import _to_lonlat, _to_merc  # noqa: E402


def test_mercator_roundtrip_is_exact_enough_to_place_a_pixel():
    for lon, lat in [(-87.65, 41.93), (121.5, 31.2), (-0.1, 51.5), (0.0, 0.0)]:
        x, y = _to_merc(lon, lat)
        lon2, lat2 = _to_lonlat(x, y)
        # 1e-9 degrees is ~0.1 mm; a 10 m pixel needs nothing like this precision.
        assert abs(lon - lon2) < 1e-9 and abs(lat - lat2) < 1e-9


def test_mercator_metres_are_not_ground_metres():
    """The whole reason the affine is derived in 3857: a 'metre' here is 1/cos(lat) long.
    Reading scale_m as ground metres predicted a 170x111 grid where 224x146 came back."""
    lat = 40.07
    x0, y0 = _to_merc(-88.00, lat)
    x1, _ = _to_merc(-87.98, lat)
    span_3857 = x1 - x0
    span_ground = 0.02 * 111320 * math.cos(math.radians(lat))
    assert span_3857 / span_ground == pytest.approx(1 / math.cos(math.radians(lat)), rel=0.01)


def test_unknown_zone_id_field_lists_the_real_columns(tmp_path):
    """A silent fallback to row numbers made the vectors key on 0..n while the label join
    expected geoids, and the two never met. The caller must be told."""
    import geopandas as gpd
    from shapely.geometry import box

    from agent_runtime.rs_embed_zonal_worker import run

    gdf = gpd.GeoDataFrame({"geoid": ["a", "b"], "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1)]},
                           crs="EPSG:4326")
    p = tmp_path / "z.geojson"
    gdf.to_file(p, driver="GeoJSON")

    out = run({"polygons_path": str(p), "zone_id_field": "GEOID", "model": "gse"})
    assert out["ok"] is False
    assert "GEOID" in out["error"]
    assert "geoid" in out["available_columns"], "must name the column that DOES exist"


def test_sums_and_counts_roll_up_exactly_where_means_would_not():
    """Why the worker stores sum+pixels rather than only a mean: a mean of means is wrong
    across unequal zones, which is the whole reason cross-scale aggregation is possible."""
    zones = [{"sum": [10.0, 20.0], "pixels": 100}, {"sum": [30.0, 30.0], "pixels": 400}]
    total_sum = [sum(z["sum"][i] for z in zones) for i in range(2)]
    total_px = sum(z["pixels"] for z in zones)
    rolled = [v / total_px for v in total_sum]

    mean_of_means = [sum(z["sum"][i] / z["pixels"] for z in zones) / 2 for i in range(2)]
    assert rolled == pytest.approx([0.08, 0.1])
    assert mean_of_means != pytest.approx(rolled), "the naive average is a different number"


def test_zonal_tools_are_registered_with_their_map_layer_contract():
    from agent_runtime.rs_embed_tools import make_rs_embed_zonal_tools
    from agent_runtime.supervisor.graph import _MAP_LAYER_TOOLS

    names = {t.name for t in make_rs_embed_zonal_tools()}
    assert names == {"embed_zones", "fit_zone_model"}
    # Both deliver layers, so a turn that ran them has genuinely put something on the map.
    assert names <= set(_MAP_LAYER_TOOLS)


def _two_squares(tmp_path):
    """A tiny polygon layer on disk — run_zonal_worker reads before it calls the service."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {"geoid": ["a", "b", "c"], "spare": [1, 2, 3],
         "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1), box(2, 0, 3, 1)]},
        crs="EPSG:4326")
    p = tmp_path / "zones.geojson"
    gdf.to_file(p, driver="GeoJSON")
    return p


def _service_reply(covered=("a", "b"), dims=4):
    """What /api/zones sends back: every zone, plus the provenance in meta."""
    rows = []
    for i, zid in enumerate(["a", "b", "c"]):
        row = {"zone_id": zid, "pixels": 100 + i if zid in covered else 0,
               "area_km2": 1.5 + i}
        if zid in covered:
            row.update({f"e{d:03d}": float(i + d) for d in range(dims)})
        rows.append(row)
    return {"ok": True, "model": "gse", "year": 2022, "zones": 3, "dim": dims,
            "rows": rows,
            "meta": {"model": "gse", "dims": dims, "bands": ["A0"], "scale_m": 10.0,
                     "pixel_ground_m": 7.44, "tile_px": 256, "tiles_planned": 4,
                     "tiles_fetched": 2, "tiles_capped": True, "zone_id_field": "geoid",
                     "zones_total": 3, "zones_with_pixels": len(covered),
                     "tile_errors": [], "pixel_size_warnings": []}}


def test_unreachable_zones_service_is_reported_with_the_fix_not_swallowed(tmp_path, monkeypatch):
    """The old failure said "the rs_embed runtime is unavailable" and stopped there, because
    the interpreter was looked up by a path that existed on one laptop. Whatever goes wrong
    now, the caller must be told which service and what to do about it."""
    import agent_runtime.rs_embed_tools as T

    monkeypatch.setattr(T, "_svc", lambda *a, **k: {
        "error": "the rs-embed service is not reachable at http://localhost:8077",
        "hint": "Start it, or set RS_EMBED_URL to where it runs."})
    out = T.run_zonal_worker({"polygons_path": str(_two_squares(tmp_path)),
                              "zone_id_field": "geoid"})
    assert out["ok"] is False
    assert "http://localhost:8077" in out["error"]
    assert "RS_EMBED_URL" in out["hint"], "the next action must survive the hand-off"


def test_service_rows_and_meta_become_the_result_the_tools_read(tmp_path, monkeypatch):
    """embed_zones indexes res["scale_m"], res["tiles_fetched"] and friends directly, so a
    missing meta key is a KeyError in the middle of a delivered turn, not a soft failure."""
    import agent_runtime.rs_embed_tools as T

    monkeypatch.setattr(T, "_svc", lambda *a, **k: _service_reply())
    out = T.run_zonal_worker({"polygons_path": str(_two_squares(tmp_path)),
                              "zone_id_field": "geoid", "clusters": 2})

    assert out["ok"] is True
    for key in ("scale_m", "pixel_ground_m", "tiles_planned", "tiles_fetched", "dims",
                "zones_total", "zones_with_pixels", "bands", "tile_errors",
                "pixel_size_warnings"):
        assert key in out, f"embed_zones reads {key!r} off this result"
    assert out["dims"] == 4 and out["scale_m"] == 10.0

    # An uncovered zone is REPORTED, not dropped: "40 zones, 31 embedded" and "31 zones" are
    # different answers, and only the first says the sweep was short.
    assert out["zones_total"] == 3 and out["zones_with_pixels"] == 2
    assert [z["zone_id"] for z in out["zones"]] == ["a", "b", "c"]
    uncovered = out["zones"][2]
    assert uncovered["pixels"] == 0 and uncovered["mean"] is None and "group" not in uncovered

    covered = [z for z in out["zones"] if z["pixels"]]
    assert all(len(z["mean"]) == 4 for z in covered)
    assert covered[0]["mean"] == [0.0, 1.0, 2.0, 3.0], "values must keep dimension order"
    assert sorted(z["group"] for z in covered) == [1, 2], "groups are 1-based for the map"
    assert out["image"]["error"], "the pixel raster is not produced on this route — say so"


def test_only_the_identifier_travels_with_the_geometry(tmp_path, monkeypatch):
    """A tract layer carries forty attribute columns. Re-encoding them into the request body
    costs bandwidth and ships attributes the service has no use for."""
    import agent_runtime.rs_embed_tools as T

    sent = {}

    def _capture(path, body=None, **kwargs):
        sent["path"], sent["body"] = path, body
        return _service_reply()

    monkeypatch.setattr(T, "_svc", _capture)
    T.run_zonal_worker({"polygons_path": str(_two_squares(tmp_path)),
                        "zone_id_field": "geoid", "clusters": 2})
    assert sent["path"] == "/api/zones"
    props = sent["body"]["zones_geojson"]["features"][0]["properties"]
    assert set(props) == {"geoid"}, f"only the id should travel, got {sorted(props)}"


def test_unknown_zone_field_is_refused_before_the_service_is_called(tmp_path, monkeypatch):
    """Naming a column that does not exist used to reach the service and come back as a
    stack trace. The layer is in hand here, so the real columns can be listed."""
    import agent_runtime.rs_embed_tools as T

    called = []
    monkeypatch.setattr(T, "_svc", lambda *a, **k: called.append(1) or _service_reply())
    out = T.run_zonal_worker({"polygons_path": str(_two_squares(tmp_path)),
                              "zone_id_field": "GEOID"})
    assert out["ok"] is False and "GEOID" in out["error"]
    assert "geoid" in out["available_columns"]
    assert not called, "no point paying for a request that cannot succeed"


def test_dimension_columns_are_ordered_by_number_not_by_name():
    """e000..e063 sorts the same either way, which is exactly why this is worth pinning: a
    rename to unpadded names would transpose dimensions silently, and nothing downstream
    could tell. The first row may also be an uncovered zone, which carries no e-columns."""
    from agent_runtime.rs_embed_tools import _dimension_keys

    rows = [{"zone_id": "a", "pixels": 0},
            {"zone_id": "b", "pixels": 5, "e0": 1, "e9": 2, "e10": 3, "e2": 4}]
    assert _dimension_keys(rows) == ["e0", "e2", "e9", "e10"]
    assert _dimension_keys([{"zone_id": "a", "pixels": 0}]) == []


def test_look_alike_groups_are_shared_with_the_standalone_worker():
    """One clustering implementation, not two: the service path and the local sweep must put
    the same zones in the same group, or a rerun through the other route relabels the map."""
    from agent_runtime.rs_embed_zonal_worker import assign_look_alike_groups

    zones = [{"pixels": 10, "mean": [1.0, 0.0]}, {"pixels": 10, "mean": [0.98, 0.02]},
             {"pixels": 10, "mean": [0.0, 1.0]}, {"pixels": 0, "mean": None}]
    n = assign_look_alike_groups(zones, 2)
    assert n == 2
    assert zones[0]["group"] == zones[1]["group"], "near-identical vectors group together"
    assert zones[2]["group"] != zones[0]["group"]
    assert "group" not in zones[3], "a zone with no pixels has no vector to group by"
    assert min(z["group"] for z in zones[:3]) == 1, "1-based: the map reads 0 as ungrouped"


def test_numpy_stand_ins_reproduce_scikit_learn(tmp_path):
    """scikit-learn cannot be imported where this code runs — its k-means segfaults a process
    that has torch loaded, taking the worker down rather than failing a call — so ridge, the
    two fold splitters and k-means were rewritten in numpy. That is only defensible if the
    numbers are the same, so scikit-learn runs HERE in a fresh subprocess, where torch is
    absent and it is safe, and the two are compared.
    """
    import subprocess

    import numpy as np

    from agent_runtime.rs_embed_zonal_worker import (group_kfold_indices, kfold_indices,
                                                     kmeans_labels, ridge_loo_predict)

    rng = np.random.default_rng(7)
    n, p_dim = 60, 8
    X = rng.normal(size=(n, p_dim))
    y = X @ rng.normal(size=p_dim) + rng.normal(scale=0.3, size=n)
    groups = np.repeat(np.arange(5), 12)
    blobs = np.repeat([[0.0, 0.0], [30.0, 0.0], [0.0, 30.0]], 12, axis=0) \
        + rng.normal(scale=0.4, size=(36, 2))
    alphas = (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)

    script = """
import json, sys
import numpy as np
from sklearn.cluster import KMeans
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import GroupKFold, KFold
d = json.load(open(sys.argv[1]))
X, y = np.array(d["X"]), np.array(d["y"])
groups, blobs = np.array(d["groups"]), np.array(d["blobs"])
alphas = tuple(d["alphas"])
n = len(X)
pred = np.full(n, np.nan)
for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
    pred[te] = RidgeCV(alphas=alphas).fit(X[tr], y[tr]).predict(X[te])
lab = KMeans(n_clusters=3, n_init=10, random_state=0).fit_predict(blobs)
json.dump({
    "ridge_oof": pred.tolist(),
    "kfold": [sorted(int(i) for i in te) for _, te in KFold(5, shuffle=True, random_state=0).split(X)],
    "groupkfold": [sorted(int(i) for i in te) for _, te in GroupKFold(n_splits=5).split(X, y, groups)],
    "kmeans": sorted(sorted(int(i) for i in np.flatnonzero(lab == c)) for c in set(lab)),
}, open(d["out"], "w"))
"""
    payload = tmp_path / "in.json"
    result = tmp_path / "out.json"
    payload.write_text(json.dumps({"X": X.tolist(), "y": y.tolist(),
                                   "groups": groups.tolist(), "blobs": blobs.tolist(),
                                   "alphas": list(alphas), "out": str(result)}))
    proc = subprocess.run([sys.executable, "-c", script, str(payload)],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        pytest.skip(f"scikit-learn unavailable for the comparison: {proc.stderr[-200:]}")
    ref = json.loads(result.read_text())

    ours = np.full(n, np.nan)
    for tr, te in group_kfold_indices(groups, 5):
        ours[te] = ridge_loo_predict(X[tr], y[tr], X[te], alphas)[0]
    assert np.allclose(ours, np.array(ref["ridge_oof"]), atol=1e-8), \
        "out-of-fold ridge predictions must match RidgeCV's, or the reported r2 moves"

    assert [sorted(int(i) for i in te) for _, te in kfold_indices(n, 5, seed=0)] == ref["kfold"]
    assert [sorted(int(i) for i in te)
            for _, te in group_kfold_indices(groups, 5)] == ref["groupkfold"]

    lab = kmeans_labels(blobs, 3)
    ours_partition = sorted(sorted(int(i) for i in np.flatnonzero(lab == c)) for c in set(lab))
    assert ours_partition == ref["kmeans"], "the same zones must land in the same group"
