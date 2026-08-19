"""Remote-sensing foundation-model embeddings as agent tools.

Proxies the rs-embed service (``examples/webapp/server.py`` in the rs-embed repo), which
owns the Earth Engine session, the model zoo and the heavy tensor work. These tools turn
its replies into the things this agent traffics in: file-store artifacts the user can
download, and ``map_layer`` descriptors that land on the interactive map.

Why a service instead of importing ``rs_embed`` here: it keeps torch / earthengine-api /
geemap out of the agent environment, keeps Earth Engine credentials in one place, and
reuses code that is tested in its own repo. Point ``RS_EMBED_URL`` at the service.

Georeferencing note. rs-embed's ``PointBuffer(buffer_m=N)`` footprint is a +/-N metre
square in **EPSG:3857**, not a geodesic square (Web Mercator metres run 1/cos(latitude)
long). Every geometry is therefore converted to an explicit bbox HERE, in 3857, and sent
as a bbox — so the raster we drape is bounded by exactly the region that was embedded
rather than by a reconstruction of it.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RS_EMBED_URL = os.getenv("RS_EMBED_URL", "http://localhost:8077").rstrip("/")
# On-the-fly models download checkpoints on first use and run a transformer; the
# precomputed backends answer in seconds. Long, but bounded — a hung request is worse
# than a slow one because the turn never ends.
_TIMEOUT_S = float(os.getenv("RS_EMBED_TIMEOUT_S", "600"))
_DEFAULT_BUFFER_M = 2048          # matches the service's own point footprint
_MAX_MODELS_PER_CALL = 5


def _svc(path: str, payload: Optional[Dict[str, Any]] = None, *, method: str = "POST") -> Dict[str, Any]:
    """Call the rs-embed service, or return an error dict that says what to do."""
    import requests

    url = f"{RS_EMBED_URL}{path}"
    try:
        r = (requests.get(url, timeout=_TIMEOUT_S) if method == "GET"
             else requests.post(url, json=payload or {}, timeout=_TIMEOUT_S))
    except requests.exceptions.ConnectionError:
        return {"error": f"the rs-embed service is not reachable at {RS_EMBED_URL}",
                "hint": "Start it with: python -m uvicorn server:app --app-dir examples/webapp "
                        "--port 8077 (from the rs-embed repo), or set RS_EMBED_URL to where it runs. "
                        "Without it no embedding tool can run — say so rather than inventing values."}
    except requests.exceptions.Timeout:
        return {"error": f"the rs-embed service did not answer within {_TIMEOUT_S:.0f}s",
                "hint": "On-the-fly models download checkpoints on first use. Retry, use a smaller "
                        "region, or use a precomputed model (gse / tessera / copernicus)."}
    if r.status_code >= 400:
        detail = ""
        try:
            detail = str(r.json().get("error") or "")[:400]
        except Exception:  # noqa: BLE001
            detail = r.text[:400]
        return {"error": f"rs-embed service returned HTTP {r.status_code}", "detail": detail}
    try:
        return r.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"rs-embed service returned unparseable output: {exc}"}


# --- geometry ------------------------------------------------------------------
def _mercator_square(lon: float, lat: float, buffer_m: float) -> List[float]:
    """The +/-buffer_m square around (lon, lat) measured in EPSG:3857, as a lon/lat bbox."""
    from pyproj import Transformer

    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    inv = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    x, y = fwd.transform(lon, lat)
    minlon, minlat = inv.transform(x - buffer_m, y - buffer_m)
    maxlon, maxlat = inv.transform(x + buffer_m, y + buffer_m)
    return [round(minlon, 6), round(minlat, 6), round(maxlon, 6), round(maxlat, 6)]


def _bounds_of_file(file_id: str) -> Optional[List[float]]:
    """WGS84 bbox of an uploaded vector dataset, or None."""
    try:
        from agent_runtime.langchain_geo_tools import _resolve, read_vector

        path, _rec = _resolve(file_id)
        gdf = read_vector(path)
        if getattr(gdf, "crs", None) is not None:
            gdf = gdf.to_crs("EPSG:4326")
        b = [float(v) for v in gdf.total_bounds]
        return [round(v, 6) for v in b] if all(v == v for v in b) else None
    except Exception:  # noqa: BLE001
        return None


def _resolve_bbox(bbox: Optional[List[float]], lon: Optional[float], lat: Optional[float],
                  file_id: Optional[str], buffer_m: float) -> Any:
    """Return ``[minlon, minlat, maxlon, maxlat]`` or an error dict naming the options."""
    if bbox:
        vals = [float(v) for v in bbox]
        if len(vals) != 4:
            return {"error": f"bbox needs 4 numbers [minlon, minlat, maxlon, maxlat]; got {len(vals)}"}
        minlon, minlat, maxlon, maxlat = vals
        if minlon >= maxlon or minlat >= maxlat:
            return {"error": "bbox is empty or inverted",
                    "detail": f"got [{minlon}, {minlat}, {maxlon}, {maxlat}]; "
                              f"expected minlon < maxlon and minlat < maxlat"}
        return [round(v, 6) for v in vals]
    if lon is not None and lat is not None:
        return _mercator_square(float(lon), float(lat), buffer_m)
    if file_id:
        b = _bounds_of_file(file_id)
        if b:
            return b
        return {"error": f"could not read a geographic extent from {file_id}",
                "hint": "Pass bbox=[minlon, minlat, maxlon, maxlat], or lon/lat for a point."}
    return {"error": "no region given",
            "hint": "Pass ONE of: bbox=[minlon, minlat, maxlon, maxlat] (what the map's Region "
                    "tool produces), lon+lat for a point, or file_id of an uploaded layer."}


def _geometry(bbox: List[float]) -> Dict[str, Any]:
    return {"type": "bbox", "minlon": bbox[0], "minlat": bbox[1],
            "maxlon": bbox[2], "maxlat": bbox[3]}


# --- artifacts -----------------------------------------------------------------
def _save_png(data_uri: str, stem: str) -> Optional[Dict[str, Any]]:
    """Persist a ``data:image/png;base64,...`` payload into the file store."""
    from agent_runtime.file_store import create_output_file_from_path

    if not isinstance(data_uri, str) or "base64," not in data_uri:
        return None
    raw = base64.b64decode(data_uri.split("base64,", 1)[1])
    out = Path(tempfile.mkdtemp(prefix="rsembed_")) / f"{stem}.png"
    out.write_bytes(raw)
    return create_output_file_from_path(out, filename=out.name)


def _raster_layer(rec: Dict[str, Any], bbox: List[float], label: str,
                  opacity: float = 0.85) -> Dict[str, Any]:
    """A ``map_layer`` descriptor the client drapes as a georeferenced image."""
    return {"url": rec.get("download_url"), "label": label, "render": "raster",
            "bounds": bbox, "opacity": opacity, "source": "analysis"}


def _fetch_package(service_path: str, stem: str) -> Optional[Dict[str, Any]]:
    """Copy the service's .npz export into the agent file store so the user can actually get it."""
    import requests

    from agent_runtime.file_store import create_output_file_from_path

    try:
        r = requests.get(f"{RS_EMBED_URL}{service_path}", timeout=_TIMEOUT_S)
        if r.status_code >= 400 or not r.content:
            return None
        out = Path(tempfile.mkdtemp(prefix="rsembed_")) / f"{stem}.npz"
        out.write_bytes(r.content)
        return create_output_file_from_path(out, filename=out.name)
    except Exception:  # noqa: BLE001
        return None


