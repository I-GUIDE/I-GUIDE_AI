from __future__ import annotations

import json
import math
import os
import re
import time
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import logging
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import requests
from requests.adapters import HTTPAdapter, Retry

try:  # pragma: no cover - optional dependency
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None  # type: ignore

from ..state import EvidenceEntry, ensure_state_shapes, get_query_text, merge_retrieval

from .utils import get_logger

@dataclass
class GeoAsset:
    id: str
    title: str
    abstract: Optional[str]
    keywords: List[str]
    bbox: Optional[Tuple[float, float, float, float]]
    datetime: Optional[Tuple[Optional[str], Optional[str]]]
    license: Optional[str]
    links: Dict[str, str]
    source: str
    provider: Optional[str]


class OpenGeoDataError(Exception):
    pass


class NLQueryError(Exception):
    pass


API_BASE_ENV_VARS: Sequence[str] = (
    "VLLM_PROXY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "ANVILGPT_URL",
    "API_BASE",
)
API_KEY_ENV_VARS: Sequence[str] = ("VLLM_API_KEY", "OPENAI_API_KEY", "OPENAI_KEY", "ANVILGPT_KEY", "API_KEY")
DEFAULT_PROVIDERS: Dict[str, Any] = {
    # "stac": ["https://planetarycomputer.microsoft.com/api/stac/v1"],
    "records": [],
    #"ckan": [("https://api.gsa.gov/technology/datagov/v3/action", None)],
    "cmr": True,
    "socrata": True,
    "datagov": True,
    # DOI-registered datasets worldwide (Zenodo, PANGAEA, USGS, NERC, Dryad, ...) — keyless.
    "datacite": True,
}
DEFAULT_NL_MODEL = "Qwen/Qwen3.5-9B"


def session(timeout: int = 12) -> requests.Session:
    sess = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
    )
    sess.mount("https://", HTTPAdapter(max_retries=retries))
    sess.mount("http://", HTTPAdapter(max_retries=retries))
    original_request = sess.request

    def _request(method: str, url: str, **kwargs: Any):
        kwargs.setdefault("timeout", timeout)
        return original_request(method, url, **kwargs)

    sess.request = _request  # type: ignore[assignment]
    return sess


logger = get_logger("opengeodata_search")


def norm_bbox(bbox: Any) -> Optional[Tuple[float, float, float, float]]:
    if not bbox:
        return None
    if isinstance(bbox, dict) and "bbox" in bbox:
        bbox = bbox["bbox"]
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if isinstance(bbox, dict) and bbox.get("type") == "Polygon":
        coords = bbox.get("coordinates", [[]])[0]
        xs = [coord[0] for coord in coords]
        ys = [coord[1] for coord in coords]
        return (min(xs), min(ys), max(xs), max(ys))
    return None
