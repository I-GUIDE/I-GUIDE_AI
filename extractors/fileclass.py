"""File-type classification + dispatch tables for the two ingest paths.

- ``classify_github(repo_dir)``  -> notebook (.ipynb) + code (.py) files in a clone.
- ``classify_upload(path)``      -> "dataset" | "publication" | "unknown" for a
  webhook-delivered file. Dataset coverage matches the EXTRACTOR_DESIGN.md §7 matrix.

Extension sets are the single place to extend format coverage.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from .base import KIND_CODE_BLOCK, KIND_DATASET, KIND_NOTEBOOK_BLOCK, KIND_PUBLICATION

# ---- GitHub path -------------------------------------------------------------
NOTEBOOK_EXT = {".ipynb"}
CODE_EXT = {".py"}

# ---- Upload path: dataset format families (see EXTRACTOR_DESIGN.md §7) --------
RASTER_EXT = {".tif", ".tiff", ".nc", ".hdf", ".h5", ".he5", ".grib", ".grb",
              ".grib2", ".zarr", ".asc", ".img", ".vrt"}
VECTOR_EXT = {".shp", ".geojson", ".gpkg", ".gdb", ".kml", ".kmz", ".gml",
              ".fgb", ".parquet", ".geoparquet"}
TABULAR_EXT = {".csv", ".tsv", ".xlsx", ".xls"}
CONTAINER_EXT = {".zip", ".tar", ".tgz", ".gz"}
SIDECAR_EXT = {".prj", ".cpg", ".dbf", ".shx", ".xml", ".json"}  # json/xml may be STAC/ISO
DATASET_EXT = RASTER_EXT | VECTOR_EXT | TABULAR_EXT | CONTAINER_EXT | SIDECAR_EXT

# ---- Upload path: publication document families ------------------------------
PUBLICATION_EXT = {".pdf", ".docx", ".doc", ".tex"}

# Directories that are themselves a single dataset (not walked into).
DATASET_DIR_SUFFIXES = (".gdb", ".zarr")

_SKIP_DIRS = {".git", ".github", "__pycache__", ".ipynb_checkpoints",
              "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


def classify_github(repo_dir: str) -> Dict[str, List[str]]:
    """Walk a cloned repo and bucket files into notebook/code by extension."""
    out: Dict[str, List[str]] = {KIND_NOTEBOOK_BLOCK: [], KIND_CODE_BLOCK: []}
    root = Path(repo_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            ext = Path(fn).suffix.lower()
            full = str(Path(dirpath) / fn)
            if ext in NOTEBOOK_EXT:
                out[KIND_NOTEBOOK_BLOCK].append(full)
            elif ext in CODE_EXT:
                out[KIND_CODE_BLOCK].append(full)
    return out


def upload_kind(path: str) -> str:
    """Return the extractor family for one uploaded path: dataset/publication/unknown."""
    name = str(path)
    lower = name.lower()
    for suffix in DATASET_DIR_SUFFIXES:
        if lower.rstrip("/").endswith(suffix):
            return KIND_DATASET
    ext = Path(name).suffix.lower()
    if ext in PUBLICATION_EXT:
        return KIND_PUBLICATION
    if ext in DATASET_EXT:
        return KIND_DATASET
    return "unknown"


def classify_upload(path: str) -> str:
    """Public alias for the webhook: 'dataset' | 'publication' | 'unknown'."""
    kind = upload_kind(path)
    if kind == KIND_DATASET:
        return "dataset"
    if kind == KIND_PUBLICATION:
        return "publication"
    return "unknown"


__all__ = [
    "NOTEBOOK_EXT", "CODE_EXT", "DATASET_EXT", "PUBLICATION_EXT",
    "RASTER_EXT", "VECTOR_EXT", "TABULAR_EXT", "CONTAINER_EXT", "SIDECAR_EXT",
    "classify_github", "classify_upload", "upload_kind",
]
