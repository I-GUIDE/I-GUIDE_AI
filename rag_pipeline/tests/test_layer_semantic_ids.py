"""A layer's id comes from what it IS, not from what it is called.

Without an explicit id, build_map_layer slugs the LABEL, which ties identity to a display
string. Both directions have bitten this codebase in one day: two labels that slugified alike
collapsed into one layer, and re-wording labels to fit the panel moved the identity of every
layer they named. These pin the separation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CHAMPAIGN = [-88.246, 40.110, -88.234, 40.121]
URBANA = [-88.212, 40.108, -88.200, 40.119]


def _embed_region_layer(monkeypatch, *, bbox, name=None, label_style=None):
    """Drive the real embed_region over a stubbed service and return its map_layer."""
    import agent_runtime.rs_embed_tools as T

    def _svc(path, body=None, method="POST", **kw):
        if path == "/api/models":
            return {"models": [{"id": "gse", "type": "precomputed"}]}
        return {"results": [{"model": "gse", "ok": True, "type": "pooled", "dim": 64,
                             "grid_hw": [101, 101], "norm": 0.8,
                             "image": "data:image/png;base64,AAAA"}]}

    monkeypatch.setattr(T, "_svc", _svc)
    monkeypatch.setattr(T, "_save_png", lambda uri, stem: {
        "file_id": f"id_{stem}", "download_url": f"/files/{stem}.png"})
    if label_style is not None:
        monkeypatch.setattr(T, "_layer_label", label_style)

    tool = {t.name: t for t in T.make_rs_embed_tools()}["embed_region"]
    out = json.loads(tool.func(bbox=bbox, name=name))
    assert out["ok"] is True, out
    return out["map_layer"]


def test_rewording_the_label_does_not_move_the_id(monkeypatch):
    """THE point of stage 1. Today's panel-truncation fix reordered every label; under
    label-derived identity that silently re-identified every layer on the map."""
    a = _embed_region_layer(monkeypatch, bbox=CHAMPAIGN, name="downtown champaign")
    b = _embed_region_layer(monkeypatch, bbox=CHAMPAIGN, name="downtown champaign",
                            label_style=lambda base, tag: f"{base} :: {tag} (v2)")

    assert a["label"] != b["label"], "the fixture must actually change the wording"
    assert a["id"] == b["id"], "identity followed the wording"


def test_two_regions_still_get_two_ids(monkeypatch):
    a = _embed_region_layer(monkeypatch, bbox=CHAMPAIGN, name="downtown champaign")
    b = _embed_region_layer(monkeypatch, bbox=URBANA, name="downtown urbana")
    assert a["id"] != b["id"]


def test_re_running_the_same_region_replaces_rather_than_stacking(monkeypatch):
    a = _embed_region_layer(monkeypatch, bbox=CHAMPAIGN, name="downtown champaign")
    b = _embed_region_layer(monkeypatch, bbox=CHAMPAIGN, name="downtown champaign")
    assert a["id"] == b["id"]


def test_an_unnamed_region_is_identified_by_where_it_is(monkeypatch):
    a = _embed_region_layer(monkeypatch, bbox=CHAMPAIGN)
    b = _embed_region_layer(monkeypatch, bbox=URBANA)
    assert a["id"] != b["id"]
    assert _embed_region_layer(monkeypatch, bbox=CHAMPAIGN)["id"] == a["id"]


def test_the_tools_id_survives_the_descriptor_builder(monkeypatch):
    """build_map_layer only honours a tool-supplied id; if it were dropped we would be back
    on the label slug without noticing."""
    from agent_runtime.map_layers import build_map_layer

    layer = _embed_region_layer(monkeypatch, bbox=CHAMPAIGN, name="downtown champaign")
    built = build_map_layer("embed_region", json.dumps({"map_layer": layer}))
    assert built["id"] == layer["id"]
    assert not built["id"].startswith("agent-"), "an explicit id must not be re-derived"


def test_different_kinds_of_layer_over_one_region_do_not_collide():
    from agent_runtime.rs_embed_tools import _layer_id

    tag = "downtown champaign"
    ids = [_layer_id("pca", "gse", tag),
           _layer_id("sharedpca", "gse", tag),
           _layer_id("segments", "gse", "k5", tag),
           _layer_id("zonepixels", "gse", tag),
           _layer_id("zonegroups", "gse", tag)]
    assert len(set(ids)) == len(ids), ids


def test_two_models_over_one_region_do_not_collide():
    from agent_runtime.rs_embed_tools import _layer_id

    assert _layer_id("pca", "gse", "urbana") != _layer_id("pca", "clay", "urbana")


def test_long_region_names_differing_past_the_cut_stay_distinct():
    """The id inherits map_layers' one truncation policy — bounded prefix plus a digest."""
    from agent_runtime.rs_embed_tools import _layer_id

    stem = "a very long region description that runs well past forty characters"
    assert _layer_id("pca", "gse", f"{stem} north") != _layer_id("pca", "gse", f"{stem} south")


