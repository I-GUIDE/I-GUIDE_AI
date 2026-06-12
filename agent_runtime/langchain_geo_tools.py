"""Vector / shapefile tools for the analysis and code agents.

Lets the agents READ, VISUALIZE, and ANALYZE uploaded vector data — in particular
Census TIGER/Line shapefiles, which arrive either as a ``.zip`` or as an extracted
set of separate files (``.shp`` + ``.shx`` + ``.dbf`` + ``.prj`` …).

The hard part is the extracted case: the file store saves each upload as
``uploads/<file_id>__<filename>``, so the sidecars are NOT co-located with the
``.shp`` under a shared basename — GDAL/pyogrio can't find them. ``_stage_vector_source``
reconstructs a readable shapefile by copying the ``.shp`` and its sibling uploads into
one temp dir under a common basename. The siblings are auto-discovered among the
conversation's attached files by matching the basename stem, so the model only has to
reference any ONE component (the ``.shp`` or a sidecar). A ``.zip`` is read directly via
GDAL's ``/vsizip/`` virtual filesystem (no extraction needed).

Backend: geopandas + pyogrio (already in the agent runtime image alongside system GDAL).
Heavy imports are deferred into the tool bodies so importing this module never fails the
agent boot. Every tool returns a JSON string and NEVER raises — on error it returns
``{"ok": false, "error": "..."}``.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SELF_CONTAINED = {".geojson", ".json", ".gpkg", ".parquet", ".geoparquet", ".fgb", ".kml"}
# Components of an (extracted) ESRI shapefile set — any one of these is enough to reference it.
_SHAPE_PARTS = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".qpj", ".aih", ".ain"}


def _resolve(ref: str) -> Tuple[Path, Optional[Dict[str, Any]]]:
    """Resolve an uploaded file_id (preferred) or an on-disk path to (Path, record)."""
    from agent_runtime.file_store import get_file_record, resolve_file_id

    ref = str(ref or "").strip()
    if not ref:
        raise ValueError("empty file reference")
    record = get_file_record(ref)
    if record:
        return resolve_file_id(ref), record
    p = Path(ref).expanduser()
    if p.is_file():
        return p.resolve(), None
    raise ValueError(f"unknown file_id or path: {ref}")


def _true_name(path: Path, record: Optional[Dict[str, Any]]) -> str:
    """The original filename (on-disk name is mangled as <file_id>__<name>)."""
    if record and record.get("filename"):
        return str(record["filename"])
    name = path.name
    return name.split("__", 1)[1] if "__" in name else name


def _index_attached(file_ids: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Resolve every conversation-attached file_id to ``{id, name, path}``.

    Unresolvable ids are skipped. Used to auto-discover shapefile sidecars by basename.
    """
    idx: List[Dict[str, Any]] = []
    for fid in file_ids or []:
        try:
            p, rec = _resolve(fid)
        except Exception:
            continue
        idx.append({"id": str(fid), "name": _true_name(p, rec), "path": p})
    return idx