def _slug(text: str) -> str:
    keep = [c if c.isalnum() else "_" for c in str(text).lower()]
    return "".join(keep).strip("_")[:40] or "region"


def _model_error(models: List[str], available: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Reject unknown model names by NAMING the real ones (a bare rejection dead-ends)."""
    ids = {str(m.get("id")) for m in available}
    bad = [m for m in models if m not in ids]
    if not bad:
        return None
    pre = sorted(str(m.get("id")) for m in available if m.get("type") == "precomputed")
    otf = sorted(str(m.get("id")) for m in available if m.get("type") != "precomputed")
    return {"ok": False, "error": f"unknown model(s): {', '.join(bad)}",
            "precomputed_models": pre, "onthefly_models": otf,
            "hint": "Precomputed models answer in seconds; on-the-fly models run a "
                    "foundation model and are much slower on first use."}


# --- tools ---------------------------------------------------------------------
def make_rs_embed_tools(default_input_file_ids: Optional[List[str]] = None) -> List[Any]:
    """Build the remote-sensing embedding StructuredTools."""
    from langchain_core.tools import StructuredTool

    meta = {"category": "geo"}

    def list_embedding_models() -> str:
        """List the remote-sensing foundation models available for embedding a region.

        Returns each model's id and whether it is `precomputed` (an existing global product,
        answers in seconds) or `onthefly` (runs the model on imagery now — minutes, and it
        downloads checkpoints the first time). Call this when unsure which model to name.
        """
        res = _svc("/api/models", method="GET")
        if res.get("error"):
            return json.dumps(res)
        models = res.get("models") or []
        return json.dumps({
            "ok": True,
            "precomputed": [m["id"] for m in models if m.get("type") == "precomputed"],
            "onthefly": [m["id"] for m in models if m.get("type") != "precomputed"],
            "detail": models,
            "note": "Precomputed models are the interactive choice; on-the-fly models are "
                    "worth the wait when you need a specific sensor or architecture.",
        })

    def embed_region(bbox: Optional[List[float]] = None, lon: Optional[float] = None,
                     lat: Optional[float] = None, file_id: Optional[str] = None,
                     models: Optional[List[str]] = None, start: str = "2022-06",
                     end: str = "2022-09", buffer_m: float = _DEFAULT_BUFFER_M,
                     name: Optional[str] = None) -> str:
        """Embed a region with remote-sensing foundation models and PUT THE RESULT ON THE MAP.

        Each model returns a learned description of what the place looks like from space over
        the given months. The embedding grid is projected to 3 colours (PCA) and draped over
        the region as an interactive raster layer, so similar-looking ground reads as similar
        colour. Also saves a downloadable .npz holding the real vectors for reuse.

        Region: pass `bbox` [minlon, minlat, maxlon, maxlat] (what the map's Region tool gives),
        or `lon`+`lat` for a point (a `buffer_m` square around it), or `file_id` of an uploaded
        layer to use its extent. `start`/`end` are months, "YYYY-MM".
        """
        box = _resolve_bbox(bbox, lon, lat, file_id, buffer_m)
        if isinstance(box, dict):
            return json.dumps({"ok": False, **box})

        avail = _svc("/api/models", method="GET")
        if avail.get("error"):
            return json.dumps({"ok": False, **avail})
        chosen = [str(m) for m in (models or ["gse"])][:_MAX_MODELS_PER_CALL]
        bad = _model_error(chosen, avail.get("models") or [])
        if bad:
            return json.dumps(bad)

        res = _svc("/api/embed", {"geometry": _geometry(box), "start": start, "end": end,
                                  "models": chosen, "buffer_m": int(buffer_m)})
        if res.get("error"):
            return json.dumps({"ok": False, **res})

        layers, summaries, failed = [], [], []
        for r in res.get("results") or []:
            model = str(r.get("model"))
            if not r.get("ok"):
                failed.append({"model": model, "error": str(r.get("error"))[:300]})
                continue
            stem = f"{_slug(name or 'embedding')}_{model}_pca"
            rec = _save_png(str(r.get("image") or ""), stem)
            entry = {"model": model, "type": r.get("type"), "dim": r.get("dim"),
                     "grid": r.get("grid_hw"), "vector_norm": round(float(r.get("norm") or 0), 3)}
            if rec:
                entry.update({"image_file_id": rec["file_id"], "download_url": rec.get("download_url")})
                layers.append(_raster_layer(rec, box, f"{model} embedding (PCA-RGB)"))
            summaries.append(entry)

        pkg = res.get("package") or {}
        out: Dict[str, Any] = {
            "ok": bool(summaries), "region_bbox": box, "months": f"{start}..{end}",
            "models": summaries, "on_map": bool(layers),
            "compute": res.get("compute"),
            "note": "The colours are a 3-component PCA of the embedding, so they show which "
                    "areas resemble each other — they are NOT land-cover classes and the "
                    "colours are not comparable across separate runs.",
        }
        if failed:
            out["failed"] = failed
        if pkg:
            rec = _fetch_package(str(res.get("download_url") or ""),
                                 f"{_slug(name or 'embedding')}_vectors")
            info: Dict[str, Any] = {"models_saved": pkg.get("models"),
                                    "pooled_vectors": True,
                                    "grids_saved": pkg.get("grids_saved") or []}
            if rec:
                info.update({"file_id": rec["file_id"], "filename": rec.get("filename"),
                             "download_url": rec.get("download_url"),
                             "size_bytes": rec.get("size_bytes")})
            # The service caps which grids go into the export (300x300 cells). Say so: a
            # missing full-resolution grid is otherwise invisible until someone loads the file.
            dropped = [m["model"] for m in summaries
                       if m["model"] not in (pkg.get("grids_saved") or [])]
            if dropped:
                info["full_grid_omitted_for"] = dropped
                info["why"] = ("the per-model grid exceeded the export cap (300x300 cells), so the "
                               "file holds the pooled vector only — enough for similarity, "
                               "prediction and comparison, not for per-pixel work")
            out["embedding_package"] = info
        # One descriptor per model; the client stacks them and the layer list toggles between.
        if layers:
            out["map_layer"] = layers[0]
            if len(layers) > 1:
                out["map_layers"] = layers
        return json.dumps(out)

    def segment_region(bbox: Optional[List[float]] = None, lon: Optional[float] = None,
                       lat: Optional[float] = None, file_id: Optional[str] = None,
                       k: int = 6, model: str = "gse", start: str = "2022-06",
                       end: str = "2022-09", buffer_m: float = _DEFAULT_BUFFER_M,
                       name: Optional[str] = None) -> str:
        """Segment a region into `k` look-alike zones from its embedding, ON THE MAP.

        Unsupervised land-cover-style segmentation: the embedding grid is clustered, so
        ground that looks alike from space gets the same colour. Returns the map layer plus
        a legend giving each cluster's share of the area. The clusters are discovered, not
        named — cluster 3 is not "forest" until someone looks.
        """
        box = _resolve_bbox(bbox, lon, lat, file_id, buffer_m)
        if isinstance(box, dict):
            return json.dumps({"ok": False, **box})
        if not 2 <= int(k) <= 10:
            return json.dumps({"ok": False, "error": f"k must be between 2 and 10; got {k}"})

        res = _svc("/api/segment", {"geometry": _geometry(box), "start": start, "end": end,
                                    "model": model, "k": int(k), "buffer_m": int(buffer_m)})
        if res.get("error"):
            return json.dumps({"ok": False, **res})
        rec = _save_png(str(res.get("image") or ""), f"{_slug(name or 'segments')}_{model}_k{k}")
        if not rec:
            return json.dumps({"ok": False, "error": "the service returned no segmentation image"})
        legend = [{"cluster": e.get("cluster"), "rgb": e.get("rgb"),
                   "share_pct": round(float(e.get("frac") or 0) * 100, 1)}
                  for e in (res.get("legend") or [])]
        return json.dumps({
            "ok": True, "region_bbox": box, "model": model, "k": int(k),
            "grid": res.get("grid_hw"), "legend": legend, "on_map": True,
            "image_file_id": rec["file_id"], "download_url": rec.get("download_url"),
            "map_layer": _raster_layer(rec, box, f"{model} segments (k={k})"),
            "note": "Clusters are unlabelled: they group similar-looking ground, and the "
                    "same number means nothing across separate runs.",
        })

    def embedding_change(bbox: Optional[List[float]] = None, lon: Optional[float] = None,
                         lat: Optional[float] = None, file_id: Optional[str] = None,
                         years: Optional[List[int]] = None, model: str = "gse",
                         buffer_m: float = _DEFAULT_BUFFER_M, name: Optional[str] = None) -> str:
        """Track how much a region CHANGED across years, from its embeddings.

        Embeds the region once per year and reports each year's distance from the baseline
        (the earliest year). A spike marks the year the place changed — new construction,
        clearing, flooding. Returns the per-year table as a CSV file plus the numbers.
        """
        box = _resolve_bbox(bbox, lon, lat, file_id, buffer_m)
        if isinstance(box, dict):
            return json.dumps({"ok": False, **box})
        yrs = sorted({int(y) for y in (years or [])})
        if len(yrs) < 2:
            return json.dumps({"ok": False, "error": "give at least two years",
                               "hint": "e.g. years=[2018, 2020, 2022, 2024]"})

        res = _svc("/api/change", {"geometry": _geometry(box), "years": yrs, "model": model,
                                   "buffer_m": int(buffer_m), "start": "2022-06", "end": "2022-09"})
        if res.get("error"):
            return json.dumps({"ok": False, **res})
        used = [int(y) for y in (res.get("years") or [])]
        dist = [float(d) for d in (res.get("distances") or [])]
        rows = list(zip(used, dist, strict=False))

        from agent_runtime.file_store import create_output_file_from_path

        out = Path(tempfile.mkdtemp(prefix="rsembed_")) / f"{_slug(name or 'change')}_{model}.csv"
        out.write_text("year,distance_from_baseline\n"
                       + "".join(f"{y},{d:.6f}\n" for y, d in rows), encoding="utf-8")
        rec = create_output_file_from_path(out, filename=out.name)
        peak = max(rows, key=lambda t: t[1]) if rows else None
        return json.dumps({
            "ok": True, "region_bbox": box, "model": model,
            "baseline_year": res.get("baseline"), "years": used,
            "distances": [round(d, 4) for d in dist],
            "largest_change_year": peak[0] if peak else None,
            "largest_change_distance": round(peak[1], 4) if peak else None,
            "csv_file_id": rec["file_id"], "download_url": rec.get("download_url"),
            "errors": res.get("errors") or [],
            "note": "Distance is 1 - cosine against the baseline year: 0 means indistinguishable. "
                    "It says THAT the place changed, not what changed.",
        })

    def compare_regions(bbox_a: List[float], bbox_b: List[float], model: str = "gse",
                        start: str = "2022-06", end: str = "2022-09") -> str:
        """Score how alike TWO regions look from space, using their embeddings.

        Returns cosine similarity (1.0 = indistinguishable) between the two regions'
        pooled embeddings. This is the retrieval primitive behind "find me somewhere
        that looks like this". Each bbox is [minlon, minlat, maxlon, maxlat].
        """
        boxes = []
        for label, raw in (("A", bbox_a), ("B", bbox_b)):
            box = _resolve_bbox(raw, None, None, None, _DEFAULT_BUFFER_M)
            if isinstance(box, dict):
                return json.dumps({"ok": False, "region": label, **box})
            boxes.append(box)
        res = _svc("/api/similarity", {"geometries": [_geometry(b) for b in boxes],
                                       "model": model, "start": start, "end": end,
                                       "buffer_m": int(_DEFAULT_BUFFER_M)})
        if res.get("error"):
            return json.dumps({"ok": False, **res})
        cos = res.get("cosine")
        return json.dumps({"ok": True, "model": model, "region_a_bbox": boxes[0],
                           "region_b_bbox": boxes[1], "cosine_similarity": cos,
                           "distance": res.get("distance"), "dim": res.get("dim"),
                           "reading": ("nearly identical" if isinstance(cos, (int, float)) and cos >= 0.95
                                       else "similar" if isinstance(cos, (int, float)) and cos >= 0.8
                                       else "different")})

    def list_prediction_heads() -> str:
        """List the pretrained downstream models that turn an embedding into a prediction."""
        res = _svc("/api/heads", method="GET")
        return json.dumps(res if res.get("error") else {"ok": True, **res})

    def predict_for_region(bbox: Optional[List[float]] = None, lon: Optional[float] = None,
                           lat: Optional[float] = None, file_id: Optional[str] = None,
                           models: Optional[List[str]] = None, start: str = "2022-06",
                           end: str = "2022-09", buffer_m: float = _DEFAULT_BUFFER_M) -> str:
        """Run a pretrained downstream head on a region's embedding to PREDICT a value.

        This is the "use the embedding" step: the region is embedded, then an already-trained
        head turns that vector into an estimate (e.g. crop presence). Call
        list_prediction_heads first to see what has been trained and how well it scored.
        """
        box = _resolve_bbox(bbox, lon, lat, file_id, buffer_m)
        if isinstance(box, dict):
            return json.dumps({"ok": False, **box})
        res = _svc("/api/predict", {"geometry": _geometry(box), "start": start, "end": end,
                                    "models": [str(m) for m in (models or [])],
                                    "buffer_m": int(buffer_m)})
        if res.get("error"):
            return json.dumps({"ok": False, **res})
        return json.dumps({"ok": True, "region_bbox": box, **res,
                           "note": "Each prediction carries the head's own validation score — "
                                   "quote it, because a confident number from a weak head is "
                                   "still a weak number."})

    return [
        StructuredTool.from_function(func=list_embedding_models, name="list_embedding_models", metadata=meta),
        StructuredTool.from_function(func=embed_region, name="embed_region", metadata=meta),
        StructuredTool.from_function(func=segment_region, name="segment_region", metadata=meta),
        StructuredTool.from_function(func=embedding_change, name="embedding_change", metadata=meta),
        StructuredTool.from_function(func=compare_regions, name="compare_regions", metadata=meta),
        StructuredTool.from_function(func=list_prediction_heads, name="list_prediction_heads", metadata=meta),
        StructuredTool.from_function(func=predict_for_region, name="predict_for_region", metadata=meta),
    ]


__all__ = ["make_rs_embed_tools", "RS_EMBED_URL"]
