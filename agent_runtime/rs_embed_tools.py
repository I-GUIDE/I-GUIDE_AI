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


def _svc(path: str, payload: Optional[Dict[str, Any]] = None, *, method: str = "POST",
         timeout: Optional[float] = None) -> Dict[str, Any]:
    """Call the rs-embed service, or return an error dict that says what to do."""
    import requests

    url = f"{RS_EMBED_URL}{path}"
    timeout = float(timeout or _TIMEOUT_S)
    try:
        r = (requests.get(url, timeout=timeout) if method == "GET"
             else requests.post(url, json=payload or {}, timeout=timeout))
    except requests.exceptions.ConnectionError:
        return {"error": f"the rs-embed service is not reachable at {RS_EMBED_URL}",
                "hint": "Start it with: python -m uvicorn server:app --app-dir examples/webapp "
                        "--port 8077 (from the rs-embed repo), or set RS_EMBED_URL to where it runs. "
                        "Without it no embedding tool can run — say so rather than inventing values."}
    except requests.exceptions.Timeout:
        return {"error": f"the rs-embed service did not answer within {timeout:.0f}s",
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


def _vector_extent(file_id: str) -> Optional[Dict[str, Any]]:
    """WGS84 bbox of an uploaded vector dataset, plus what its geometry IS.

    The geometry kind decides whether a bounding box is a fair reading of "this area": a
    point cloud has no shape to respect, a polygon layer does.
    """
    try:
        from agent_runtime.langchain_geo_tools import _resolve, read_vector

        path, _rec = _resolve(file_id)
        gdf = read_vector(path)
        if getattr(gdf, "crs", None) is not None:
            gdf = gdf.to_crs("EPSG:4326")
        b = [float(v) for v in gdf.total_bounds]
        if not all(v == v for v in b):
            return None
        kinds = {str(k) for k in gdf.geometry.geom_type.unique()}
        shaped = bool(kinds & {"Polygon", "MultiPolygon", "LineString", "MultiLineString"})
        fill = None
        if shaped:
            try:
                m = gdf.to_crs("EPSG:3857")
                total = float(m.geometry.area.sum())
                bx = m.total_bounds
                env = float(bx[2] - bx[0]) * float(bx[3] - bx[1])
                fill = round(total / env, 4) if env > 0 and total > 0 else None
            except Exception:  # noqa: BLE001
                fill = None
        return {"bounds": [round(v, 6) for v in b], "geom_kinds": sorted(kinds),
                "features": int(len(gdf)), "shaped": shaped, "fill_fraction": fill}
    except Exception:  # noqa: BLE001
        return None


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
                  file_id: Optional[str], buffer_m: float,
                  polygon_extent_ok: bool = True) -> Any:
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
        info = _vector_extent(file_id)
        if info and info["shaped"] and not polygon_extent_ok:
            # Observed: asked for "the embeddings for the area with geoid 17031330100", the
            # agent called BOTH embed_zones and this tool, and reported this one — because a
            # bbox tool that accepts a file_id looks like the direct answer. It embedded the
            # tract's bounding box: the tract fills 69% of it, so 46% of what was embedded was
            # outside the tract, most of it Lake Michigan. Name the tool that keeps the shape.
            pct = f"{info['fill_fraction'] * 100:.0f}%" if info.get("fill_fraction") else "part"
            return {"error": f"{file_id} contains {info['features']} "
                             f"{'/'.join(info['geom_kinds'])} feature(s), and this tool embeds a "
                             f"RECTANGLE — the shapes cover only {pct} of their bounding box, so "
                             f"the rest of the rectangle would be embedded too.",
                    "use_instead": "embed_zones",
                    "hint": "For the VECTOR of each shape use embed_zones(file_id=..., "
                            "zone_id_field=...), which averages only the pixels inside it. For a "
                            "pixel-level PCA PICTURE, call this tool again with an explicit "
                            "bbox=[minlon, minlat, maxlon, maxlat] — the image will then be of "
                            "that rectangle, which is wider than the shape, and the answer "
                            "should say so.",
                    "bounding_box": info["bounds"]}
        if info:
            return info["bounds"]
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
        or `lon`+`lat` for a point (a `buffer_m` square around it). A `file_id` is accepted
        only for POINT layers; for polygons use embed_zones, which keeps their shape.
        `start`/`end` are months, "YYYY-MM".
        """
        box = _resolve_bbox(bbox, lon, lat, file_id, buffer_m, polygon_extent_ok=False)
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




# --- zonal embeddings: pixels inside a polygon, aggregated ----------------------
_ZONAL_TIMEOUT_S = float(os.getenv("RS_EMBED_ZONAL_TIMEOUT_S", "900"))
# Distinct, colour-blind-safe hues for cluster classes (Okabe-Ito, alpha added).
_CLUSTER_COLORS = [
    [0, 114, 178, 190], [230, 159, 0, 190], [0, 158, 115, 190], [204, 121, 167, 190],
    [86, 180, 233, 190], [213, 94, 0, 190], [240, 228, 66, 190], [120, 120, 120, 190],
]


def _dimension_keys(rows: List[Dict[str, Any]]) -> List[str]:
    """The eNNN columns, in dimension order.

    Sorted by the number, not the string: the service zero-pads today, so lexicographic
    order happens to agree, but a rename to unpadded ``e9``/``e10`` would silently transpose
    two dimensions of every vector — the kind of wrong answer nothing downstream can detect.
    Read from the first row that HAS them, because an uncovered zone carries none.
    """
    for row in rows:
        keys = [k for k in row if str(k).startswith("e") and str(k)[1:].isdigit()]
        if keys:
            return sorted(keys, key=lambda k: int(str(k)[1:]))
    return []


def run_zonal_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Per-zone embeddings from the rs-embed SERVICE, in the shape the tools already expect.

    This used to fork a subprocess under a second interpreter that had ``rs_embed``
    installed, because the library had no zones API: the tile sweep, the EPSG:3857 affine,
    the rasterising and the per-zone accumulation were all done here. Two things followed.
    The interpreter was located by a hardcoded path that existed on one laptop and nowhere
    else, so the deployed container answered "the rs_embed runtime is unavailable" for every
    request. And forking a process that already had torch loaded raised an OpenMP mutex
    failure that wedged the turn.

    ``rs_embed.embed_zones`` does the sweep now, so it belongs behind the service — where the
    model runtime, the Earth Engine credentials and the warm weights cache already are — and
    this is one HTTP call. The result keeps the old keys, so ``embed_zones`` and
    ``fit_zone_model`` downstream needed no edits.
    """
    if payload.get("mode") == "fit":
        # Fitting a ridge on vectors that already exist is pandas, numpy and a linear solve
        # over a CSV: no model runtime, no Earth Engine, nothing the service owns. It stays
        # in this process rather than crossing the boundary for nothing — which is why the
        # worker's fit path is scikit-learn-free: its k-means segfaults a process that has
        # torch loaded, and a segfault takes the worker down, not just the turn.
        from agent_runtime.rs_embed_zonal_worker import fit as _fit
        return _fit(payload)

    import geopandas as gpd

    id_field = payload.get("zone_id_field")
    model = str(payload.get("model") or "gse")
    year = int(payload.get("year") or 2022)
    try:
        gdf = gpd.read_file(payload.get("polygons_path"))
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        gdf = gdf.to_crs("EPSG:4326")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not read the polygons: {exc}"}
    if id_field:
        if id_field not in gdf.columns:
            return {"ok": False,
                    "error": f"zone_id_field {id_field!r} is not in the layer",
                    "available_columns": [c for c in gdf.columns if c != "geometry"][:40],
                    "hint": "Name a column that identifies each polygon, or omit zone_id_field "
                            "to key zones by row number — but then use the same setting when "
                            "fitting, or the vectors and the labels will not meet."}
        # The id is the ONLY thing joining a returned vector back to a polygon — on the map,
        # in the CSV, and in fit_zone_model's merge. A column that cannot key uniquely does
        # not fail: it quietly paints one zone's vector onto every polygon that shares its
        # value, and puts the same feature row in both sides of a cross-validation split.
        blank = gdf[id_field].isna()
        if bool(blank.any()):
            return {"ok": False,
                    "error": f"{int(blank.sum())} of {len(gdf)} polygons have no value in "
                             f"{id_field!r}, so their vectors could not be joined back",
                    "hint": "Fill the column, pick another, or omit zone_id_field to key zones "
                            "by row number — but then use the same setting when fitting."}
        dup = gdf[id_field][gdf[id_field].duplicated(keep=False)]
        if len(dup):
            examples = sorted({str(v) for v in dup})[:4]
            return {"ok": False,
                    "error": f"{id_field!r} does not identify a polygon uniquely: {len(dup)} "
                             f"polygons share {dup.nunique()} value(s), e.g. {examples}",
                    "hint": "Pick a unique column, or omit zone_id_field to key zones by row "
                            "number — but then use the same setting when fitting.",
                    "available_columns": [c for c in gdf.columns if c != "geometry"][:40]}

    # Only the identifier travels with the geometry. The service keys zones by row index when
    # no field is named, and dropping columns cannot reorder rows, so the mapping is safe —
    # while a tract layer's forty attribute columns would otherwise be re-encoded into the
    # request body for nothing. The id goes as text: the round trip through JSON turns a
    # numeric id into a number and back, and 17031836500 that returns as "17031836500.0"
    # matches no polygon on the way home.
    keep = [id_field, "geometry"] if id_field else ["geometry"]
    sending = gdf[keep].copy()
    if id_field:
        sending[id_field] = sending[id_field].astype(str)
    body: Dict[str, Any] = {
        "zones_geojson": json.loads(sending.to_json()),
        "model": model, "year": year, "zone_id_field": id_field,
        "tile_px": int(payload.get("tile_px") or 256),
    }
    if payload.get("max_tiles"):
        body["max_tiles"] = int(payload["max_tiles"])
    res = _svc("/api/zones", body, timeout=_ZONAL_TIMEOUT_S)
    if res.get("error") or not res.get("ok"):
        out = {"ok": False,
               "error": str(res.get("error") or "the zones service returned no result")[:400]}
        # _svc's hint names the next action (start the service, point RS_EMBED_URL at it).
        # Dropping it turned a fixable failure into "unavailable" with nowhere to go.
        for key in ("hint", "detail"):
            if res.get(key):
                out[key] = res[key]
        return out

    meta = res.get("meta") or {}
    rows = res.get("rows") or []
    dim_keys = _dimension_keys(rows)
    zones: List[Dict[str, Any]] = []
    for row in rows:
        pixels = int(row.get("pixels") or 0)
        zones.append({
            "zone_id": str(row.get("zone_id")),
            "pixels": pixels,
            "area_km2": float(row.get("area_km2") or 0.0),
            "mean": [float(row.get(k) or 0.0) for k in dim_keys] if pixels else None,
        })
    covered = sum(1 for z in zones if z["pixels"])
    if covered:
        # One clustering implementation, shared with the standalone worker: the map's groups
        # are L2-normalised k-means over the zone vectors, 1-based because the layer treats
        # a missing group as "not embedded".
        from agent_runtime.rs_embed_zonal_worker import assign_look_alike_groups
        assign_look_alike_groups(zones, int(payload.get("clusters") or 0))

    out: Dict[str, Any] = {
        "ok": covered > 0,
        "model": str(meta.get("model") or model),
        "year": year,
        "dims": int(meta.get("dims") or len(dim_keys)),
        "bands": list(meta.get("bands") or []),
        "zone_id_field": meta.get("zone_id_field", id_field),
        # zones_total counts the polygons SENT, not the rows returned: a zone no tile reached
        # comes back with pixels == 0 and must still be accounted for.
        "zones_total": int(meta.get("zones_total") or len(zones)),
        "zones_with_pixels": int(meta.get("zones_with_pixels") or covered),
        # scale_m is EPSG:3857 metres; pixel_ground_m is what a pixel actually covers.
        "scale_m": meta.get("scale_m"),
        "pixel_ground_m": meta.get("pixel_ground_m"),
        "tiles_planned": meta.get("tiles_planned"),
        "tiles_fetched": meta.get("tiles_fetched"),
        "tiles_capped": bool(meta.get("tiles_capped")),
        "tile_errors": list(meta.get("tile_errors") or []),
        "pixel_size_warnings": list(meta.get("pixel_size_warnings") or []),
        "zones": zones,
        # The pixel-level raster was a by-product of holding the whole grid in memory here.
        # The service aggregates and returns vectors, so that view is not produced on this
        # route; embed_region still renders a PCA-RGB image for a bbox.
        "image": {"error": "the zones service returns aggregated vectors, so the pixel-level "
                           "image is not produced on this route — embed_region renders one "
                           "for a bbox if the pixels themselves are the point"},
        "error": None if covered else "no zone received any pixels",
    }
    if not covered:
        out["hint"] = ("Check that the polygons are where you think they are, that the model "
                       "has coverage for this year, and that max_tiles is not cutting the "
                       "sweep short before it reaches them.")
    return out


