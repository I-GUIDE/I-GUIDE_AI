from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


DEFAULT_OSM_XYZ_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def _write_result(spec: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    result_path = Path(str(spec["result_path"]))
    result_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, default=str), encoding="utf-8")


def _extent_payload(extent: Any) -> Dict[str, float]:
    return {
        "xmin": float(extent.xMinimum()),
        "ymin": float(extent.yMinimum()),
        "xmax": float(extent.xMaximum()),
        "ymax": float(extent.yMaximum()),
    }


def _init_qgis():
    from qgis.core import QgsApplication

    prefix = os.getenv("QGIS_PREFIX_PATH")
    if prefix:
        QgsApplication.setPrefixPath(prefix, True)
    qgs = QgsApplication([], False)
    qgs.initQgis()
    return qgs


def _load_layer(path: str, name: Optional[str], provider: str):
    from qgis.core import QgsRasterLayer, QgsVectorLayer

    layer_name = name or Path(path).stem or "layer"
    provider_name = (provider or "ogr").strip().lower()
    if provider_name in {"gdal", "raster"}:
        return QgsRasterLayer(path, layer_name, "gdal")
    return QgsVectorLayer(path, layer_name, provider or "ogr")


def _load_xyz_basemap(name: str, url: str):
    from qgis.core import QgsRasterLayer

    uri = f"type=xyz&url={url}&zmin=0&zmax=19"
    return QgsRasterLayer(uri, name or "OpenStreetMap", "wms")


def _geometry_type_name(layer: Any) -> Optional[str]:
    try:
        from qgis.core import QgsWkbTypes

        return QgsWkbTypes.displayString(layer.wkbType())
    except Exception:
        return None


def _style_vector_layer(layer: Any) -> None:
    try:
        from qgis.core import QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer, QgsWkbTypes
    except Exception:
        return

    geometry_type = QgsWkbTypes.geometryType(layer.wkbType())
    layer_name = (layer.name() or "").lower()
    if geometry_type == QgsWkbTypes.PointGeometry:
        symbol = QgsMarkerSymbol.createSimple(
            {
                "name": "circle",
                "color": "215,25,28,255",
                "outline_color": "255,255,255,255",
                "outline_width": "0.7",
                "size": "4.0",
            }
        )
    elif geometry_type == QgsWkbTypes.LineGeometry:
        symbol = QgsLineSymbol.createSimple(
            {
                "line_color": "37,99,235,255",
                "line_width": "0.8",
            }
        )
    elif geometry_type == QgsWkbTypes.PolygonGeometry:
        if "buffer" in layer_name:
            symbol = QgsFillSymbol.createSimple(
                {
                    "color": "251,191,36,90",
                    "outline_color": "217,119,6,230",
                    "outline_width": "0.8",
                }
            )
        else:
            symbol = QgsFillSymbol.createSimple(
                {
                    "color": "59,130,246,35",
                    "outline_color": "37,99,235,220",
                    "outline_width": "0.7",
                }
            )
    else:
        return
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _layer_summary(spec: Mapping[str, Any]) -> Dict[str, Any]:
    layer = _load_layer(
        str(spec.get("layer_path") or ""),
        spec.get("layer_name"),
        str(spec.get("provider") or "ogr"),
    )
    if not layer.isValid():
        return {
            "ok": False,
            "error": "Layer is not valid.",
            "layer_path": spec.get("layer_path"),
            "provider": spec.get("provider"),
        }

    payload: Dict[str, Any] = {
        "ok": True,
        "layer_path": spec.get("layer_path"),
        "name": layer.name(),
        "crs": layer.crs().authid() if layer.crs().isValid() else "",
        "extent": _extent_payload(layer.extent()),
    }
    if hasattr(layer, "fields"):
        fields = layer.fields()
        payload["layer_type"] = "vector"
        payload["geometry_type"] = _geometry_type_name(layer)
        payload["feature_count"] = int(layer.featureCount())
        payload["fields"] = [
            {
                "name": field.name(),
                "type": field.typeName(),
            }
            for field in fields
        ]
        sample_limit = int(spec.get("sample_limit") or 0)
        samples = []
        for feature in layer.getFeatures():
            if len(samples) >= sample_limit:
                break
            samples.append(
                {
                    "id": int(feature.id()),
                    "attributes": dict(zip([field.name() for field in fields], feature.attributes())),
                    "geometry_wkt": feature.geometry().asWkt() if feature.hasGeometry() else None,
                }
            )
        payload["sample_features"] = samples
    else:
        payload["layer_type"] = "raster"
        payload["width"] = int(layer.width())
        payload["height"] = int(layer.height())
        payload["band_count"] = int(layer.bandCount())
    return payload


def _parse_extent(raw: Optional[str]):
    if not raw:
        return None
    from qgis.core import QgsRectangle

    parsed = json.loads(raw)
    if isinstance(parsed, list) and len(parsed) == 4:
        return QgsRectangle(float(parsed[0]), float(parsed[1]), float(parsed[2]), float(parsed[3]))
    if isinstance(parsed, dict):
        return QgsRectangle(
            float(parsed["xmin"]),
            float(parsed["ymin"]),
            float(parsed["xmax"]),
            float(parsed["ymax"]),
        )
    raise ValueError("extent_json must be [xmin, ymin, xmax, ymax] or an object with xmin/ymin/xmax/ymax.")


