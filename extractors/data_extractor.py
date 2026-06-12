"""Dataset / metadata extractor (#2) — webhook (upload) path.

Dispatches an uploaded data file by family to a handler and emits ONE Dataset
AssetRecord: `crs`, `spatial-bounding-box-geojson`, and an `extracted` block
(format, size, + family fields: resolution/bands/variables for raster;
schema/geometry/feature_count/layers for vector; columns/row_count for tabular).
Index-only; never executable.

Heavy GIS libs (rasterio/xarray/fiona/geopandas) are OPTIONAL: stdlib handlers cover
GeoJSON/CSV/zip; raster/vector handlers try the lib and degrade with a note if it's
absent or the file can't be read. See EXTRACTOR_DESIGN.md §7.
"""

from __future__ import annotations

import csv
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    EMIT_OPENSEARCH,
    KIND_DATASET,
    AssetRecord,
    ExtractContext,
    Extractor,
    ExtractionResult,
)
from .doc_ids import dataset_doc_id, resource_type_for
from .fileclass import CONTAINER_EXT, RASTER_EXT, TABULAR_EXT, VECTOR_EXT

_LATS = {"lat", "latitude", "y"}
_LONS = {"lon", "lng", "long", "longitude", "x"}


def _envelope(minx: float, miny: float, maxx: float, maxy: float) -> Dict[str, Any]:
    return {"type": "envelope", "coordinates": [[minx, maxy], [maxx, miny]]}


def _iter_coords(obj: Any):
    """Yield [x, y] pairs from arbitrary GeoJSON coordinate nesting."""
    if isinstance(obj, (list, tuple)):
        if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
            yield obj[0], obj[1]
        else:
            for item in obj:
                yield from _iter_coords(item)


def _handle_geojson(path: str) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = data.get("features") if isinstance(data, dict) else None
    feats = feats if isinstance(feats, list) else ([data] if isinstance(data, dict) else [])
    xs: List[float] = []
    ys: List[float] = []
    geom_types: set = set()
    prop_keys: set = set()
    for f in feats:
        geom = (f or {}).get("geometry") or {}
        if geom.get("type"):
            geom_types.add(geom["type"])
        for x, y in _iter_coords(geom.get("coordinates")):
            xs.append(x); ys.append(y)
        for k in ((f or {}).get("properties") or {}):
            prop_keys.add(k)
    meta: Dict[str, Any] = {"format": "GeoJSON", "feature_count": len(feats),
                            "geometry_types": sorted(geom_types), "schema": sorted(prop_keys),
                            "crs": "EPSG:4326"}
    if xs and ys:
        meta["bounds"] = [min(xs), min(ys), max(xs), max(ys)]
    return meta


def _handle_tabular(path: str) -> Dict[str, Any]:
    ext = Path(path).suffix.lower()
    delim = "\t" if ext == ".tsv" else ","
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter=delim)
        rows = list(reader)
    if not rows:
        return {"format": "CSV", "row_count": 0, "schema": []}
    header = rows[0]
    body = rows[1:]
    meta: Dict[str, Any] = {"format": "CSV" if ext != ".tsv" else "TSV",
                            "schema": header, "row_count": len(body)}
    lower = {h.lower(): i for i, h in enumerate(header)}
    lat_i = next((lower[h] for h in lower if h in _LATS), None)
    lon_i = next((lower[h] for h in lower if h in _LONS), None)
    if lat_i is not None and lon_i is not None:
        xs: List[float] = []; ys: List[float] = []
        for r in body[:5000]:
            try:
                ys.append(float(r[lat_i])); xs.append(float(r[lon_i]))
            except (ValueError, IndexError):
                continue
        if xs and ys:
            meta["bounds"] = [min(xs), min(ys), max(xs), max(ys)]
            meta["crs"] = "EPSG:4326"
            meta["geometry_from"] = {"lon": header[lon_i], "lat": header[lat_i]}
    return meta


def _handle_raster(path: str) -> Dict[str, Any]:
    ext = Path(path).suffix.lower()
    try:
        if ext in {".nc", ".hdf", ".h5", ".he5", ".grib", ".grb", ".grib2", ".zarr"}:
            import xarray as xr  # type: ignore
            ds = xr.open_dataset(path)
            return {"format": ext.lstrip("."), "variables": list(map(str, ds.data_vars)),
                    "dims": {k: int(v) for k, v in ds.dims.items()}}
        import rasterio  # type: ignore
        with rasterio.open(path) as src:
            b = src.bounds
            return {"format": "GeoTIFF" if ext in {".tif", ".tiff"} else ext.lstrip("."),
                    "crs": str(src.crs), "bounds": [b.left, b.bottom, b.right, b.top],
                    "resolution": list(src.res), "bands": src.count,
                    "dtypes": [str(d) for d in src.dtypes]}
    except Exception as exc:
        return {"format": ext.lstrip("."), "note": f"raster reader unavailable/failed: {type(exc).__name__}: {exc}"}