def _stage_vector_source(ref: str, sibling_file_ids: Optional[List[str]],
                         attached: Optional[List[Dict[str, Any]]] = None) -> Tuple[str, Optional[str]]:
    """Return ``(gdal_readable_path, tempdir_to_cleanup_or_None)``.

    - ``.zip``  -> ``/vsizip/<abs>`` (GDAL reads the zipped shapefile in place).
    - a shapefile component (``.shp`` OR a sidecar like ``.dbf``) -> reconstruct a readable
      shapefile by copying the ``.shp`` and every sibling into one temp dir under a shared
      basename. Siblings come from (a) explicit ``sibling_file_ids`` and (b) auto-discovery
      among the conversation's ``attached`` files sharing the same basename stem — so the
      model only has to reference any ONE component.
    - self-contained (.geojson/.gpkg/.parquet/…) -> the path as-is.
    """
    path, record = _resolve(ref)
    name = _true_name(path, record)
    ext = Path(name).suffix.lower()

    if ext == ".zip":
        return f"/vsizip/{path.resolve()}", None
    if ext in _SELF_CONTAINED:
        return str(path), None
    if ext in _SHAPE_PARTS:
        stem = Path(name).stem
        # dest_filename -> source Path, starting with the referenced component itself.
        members: Dict[str, Path] = {name: path}
        for sib in sibling_file_ids or []:  # explicit siblings the caller passed
            try:
                sp, srec = _resolve(sib)
            except Exception:
                continue
            members.setdefault(_true_name(sp, srec), sp)
        for entry in attached or []:  # auto-discover the rest of the set by basename
            if Path(entry["name"]).stem == stem:
                members.setdefault(entry["name"], entry["path"])
        shp_name = next((n for n in members if Path(n).suffix.lower() == ".shp"), None)
        if not shp_name:
            raise ValueError(
                f"no .shp found for '{name}'; attach the .shp together with its .shx/.dbf/.prj, "
                "or upload the shapefile as a single .zip")
        tmp = tempfile.mkdtemp(prefix="vec_")
        shp_stem = Path(shp_name).stem
        for n, sp in members.items():
            sext = Path(n).suffix.lower()
            if sext:
                shutil.copyfile(sp, Path(tmp) / f"{shp_stem}{sext}")
        return str(Path(tmp) / f"{shp_stem}.shp"), tmp
    # Unknown extension: let GDAL attempt to read it directly.
    return str(path), None


def _epsg(crs: Any) -> Optional[str]:
    if crs is None:
        return None
    try:
        code = crs.to_epsg()
        if code:
            return f"EPSG:{code}"
    except Exception:
        pass
    try:
        return str(crs)
    except Exception:
        return None


