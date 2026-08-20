"""Tests for the map_layer descriptor boundary (agent_runtime/map_layers.py).

Every tool's layer crosses build_map_layer on its way to the client, so invariants
the client depends on are enforced here rather than trusted to each emitter.
"""
# --- a categorical layer without its palette must not reach the client as 'categories' ---
# Observed twice, from two different tools: render='categories' with no legend made the client
# fall back to the numeric ramp, Number("High-High") is NaN, and all 801 features drew as one
# flat fill while the answer text described five colours.

def _descriptor(**over):
    import json
    ml = {"url": "/agent/files/x/download", "label": "LISA clusters",
          "render": "categories", "style_by": "lisa_class", "count": 801}
    ml.update(over)
    return json.dumps({"map_layer": ml})


def test_categorical_layer_without_a_legend_is_downgraded():
    from agent_runtime.map_layers import build_map_layer

    out = build_map_layer("add_map_layer", _descriptor())
    assert out["render"] == "shapes", "must not claim to encode classes it cannot colour"
    assert out.get("legend_missing") is True


def test_categorical_layer_keeps_its_render_when_the_legend_is_usable():
    from agent_runtime.map_layers import build_map_layer

    out = build_map_layer("add_map_layer", _descriptor(
        legend=[{"label": "High-High", "color": [215, 25, 28, 200]},
                {"label": "Low-Low", "color": [44, 123, 182, 200]}]))
    assert out["render"] == "categories"
    assert [e["label"] for e in out["legend"]] == ["High-High", "Low-Low"]


def test_a_malformed_legend_counts_as_no_legend():
    from agent_runtime.map_layers import build_map_layer

    out = build_map_layer("add_map_layer", _descriptor(
        legend=[{"label": "High-High", "color": "red"}, {"color": [1, 2, 3, 4]}]))
    assert out["render"] == "shapes"
    assert "legend" not in out