def _transform_extent(extent: Any, source_crs: Any, target_crs: Any):
    if not source_crs or not source_crs.isValid() or not target_crs or not target_crs.isValid() or source_crs == target_crs:
        return extent
    from qgis.core import QgsCoordinateTransform, QgsProject

    transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
    return transform.transformBoundingBox(extent)


def _combine_extents(layers: Iterable[Any], target_crs: Optional[Any] = None):
    combined = None
    for layer in layers:
        extent = layer.extent()
        if target_crs is not None:
            extent = _transform_extent(extent, layer.crs(), target_crs)
        if combined is None:
            combined = extent
        else:
            combined.combineExtentWith(extent)
    return combined


def _expanded_extent(extent: Any, factor: float = 0.20):
    if extent is None:
        return None
    width = extent.width()
    height = extent.height()
    if width == 0:
        width = max(abs(extent.xMinimum()) * 0.0005, 1000.0)
    if height == 0:
        height = max(abs(extent.yMinimum()) * 0.0005, 1000.0)
    pad_x = width * factor
    pad_y = height * factor
    extent.setXMinimum(extent.xMinimum() - pad_x)
    extent.setXMaximum(extent.xMaximum() + pad_x)
    extent.setYMinimum(extent.yMinimum() - pad_y)
    extent.setYMaximum(extent.yMaximum() + pad_y)
    return extent


def _render_map(spec: Mapping[str, Any]) -> Dict[str, Any]:
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor, QImage, QPainter
    from qgis.core import QgsCoordinateReferenceSystem, QgsMapRendererCustomPainterJob, QgsMapSettings, QgsProject

    job_dir = Path(str(spec["job_dir"]))
    output_filename = Path(str(spec.get("output_filename") or "map.png")).name
    output_path = job_dir / output_filename

    data_layers = []
    invalid_layers = []
    for item in spec.get("layers") or []:
        if not isinstance(item, dict):
            invalid_layers.append({"layer": item, "error": "layer entry must be an object"})
            continue
        layer = _load_layer(
            str(item.get("path") or item.get("layer_path") or ""),
            item.get("name") or item.get("layer_name"),
            str(item.get("provider") or "ogr"),
        )
        if layer.isValid():
            if hasattr(layer, "wkbType"):
                _style_vector_layer(layer)
            QgsProject.instance().addMapLayer(layer, False)
            data_layers.append(layer)
        else:
            invalid_layers.append({"layer": item, "error": "layer is not valid"})

    if not data_layers:
        return {"ok": False, "error": "No valid layers to render.", "invalid_layers": invalid_layers}

    basemap_layer = None
    basemap = str(spec.get("basemap") or "").strip().lower()
    if basemap and basemap not in {"none", "false", "off", "0"}:
        basemap_url = str(spec.get("basemap_url") or DEFAULT_OSM_XYZ_URL)
        basemap_name = str(spec.get("basemap_name") or "OpenStreetMap")
        candidate = _load_xyz_basemap(basemap_name, basemap_url)
        if candidate.isValid():
            QgsProject.instance().addMapLayer(candidate, False)
            basemap_layer = candidate
        else:
            invalid_layers.append({"layer": {"basemap": basemap, "url": basemap_url}, "error": "basemap layer is not valid"})

    width = int(spec.get("width") or 1200)
    height = int(spec.get("height") or 800)
    map_crs = QgsCoordinateReferenceSystem(str(spec.get("crs") or ("EPSG:3857" if basemap_layer else "")))
    if not map_crs.isValid():
        map_crs = data_layers[0].crs()
    # QgsMapSettings expects the top-most layer first. Treat layers_json order as
    # bottom-to-top for callers, and always keep the basemap at the bottom.
    render_layers = list(reversed(data_layers)) + ([basemap_layer] if basemap_layer else [])
    settings = QgsMapSettings()
    settings.setLayers(render_layers)
    settings.setOutputSize(QSize(width, height))
    if map_crs.isValid():
        settings.setDestinationCrs(map_crs)
    extent = _parse_extent(spec.get("extent_json")) or _combine_extents(data_layers, map_crs if map_crs.isValid() else None)
    extent = _expanded_extent(extent)
    settings.setExtent(extent)
    settings.setBackgroundColor(QColor("white"))

    image = QImage(QSize(width, height), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    painter = QPainter(image)
    job = QgsMapRendererCustomPainterJob(settings, painter)
    job.start()
    job.waitForFinished()
    painter.end()
    image.save(str(output_path))

    return {
        "ok": output_path.exists(),
        "output_path": str(output_path),
        "width": width,
        "height": height,
        "extent": _extent_payload(extent),
        "crs": map_crs.authid() if map_crs.isValid() else "",
        "layer_count": len(render_layers),
        "data_layer_count": len(data_layers),
        "basemap": basemap_layer.name() if basemap_layer else None,
        "invalid_layers": invalid_layers,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: python -m rag_pipeline.qgis_pyqgis_worker <layer_summary|render_map> <job_spec.json>", file=sys.stderr)
        return 2

    operation = argv[1]
    spec_path = Path(argv[2])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    qgs = None
    try:
        qgs = _init_qgis()
        if operation == "layer_summary":
            payload = _layer_summary(spec)
        elif operation == "render_map":
            payload = _render_map(spec)
        else:
            payload = {"ok": False, "error": f"Unsupported PyQGIS operation: {operation}"}
        _write_result(spec, payload)
        return 0 if payload.get("ok") else 1
    except Exception as exc:
        _write_result(
            spec,
            {
                "ok": False,
                "error": str(exc),
                "exception_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
            },
        )
        return 1
    finally:
        if qgs is not None:
            qgs.exitQgis()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