def test_empty_parts_are_dropped_rather_than_leaving_a_dangling_separator():
    from agent_runtime.rs_embed_tools import _layer_id

    assert _layer_id("pca", "gse", "") == _layer_id("pca", "gse")
    assert "--" not in _layer_id("pca", "gse", "", "  ", "urbana")


# --- regressions the adversarial review of this change found ------------------------------

def test_two_cluster_counts_over_one_zone_set_stay_separate():
    """The regression this change introduced: k is in the label, so it must be in the id.
    Asking for 3 groups and then 6 is two analyses, and the second must not replace the
    first. segment_region already keyed on its k; zone groups did not."""
    from agent_runtime.rs_embed_tools import _layer_id

    tag = "champaign tracts"
    assert _layer_id("zonegroups", "gse", tag, "k3") != _layer_id("zonegroups", "gse", tag, "k6")


def test_one_polygon_layers_from_two_places_do_not_share_an_id():
    """Without zone_id_field the zone ids are row indices, so every single-polygon layer is
    zone "0"; the region is what tells them apart."""
    from agent_runtime.rs_embed_tools import _layer_id

    assert _layer_id("zone", "gse", "urbana", "0") != _layer_id("zone", "gse", "champaign", "0")


def test_the_same_column_predicted_over_two_areas_stays_separate():
    """fit_zone_model has no bbox to fall back on, so an unnamed run was identified by its
    label_column alone."""
    from agent_runtime.rs_embed_tools import _layer_id

    a = _layer_id("predicted", "canopy_pct", "file_aaa", "")
    b = _layer_id("predicted", "canopy_pct", "file_bbb", "")
    assert a != b


def test_non_latin_region_names_do_not_all_collapse_together():
    """The slug drops every non-ASCII character; an empty slug then fell back to `kind`, so
    every CJK/Cyrillic/Arabic name shared one id."""
    from agent_runtime.rs_embed_tools import _layer_id

    ids = [_layer_id("pca", "gse", n) for n in ("北京", "上海", "Москва", "القاهرة")]
    assert len(set(ids)) == 4, ids
    assert all("-pca-gse-pca" not in i for i in ids), "the fallback swallowed the name"
    # still stable, so a re-run replaces rather than stacking
    assert _layer_id("pca", "gse", "北京") == _layer_id("pca", "gse", "北京")


def test_mirrored_hemispheres_do_not_share_an_id():
    """The slug flattens the minus sign, so -88.240 read exactly like 88.240."""
    from agent_runtime.rs_embed_tools import _layer_id

    assert _layer_id("pca", "gse", "40.115,-88.240") != _layer_id("pca", "gse", "40.115,88.240")


def test_a_plain_name_keeps_a_readable_id():
    """The digest is for lossy parts only — ordinary names must stay legible in logs."""
    from agent_runtime.rs_embed_tools import _layer_id

    assert _layer_id("pca", "gse", "downtown champaign") == "embed-pca-gse-downtown_champaign"