def _handle_vector(path: str) -> Dict[str, Any]:
    ext = Path(path).suffix.lower()
    if ext in {".geojson", ".json"}:
        return _handle_geojson(path)
    try:
        import fiona  # type: ignore
        layers = fiona.listlayers(path)
        with fiona.open(path) as src:
            return {"format": src.driver, "crs": str(src.crs), "bounds": list(src.bounds),
                    "geometry_type": src.schema.get("geometry"),
                    "schema": list((src.schema.get("properties") or {}).keys()),
                    "feature_count": len(src), "layers": layers}
    except Exception as exc:
        return {"format": ext.lstrip("."), "note": f"vector reader unavailable/failed: {type(exc).__name__}: {exc}"}


def _handle_container(path: str) -> Dict[str, Any]:
    members: List[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            members = [n for n in zf.namelist() if not n.endswith("/")]
    except Exception as exc:
        return {"format": "zip", "note": f"could not read container: {exc}"}
    fams: Dict[str, int] = {}
    for m in members:
        fams[family_for_ext(Path(m).suffix.lower())] = fams.get(family_for_ext(Path(m).suffix.lower()), 0) + 1
    return {"format": "zip", "member_count": len(members), "member_families": fams,
            "members": members[:50]}


def family_for_ext(ext: str) -> str:
    ext = ext.lower()
    if ext in RASTER_EXT:
        return "raster"
    if ext in VECTOR_EXT:
        return "vector"
    if ext in TABULAR_EXT:
        return "tabular"
    if ext in CONTAINER_EXT:
        return "container"
    return "sidecar"


_HANDLERS = {"raster": _handle_raster, "vector": _handle_vector,
             "tabular": _handle_tabular, "container": _handle_container}


def extract_dataset_metadata(path: str) -> Dict[str, Any]:
    ext = Path(path).suffix.lower()
    family = family_for_ext(ext)
    handler = _HANDLERS.get(family)
    meta: Dict[str, Any] = {"family": family, "ext": ext}
    if handler:
        try:
            meta.update(handler(path))
        except Exception as exc:
            meta["note"] = f"{family} handler failed: {type(exc).__name__}: {exc}"
    else:
        meta["format"] = ext.lstrip(".") or "unknown"
    try:
        meta["size_bytes"] = os.path.getsize(path)
    except OSError:
        pass
    return meta


class DataExtractor:
    name = "dataset"

    def extract(self, path: str, *, ctx: ExtractContext) -> ExtractionResult:
        fname = os.path.basename(path)
        meta = extract_dataset_metadata(path)
        anchor = ctx.anchor() or fname
        doc_id = dataset_doc_id(anchor)
        f = ctx.fields or {}
        title = str(f.get("title") or fname)

        spatial: Dict[str, Any] = {}
        if meta.get("crs"):
            spatial["crs"] = meta["crs"]
        if meta.get("bounds"):
            b = meta["bounds"]
            spatial["bounds"] = b
            spatial["spatial-bounding-box-geojson"] = _envelope(b[0], b[1], b[2], b[3])
        for k in ("resolution", "schema", "geometry_type", "feature_count", "variables"):
            if meta.get(k) is not None:
                spatial[k] = meta[k]

        fields_desc = ", ".join(map(str, meta.get("schema") or meta.get("variables") or []))
        contents = f"{title}\nformat={meta.get('format')} family={meta.get('family')}" + \
                   (f"\nfields: {fields_desc}" if fields_desc else "") + \
                   (f"\n{f.get('abstract')}" if f.get("abstract") else "")
        source_fields = {k: f[k] for k in ("authors", "contributor", "abstract", "tags", "license", "doi")
                         if f.get(k)}

        asset = AssetRecord(
            asset_id=doc_id, kind=KIND_DATASET, resource_type=resource_type_for(KIND_DATASET),
            doc_id=doc_id, emit_targets=[EMIT_OPENSEARCH], source_rel_path=fname,
            title=title, contents=contents.strip(),
            spatial=(spatial or None), source_fields=source_fields,
            extracted={"format": meta.get("format"), "family": meta.get("family"),
                       "size_bytes": meta.get("size_bytes"), "note": meta.get("note"),
                       "member_families": meta.get("member_families"),
                       "parent_type": "Dataset", "parent_title": title},
        )
        warnings = [f"dataset: {meta['note']}"] if meta.get("note") else []
        return ExtractionResult(assets=[asset], warnings=warnings)


_: Extractor = DataExtractor()  # type: ignore[assignment]

__all__ = ["DataExtractor", "extract_dataset_metadata", "family_for_ext"]