def make_rs_embed_zonal_tools(default_input_file_ids: Optional[List[str]] = None) -> List[Any]:
    """Tools that aggregate per-pixel embeddings inside polygons."""
    from langchain_core.tools import StructuredTool

    meta = {"category": "geo"}

    def embed_zones(file_id: str, zone_id_field: Optional[str] = None, model: str = "gse",
                    year: int = 2022, clusters: int = 5, tile_px: int = 200,
                    max_tiles: int = 24, name: Optional[str] = None,
                    sibling_file_ids: Optional[List[str]] = None) -> str:
        """Embed one or many POLYGONS — the pixels INSIDE each shape — and map the result.

        This is the tool for "the embedding of this area" whenever the area is a shape rather
        than a rectangle, whether the layer holds one feature or eight hundred. embed_region
        embeds a RECTANGLE, which for a lakefront polygon also takes in open water.

        Divides the polygons' area into pixels, embeds each pixel with a remote-sensing
        foundation model, and averages the pixels that fall INSIDE each polygon — so each
        zone (census tract, county, field, watershed, drawn box) gets one vector describing
        what it looks like from space. Works with any polygon layer: GeoJSON, shapefile,
        GeoPackage.

        Returns a CSV of per-zone vectors ready for machine learning (use fit_zone_model),
        and puts the zones on the map grouped into `clusters` look-alike groups. Each zone
        also carries `pixels` and `area_km2` — its support — and because the pixel count is
        there, the per-zone SUM is recoverable exactly, so zones roll up to a coarser
        partition without error.
        """
        tmp = None
        try:
            import numpy as np

            from agent_runtime.file_store import create_output_file_from_path
            from agent_runtime.langchain_geo_tools import _stage, artifact_name  # type: ignore
        except Exception:  # pragma: no cover - import shape differs in some builds
            from agent_runtime.file_store import create_output_file_from_path
            import numpy as np
            from agent_runtime.langchain_geo_tools import artifact_name
            _stage = None  # type: ignore

        try:
            from agent_runtime.langchain_geo_tools import _stage_vector_source, _index_attached

            attached = _index_attached(default_input_file_ids)
            read_path, tmp = _stage_vector_source(file_id, sibling_file_ids, attached)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": f"could not read {file_id}: {exc}"})

        png_path = Path(tempfile.mkdtemp(prefix="rsembed_zonal_")) / artifact_name(
            name, "png", default=f"{model}_zone_pixels")
        res = run_zonal_worker({"polygons_path": str(read_path), "zone_id_field": zone_id_field,
                                "model": model, "year": int(year), "tile_px": int(tile_px),
                                "max_tiles": int(max_tiles),
                                "clusters": max(2, min(int(clusters), len(_CLUSTER_COLORS))),
                                "image": True, "out_png": str(png_path)})
        if not res.get("ok"):
            return json.dumps({"ok": False, **{k: v for k, v in res.items() if k != "zones"}})

        zones = res["zones"]
        dims = int(res["dims"])
        with_px = [z for z in zones if z["pixels"]]

        # --- CSV of per-zone vectors: the artifact an ML step consumes ---
        stem = artifact_name(name, "csv", default=f"{model}_zone_embeddings")
        out_csv = Path(tempfile.mkdtemp(prefix="rsembed_zonal_")) / stem
        cols = ["zone_id", "pixels", "area_km2"] + [f"e{i:03d}" for i in range(dims)]
        lines = [",".join(cols)]
        for z in with_px:
            lines.append(",".join([str(z["zone_id"]), str(z["pixels"]), f"{z['area_km2']:.6f}"]
                                  + [f"{v:.6f}" for v in z["mean"]]))
        out_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
        csv_rec = create_output_file_from_path(out_csv, filename=out_csv.name)

        # --- the map layer: look-alike groups. Groups are the one view of a 64-dim vector
        # worth looking at — a choropleth of a single dimension is a picture of an arbitrary
        # axis, and it looks like a result.
        layer = None
        cluster_note = None
        try:
            import geopandas as gpd

            gdf = gpd.read_file(read_path)
            if gdf.crs is None:
                gdf = gdf.set_crs("EPSG:4326")
            gdf = gdf.to_crs("EPSG:4326")
            # str() of the same values run_zonal_worker sent, so the ids match on the way back.
            ids = ([str(v) for v in gdf[zone_id_field]]
                   if zone_id_field and zone_id_field in gdf.columns
                   else [str(i) for i in range(len(gdf))])
            grouped = {z["zone_id"]: z for z in with_px if z.get("group")}
            keep = [i for i, zid in enumerate(ids) if zid in grouped]
            if keep:
                sub = gdf.iloc[keep][["geometry"]].copy()
                sub["zone_id"] = [ids[i] for i in keep]
                sub["pixels"] = [grouped[ids[i]]["pixels"] for i in keep]
                sub["area_km2"] = [round(grouped[ids[i]]["area_km2"], 4) for i in keep]
                sub["look_alike_group"] = [f"group {grouped[ids[i]]['group']}" for i in keep]
                gj = Path(tempfile.mkdtemp(prefix="rsembed_zonal_")) / artifact_name(
                    name, "geojson", default=f"{model}_zone_groups")
                sub.to_file(gj, driver="GeoJSON")
                rec = create_output_file_from_path(gj, filename=gj.name)
                present = sorted({str(v) for v in sub["look_alike_group"]})
                layer = {"url": rec.get("download_url"),
                         "label": f"{model} zone groups (k={len(present)})",
                         "render": "categories", "style_by": "look_alike_group",
                         "source": "analysis", "count": len(keep),
                         "legend": [{"label": g,
                                     "color": _CLUSTER_COLORS[i % len(_CLUSTER_COLORS)]}
                                    for i, g in enumerate(present)]}
        except Exception as exc:  # noqa: BLE001
            cluster_note = f"zones computed but not mapped: {type(exc).__name__}: {exc}"[:200]
        if layer is None and cluster_note is None:
            # The group layer is the only thing this tool puts on the map, so "no layer" is a
            # failed delivery, not a detail. Say it in the payload the model reads.
            cluster_note = ("no zone could be placed on the map: none of the embedded zone ids "
                            "matched a polygon in the layer. Say the vectors were computed but "
                            "not mapped — do not describe a map.")

        # One zone (or a few) is the commonest "embed this area" request, and a path to a CSV
        # does not answer it — which is why the model went looking for a tool that returned a
        # vector and found the bounding-box one.
        inline = []
        if len(with_px) <= 5:
            for z in with_px:
                vec = np.asarray(z["mean"], dtype=float)
                inline.append({"zone_id": z["zone_id"], "pixels": z["pixels"],
                               "area_km2": z["area_km2"], "dim": int(vec.size),
                               "vector_norm": round(float(np.linalg.norm(vec)), 4),
                               "vector_first_10": [round(float(v), 6) for v in vec[:10]]})

        out: Dict[str, Any] = {
            "ok": True, "model": model, "year": int(year), "dims": dims,
            "zones_total": res["zones_total"], "zones_with_pixels": res["zones_with_pixels"],
            # scale_m is EPSG:3857 metres; the ground figure is what a pixel really covers.
            "scale_m_mercator": res["scale_m"], "pixel_ground_m": res["pixel_ground_m"],
            "tiles_fetched": res["tiles_fetched"], "tiles_planned": res["tiles_planned"],
            "vectors_csv": {"file_id": csv_rec["file_id"], "filename": csv_rec.get("filename"),
                            "download_url": csv_rec.get("download_url"),
                            "size_bytes": csv_rec.get("size_bytes")},
            "support": {"pixels_min": min((z["pixels"] for z in with_px), default=0),
                        "pixels_median": int(np.median([z["pixels"] for z in with_px])) if with_px else 0,
                        "pixels_max": max((z["pixels"] for z in with_px), default=0)},
            "note": "Each zone's vector is the MEAN of the pixels inside it. `pixels` is its "
                    "support: a model fitted on small zones is extrapolating when applied to "
                    "a much larger one, because pooling averages away variance.",
        }
        if res.get("tiles_capped"):
            out["truncated"] = (f"stopped after max_tiles={max_tiles} of {res['tiles_planned']} "
                                f"tiles — zones outside those tiles have no pixels")
        if res.get("pixel_size_warnings"):
            out["pixel_size_warnings"] = res["pixel_size_warnings"]
        if inline:
            out["zone_vectors"] = inline
        if res.get("tile_errors"):
            out["tile_errors"] = res["tile_errors"]
        if cluster_note:
            out["cluster_note"] = cluster_note
        # Two views, both delivered: the pixel-level embedding masked to the shapes (what the
        # zone vectors are computed FROM), and the zones grouped by those vectors. Asking for
        # "the embedding of these polygons" and getting only a group colour hid the actual data.
        layers = []
        img = res.get("image") or {}
        if img.get("path") and Path(img["path"]).exists() and img.get("bounds"):
            rec_png = create_output_file_from_path(Path(img["path"]),
                                                   filename=Path(img["path"]).name)
            out["pixel_image"] = {"file_id": rec_png["file_id"],
                                  "download_url": rec_png.get("download_url"),
                                  "size_bytes": rec_png.get("size_bytes"),
                                  "size_px": img.get("size_px"),
                                  "pixels_shown": img.get("pixels_shown"),
                                  "colour": img.get("colour")}
            layers.append(_raster_layer(rec_png, [float(v) for v in img["bounds"]],
                                        f"{model} pixel embedding in zones"))
        elif img.get("error"):
            out["image_note"] = img["error"]
        if layer:
            layers.append(layer)
        if layers:
            out["map_layer"] = layers[0]
            if len(layers) > 1:
                out["map_layers"] = layers
            out["on_map"] = True
        return json.dumps(out)


    def fit_zone_model(vectors_csv_file_id: str, polygons_file_id: str, label_column: str,
                       zone_id_field: Optional[str] = None, blocks: int = 5,
                       name: Optional[str] = None,
                       sibling_file_ids: Optional[List[str]] = None) -> str:
        """Predict a per-zone VALUE from zone embeddings, and map the prediction.

        Takes the CSV from embed_zones plus the polygon layer carrying the truth in
        `label_column` (tree canopy, yield, hardship index, ...), fits a ridge model and
        scores it with SPATIAL BLOCK cross-validation: folds are contiguous blocks of space,
        so no zone is scored by a model that trained on its neighbours.

        Reports the blocked score, the score a naive random split WOULD have claimed, and a
        predict-the-mean baseline — the gap between the first two is how much of an apparent
        result was just adjacency. Puts out-of-fold predictions on the map with residuals.
        """
        tmp = None
        try:
            from agent_runtime.file_store import create_output_file_from_path
            from agent_runtime.langchain_geo_tools import (_index_attached, _resolve,
                                                          _stage_vector_source, artifact_name)

            csv_path, _rec = _resolve(vectors_csv_file_id)
            attached = _index_attached(default_input_file_ids)
            poly_path, tmp = _stage_vector_source(polygons_file_id, sibling_file_ids, attached)
            gj = Path(tempfile.mkdtemp(prefix="rsembed_fit_")) / artifact_name(
                name, "geojson", default=f"{label_column}_predicted")

            # Fitting needs no model runtime and no Earth Engine, so it runs here rather
            # than behind the service — see run_zonal_worker's "fit" branch.
            res = run_zonal_worker({"mode": "fit", "vectors_csv": str(csv_path),
                                    "polygons_path": str(poly_path),
                                    "label_column": label_column,
                                    "zone_id_field": zone_id_field,
                                    "blocks": int(blocks), "out_geojson": str(gj)})
            if not res.get("ok"):
                return json.dumps({"ok": False, **res})

            rec = create_output_file_from_path(gj, filename=gj.name)
            blocked = res["spatial_block_cv"]
            naive = res.get("naive_random_split_cv") or {}
            out = {
                "ok": True, **{k: v for k, v in res.items() if k != "ok"},
                "predictions_file_id": rec["file_id"],
                "download_url": rec.get("download_url"),
                "on_map": True,
                "map_layer": {"url": rec.get("download_url"),
                              "label": f"{label_column} predicted from embeddings",
                              "render": "choropleth", "style_by": "predicted",
                              "source": "analysis", "count": res.get("zones_fitted")},
                "note": "r2/rmse are OUT-OF-FOLD under spatial block CV, so they estimate "
                        "performance in an area the model has not seen. Mapped values are "
                        "those out-of-fold predictions, not fitted values. Quote the blocked "
                        "score, not the random-split one.",
            }
            if isinstance(blocked.get("r2"), (int, float)) and blocked["r2"] <= 0:
                out["verdict"] = ("no skill: the model does no better than predicting the mean, "
                                  "so the embeddings do not explain this variable at this "
                                  "sample size. Report that, do not present the map as a result.")
            elif isinstance(naive.get("r2"), (int, float)) and \
                    naive["r2"] - blocked["r2"] > 0.15:
                out["verdict"] = (f"a random split would have claimed r2={naive['r2']}, versus "
                                  f"{blocked['r2']} when whole blocks are held out — most of "
                                  f"that apparent skill was spatial adjacency, not prediction.")
            return json.dumps(out)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            if tmp:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)

    return [StructuredTool.from_function(func=embed_zones, name="embed_zones", metadata=meta),
            StructuredTool.from_function(func=fit_zone_model, name="fit_zone_model", metadata=meta)]


__all__ = ["make_rs_embed_tools", "make_rs_embed_zonal_tools", "run_zonal_worker", "RS_EMBED_URL"]