def make_langchain_geo_tools(default_input_file_ids: Optional[List[str]] = None) -> List[Any]:
    """Build the vector/shapefile StructuredTools (geopandas-backed).

    ``default_input_file_ids`` is the conversation's attached file set. The tools use it to
    auto-discover shapefile sidecars (.shx/.dbf/.prj) by basename, so the model only has to
    reference the .shp (or any single component) — passing ``sibling_file_ids`` is optional.
    """
    from langchain_core.tools import StructuredTool

    _attached = _index_attached(default_input_file_ids)

    def _stage(ref, sibling_file_ids=None):
        return _stage_vector_source(ref, sibling_file_ids, _attached)

    _SIB = ("For an uploaded shapefile, just pass the .shp's file_id (or any single component) — "
            "the tool auto-finds the .shx/.dbf/.prj among the attached files. You may also pass "
            "the other components as sibling_file_ids, or upload the shapefile as a single .zip.")

    def inspect_vector(file_id: str, sibling_file_ids: Optional[List[str]] = None,
                       layer: Optional[str] = None) -> str:
        """Read a vector dataset's metadata (CRS, extent, geometry type, feature count,
        attribute schema) WITHOUT loading all geometry. Accepts a TIGER/shapefile .zip,
        a .shp (+ sidecars), GeoJSON, GeoPackage, or GeoParquet. """
        tmp = None
        try:
            import pyogrio
            read_path, tmp = _stage(file_id, sibling_file_ids)
            try:
                layers = [str(n) for n in (pyogrio.list_layers(read_path)[:, 0])]
            except Exception:
                layers = []
            info = pyogrio.read_info(read_path, layer=layer) if layer else pyogrio.read_info(read_path)
            # fields/dtypes come back as numpy arrays — don't use `or []` (ambiguous truth value).
            _fields = info.get("fields")
            _dtypes = info.get("dtypes")
            fields = list(_fields) if _fields is not None else []
            dtypes = list(_dtypes) if _dtypes is not None else []
            cols = [{"name": str(f), "dtype": str(d)} for f, d in zip(fields, dtypes)]
            tb = info.get("total_bounds")
            return json.dumps({
                "ok": True,
                "driver": info.get("driver"),
                "layers": layers,
                "feature_count": int(info.get("features") or 0),
                "geometry_type": info.get("geometry_type"),
                "crs": info.get("crs"),
                "columns": cols,
                "bounds": [float(x) for x in tb] if tb is not None else None,
            }, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "hint": _SIB})
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def plot_vector(file_id: str, column: Optional[str] = None,
                    sibling_file_ids: Optional[List[str]] = None, layer: Optional[str] = None,
                    max_features: int = 50000, cmap: str = "viridis", title: Optional[str] = None) -> str:
        """Render a vector dataset to a PNG map and return a downloadable file_id. Pass
        `column` for a choropleth. Large layers are downsampled to `max_features`. """
        tmp = None
        png = None
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import geopandas as gpd
            from agent_runtime.file_store import create_output_file_from_path

            read_path, tmp = _stage(file_id, sibling_file_ids)
            gdf = gpd.read_file(read_path, layer=layer) if layer else gpd.read_file(read_path)
            total = len(gdf)
            downsampled = total > max_features
            if downsampled:
                gdf = gdf.sample(int(max_features), random_state=0)
            fig, ax = plt.subplots(figsize=(9, 9))
            if column and column in gdf.columns:
                gdf.plot(column=column, legend=True, cmap=cmap, ax=ax, linewidth=0.3, edgecolor="#444")
            else:
                gdf.plot(ax=ax, color="#3aa9a0", edgecolor="#1c5a97", linewidth=0.4, alpha=0.85)
            ax.set_axis_off()
            ax.set_title(title or f"{total} features" + (f" (showing {len(gdf)})" if downsampled else ""))
            fd, png = tempfile.mkstemp(prefix="vec_plot_", suffix=".png")
            os.close(fd)
            fig.savefig(png, bbox_inches="tight", dpi=150)
            plt.close(fig)
            rec = create_output_file_from_path(png, filename="vector_plot.png")
            return json.dumps({
                "ok": True, "file_id": rec["file_id"], "filename": rec.get("filename"),
                "download_url": rec.get("download_url"),
                "plotted_features": int(len(gdf)), "total_features": int(total),
                "downsampled": bool(downsampled), "crs": _epsg(getattr(gdf, "crs", None)),
            }, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "hint": _SIB})
        finally:
            if png and os.path.exists(png):
                try: os.remove(png)
                except OSError: pass
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def vector_to_geojson(file_id: str, sibling_file_ids: Optional[List[str]] = None,
                          layer: Optional[str] = None, target_crs: str = "EPSG:4326") -> str:
        """Convert a vector dataset to GeoJSON (reprojected to target_crs, default WGS84)
        and return a downloadable file_id. """
        tmp = None
        out = None
        try:
            import geopandas as gpd
            from agent_runtime.file_store import create_output_file_from_path

            read_path, tmp = _stage(file_id, sibling_file_ids)
            gdf = gpd.read_file(read_path, layer=layer) if layer else gpd.read_file(read_path)
            if target_crs and getattr(gdf, "crs", None) is not None:
                gdf = gdf.to_crs(target_crs)
            out = Path(tempfile.mkdtemp(prefix="vec_gj_")) / "vector.geojson"
            gdf.to_file(out, driver="GeoJSON")
            rec = create_output_file_from_path(out, filename="vector.geojson")
            return json.dumps({
                "ok": True, "file_id": rec["file_id"], "filename": rec.get("filename"),
                "download_url": rec.get("download_url"),
                "feature_count": int(len(gdf)), "crs": _epsg(getattr(gdf, "crs", None)),
            }, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "hint": _SIB})
        finally:
            if out and out.parent.exists():
                shutil.rmtree(out.parent, ignore_errors=True)
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def reproject_vector(file_id: str, target_crs: str, sibling_file_ids: Optional[List[str]] = None,
                         layer: Optional[str] = None) -> str:
        """Reproject a vector dataset to target_crs (e.g. "EPSG:5070") and return a
        downloadable GeoParquet file_id for further analysis. """
        tmp = None
        out = None
        try:
            import geopandas as gpd
            from agent_runtime.file_store import create_output_file_from_path

            read_path, tmp = _stage(file_id, sibling_file_ids)
            gdf = gpd.read_file(read_path, layer=layer) if layer else gpd.read_file(read_path)
            if getattr(gdf, "crs", None) is None:
                return json.dumps({"ok": False, "error": "source has no CRS (.prj missing); cannot reproject"})
            gdf = gdf.to_crs(target_crs)
            out = Path(tempfile.mkdtemp(prefix="vec_rp_")) / "reprojected.parquet"
            gdf.to_parquet(out)
            rec = create_output_file_from_path(out, filename="reprojected.parquet")
            return json.dumps({
                "ok": True, "file_id": rec["file_id"], "filename": rec.get("filename"),
                "download_url": rec.get("download_url"),
                "feature_count": int(len(gdf)), "crs": _epsg(gdf.crs),
            }, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "hint": _SIB})
        finally:
            if out and out.parent.exists():
                shutil.rmtree(out.parent, ignore_errors=True)
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def vector_spatial_join(left_file_id: str, right_file_id: str, how: str = "inner",
                            predicate: str = "intersects",
                            left_siblings: Optional[List[str]] = None,
                            right_siblings: Optional[List[str]] = None) -> str:
        """Spatial-join two vector datasets (e.g. points-in-TIGER-polygons). how:
        inner|left|right; predicate: intersects|within|contains. Returns a downloadable
        GeoParquet file_id of the joined result. """
        t1 = t2 = None
        out = None
        try:
            import geopandas as gpd
            from agent_runtime.file_store import create_output_file_from_path

            lp, t1 = _stage(left_file_id, left_siblings)
            rp, t2 = _stage(right_file_id, right_siblings)
            left = gpd.read_file(lp)
            right = gpd.read_file(rp)
            note = None
            if getattr(left, "crs", None) is not None and getattr(right, "crs", None) is not None:
                if left.crs != right.crs:
                    right = right.to_crs(left.crs)
            else:
                note = "one or both inputs lack a CRS; join performed without reprojection"
            joined = gpd.sjoin(left, right, how=how, predicate=predicate)
            out = Path(tempfile.mkdtemp(prefix="vec_sj_")) / "spatial_join.parquet"
            joined.to_parquet(out)
            rec = create_output_file_from_path(out, filename="spatial_join.parquet")
            res = {"ok": True, "file_id": rec["file_id"], "filename": rec.get("filename"),
                   "download_url": rec.get("download_url"),
                   "feature_count": int(len(joined)), "crs": _epsg(getattr(joined, "crs", None))}
            if note:
                res["note"] = note
            return json.dumps(res, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "hint": _SIB})
        finally:
            for t in (t1, t2):
                if t:
                    shutil.rmtree(t, ignore_errors=True)
            if out and out.parent.exists():
                shutil.rmtree(out.parent, ignore_errors=True)

    meta = {"category": "geo"}
    return [
        StructuredTool.from_function(func=inspect_vector, name="inspect_vector", metadata=meta,
            description=("Read a vector / shapefile's metadata (CRS, extent, geometry type, feature "
                         "count, attribute columns) without loading all geometry. Handles a TIGER/Line "
                         "shapefile .zip, a .shp (+ sidecars), GeoJSON, GeoPackage, or GeoParquet by "
                         "file_id. " + _SIB)),
        StructuredTool.from_function(func=plot_vector, name="plot_vector", metadata=meta,
            description=("Render a vector dataset to a PNG map (optionally a choropleth via `column`) "
                         "and return a downloadable file_id. Use this to VISUALIZE an uploaded "
                         "shapefile/TIGER layer. Large layers auto-downsample. " + _SIB)),
        StructuredTool.from_function(func=vector_to_geojson, name="vector_to_geojson", metadata=meta,
            description=("Convert a vector dataset to GeoJSON (reprojected to WGS84 by default) and "
                         "return a downloadable file_id, e.g. for web mapping. " + _SIB)),
        StructuredTool.from_function(func=reproject_vector, name="reproject_vector", metadata=meta,
            description=("Reproject a vector dataset to a target CRS (e.g. EPSG:5070 for equal-area "
                         "US analysis) and return a downloadable GeoParquet file_id. " + _SIB)),
        StructuredTool.from_function(func=vector_spatial_join, name="vector_spatial_join", metadata=meta,
            description=("Spatial-join two vector datasets (e.g. assign points to the TIGER polygon "
                         "they fall in). CRS is aligned automatically. Returns a downloadable "
                         "GeoParquet file_id.")),
    ]


__all__ = ["make_langchain_geo_tools"]
