"""Extract a plottable map layer (GeoJSON) from a tool result.

Turns a geometry-bearing tool output -- notably ``overpass_search``, but any result
carrying a GeoJSON ``FeatureCollection`` or a list of ``{geometry, ...}`` features --
into a ``LayerArtifact``-shaped dict. The streaming layer emits it as a dedicated,
untruncated ``map_layer`` SSE event so a map client can plot the agent's spatial
findings live (the ``tool_result`` event carries only truncated text).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cap features per emitted layer so a single event stays a sane size.
_MAX_FEATURES = 500

# tool_name -> (layer source category, display-label prefix)
_GEO_TOOLS: Dict[str, tuple] = {
    "overpass_search": ("overpass", "OSM"),
    "spatial_search": ("kb", "Spatial"),
}


def _coerce_obj(output: Any) -> Optional[Any]:
    """Best-effort parse of a tool output into a dict/list."""
    if output is None:
        return None
    if isinstance(output, (dict, list)):
        return output
    content = getattr(output, "content", None)  # LangChain ToolMessage
    text = content if isinstance(content, str) else (output if isinstance(output, str) else str(output))
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


def _is_geometry(g: Any) -> bool:
    return isinstance(g, dict) and isinstance(g.get("type"), str) and g.get("coordinates") is not None


def _features_from(obj: Any) -> List[Dict[str, Any]]:
    """Collect GeoJSON Features from a FeatureCollection, an overpass-style
    ``{features:[{geometry,...}]}``, or a bare list of feature-like dicts."""
    out: List[Dict[str, Any]] = []

    def add(item: Any) -> None:
        if isinstance(item, dict) and _is_geometry(item.get("geometry")):
            props = item.get("properties")
            if not isinstance(props, dict):
                props = {k: v for k, v in item.items() if k != "geometry"}
            out.append({"type": "Feature", "geometry": item["geometry"], "properties": props})

    if isinstance(obj, dict):
        items = obj.get("features")
        if isinstance(items, list):
            for it in items:
                add(it)
    elif isinstance(obj, list):
        for it in obj:
            add(it)
    return out


def build_map_layer(tool_name: str, output: Any) -> Optional[Dict[str, Any]]:
    """Return a ``LayerArtifact``-shaped dict for a geometry-bearing tool result, else None."""
    obj = _coerce_obj(output)
    if obj is None:
        return None

    # A tool that has ALREADY written a styled layer (add_map_layer) describes it explicitly:
    # the client fetches the GeoJSON by url rather than receiving it inline, which is what makes
    # a 50k-point heat map or an 800-polygon choropleth practical to stream.
    if isinstance(obj, dict) and isinstance(obj.get("map_layer"), dict):
        ml = dict(obj["map_layer"])
        url = str(ml.get("url") or "").strip()
        if url:
            render = str(ml.get("render") or "shapes")
            slug = re.sub(r"[^a-z0-9]+", "_", str(ml.get("label") or render).lower()).strip("_")[:40]
            out = {
                "kind": "map_layer",
                "id": ml.get("id") or f"agent-{slug or render}",
                "source": ml.get("source") or "analysis",
                "label": ml.get("label") or render,
                "url": url,
                "render": render,
                "style_by": ml.get("style_by"),
                "count": ml.get("count"),
                # Whether the layer is a SUBSET is part of the layer, not a remark in the
                # answer text: the client shows "shown/total" from these. Dropping them here
                # silently turned every sampled layer into one that looked complete.
                "sampled": bool(ml.get("sampled")),
                "total": ml.get("total"),
            }
            # A raster layer (e.g. an embedding PCA image) is draped over a geographic
            # extent rather than parsed as GeoJSON, so it travels with its bounds.
            if render == "raster":
                bounds = ml.get("bounds")
                if not (isinstance(bounds, (list, tuple)) and len(bounds) == 4):
                    logger.warning("raster map_layer %r has no usable bounds; dropping", out["id"])
                    return None
                out["bounds"] = [float(v) for v in bounds]
                out["opacity"] = float(ml.get("opacity") or 0.85)
            return out
    features = _features_from(obj)
    if not features:
        return None

    capped = features[:_MAX_FEATURES]
    source, prefix = _GEO_TOOLS.get(tool_name, ("analysis", tool_name))

    label = prefix
    suffix = ""
    if isinstance(obj, dict):
        query = obj.get("query") or {}
        feature = query.get("feature") or query.get("osm_filter")
        place = query.get("place")
        if feature:
            label = f"{prefix}: {feature}" + (f" in {place}" if place else "")
            suffix = re.sub(r"[^a-z0-9]+", "_", str(feature).lower()).strip("_")[:40]

    layer_id = f"agent-{tool_name}" + (f"-{suffix}" if suffix else "")
    return {
        "kind": "map_layer",
        "id": layer_id,
        "source": source,
        "label": label,
        "count": len(capped),
        "truncated": len(features) > len(capped),
        "geojson": {"type": "FeatureCollection", "features": capped},
    }
