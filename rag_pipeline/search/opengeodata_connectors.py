from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from requests.adapters import HTTPAdapter, Retry

import json

try:  # pragma: no cover - optional dependency
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None  # type: ignore

from .opengeodata_utils import *

def search_cmr_collections(
    q: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    time_range: Optional[Tuple[Optional[str], Optional[str]]] = None,
    limit: int = 10,
) -> List[GeoAsset]:
    logger.info(f"CMR search_cmr_collections called with: q='{q}', bbox={bbox}, time_range={time_range}, limit={limit}")
    params: Dict[str, Any] = {"page_size": limit, "include_has_granules": "true"}
    sess = session()
    if q:
        params["keyword"] = q
        # # CMR supports multiple search strategies - try both keyword and text search
        # # Fix common typos: "lancover" -> "landcover", "land cover"
        # query_normalized = q.lower().strip()
        # if "lancover" in query_normalized:
        #     query_normalized = query_normalized.replace("lancover", "landcover")
        # params["keyword"] = query_normalized
        # # Also try as text search for better matching
        # params["text"] = query_normalized
    if bbox:
        params["bounding_box"] = ",".join(map(str, bbox))
    if time_range:
        start, end = time_range
        if start or end:
            params["temporal"] = f"{start or ''},{end or ''}"
    response = sess.get("https://cmr.earthdata.nasa.gov/search/collections.json", params=params)
    response.raise_for_status()
    entries = response.json().get("feed", {}).get("entry", []) or []

    assets: List[GeoAsset] = []
    for entry in entries:
        box = None
        if entry.get("boxes"):
            try:
                minlat, minlon, maxlat, maxlon = map(float, entry["boxes"][0].split())
                box = (minlon, minlat, maxlon, maxlat)
            except Exception:
                pass
        landing = ""
        for link in entry.get("links", []):
            rel = link.get("rel", "")
            if rel.endswith("/data#") or rel.endswith("/documentation#") or link.get("href"):
                landing = link.get("href", "")
                break
        assets.append(
            GeoAsset(
                id=entry["id"],
                title=entry.get("dataset_id") or entry.get("short_name") or entry["id"],
                abstract=entry.get("summary"),
                keywords=[
                    ", ".join(keyword.values()) if isinstance(keyword, dict) else str(keyword)
                    for keyword in (entry.get("science_keywords") or [])
                ],
                bbox=box,
                datetime=(entry.get("time_start"), entry.get("time_end")),
                license=None,
                links={"landing": landing, "api": "https://cmr.earthdata.nasa.gov/search/"},
                source="cmr",
                provider=entry.get("archive_center") or entry.get("data_center"),
            )
        )
    return assets


def search_socrata(
    q: Optional[str] = None,
    categories: Optional[List[str]] = None,
    domains: Optional[List[str]] = None,
    limit: int = 10,
) -> List[GeoAsset]:
    """
    Indexes datasets across every public Socrata-hosted open data portal at once, 
    including a lot of city/state portals (e.g. Chicago, NYC) that publish non-geospatial topical
    data like crime, permits, health, etc. Socrata also auto-categorizes
    datasets (e.g. anything tagged "police"/"public safety" lands under the
    "Crime" category), `categories` can be filter directly.
    """

    url = "https://api.us.socrata.com/api/catalog/v1"

    logger.info(f"Socrata search called with: q='{q}', categories={categories}, domains={domains}, limit={limit}")
    sess = session()
    params: Dict[str, Any] = {"limit": limit, "only": "datasets"}
    
    if q: params["q"] = q
    if categories: params["categories"] = categories
    if domains: params["domains"] = domains
 
    response = sess.get(url, params=params)
    response.raise_for_status()
    results = response.json().get("results", []) or []
 
    assets: List[GeoAsset] = []
    for item in results[:limit]:
        resource = item.get("resource", {}) or {}
        classification = item.get("classification", {}) or {}
        metadata = item.get("metadata", {}) or {}
 
        tags = list(classification.get("tags") or [])
        tags += list(classification.get("domain_tags") or [])
        domain_category = classification.get("domain_category")
        if domain_category: tags.append(domain_category)
 
        landing = item.get("permalink") or item.get("link") or ""
        domain = metadata.get("domain") or ""
 
        assets.append(
            GeoAsset(
                id=str(resource.get("id") or landing or f"socrata-{len(assets)}"),
                title=resource.get("name") or "Untitled Socrata dataset",
                abstract=resource.get("description"),
                keywords=[tag for tag in tags if tag],
                bbox=None,
                datetime=(resource.get("createdAt"), resource.get("updatedAt")),
                license=resource.get("license"),
                links={"landing": landing, "api": url},
                source="socrata",
                provider=domain or resource.get("attribution"),
            )
        )

    return assets


def bbox_to_geojson_polygon(bbox: Tuple[float, float, float, float]) -> Dict[str, Any]:
    minlon, minlat, maxlon, maxlat = bbox
    return {
        "type": "Polygon",
        "coordinates": [[
            [minlon, minlat],
            [maxlon, minlat],
            [maxlon, maxlat],
            [minlon, maxlat],
            [minlon, minlat],
        ]],
    }
 
 
def parse_dcat_temporal(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    # DCAT-US 'temporal' is a single string like '2018-01-01/2018-09-28'
    if not value:
        return (None, None)
    parts = value.split("/")
    if len(parts) == 2:
        return (parts[0] or None, parts[1] or None)
    return (value, value)


def get_dcat_links(item):
    arr = None
    if isinstance(item, dict):
        arr = item.get("distribution")
        if arr is None:
            dcat = item.get("dcat") or {}
            if isinstance(dcat, dict):
                arr = dcat.get("distribution")

    if not arr:
        return {}

    links: Dict[str, str] = {}
    if isinstance(arr, dict):
        for k, v in arr.items():
            if isinstance(v, (list, tuple)):
                for idx, entry in enumerate(v):
                    if isinstance(entry, dict):
                        url = entry.get("accessURL")
                        title = entry.get("title") or entry.get("mediaType") or f"link{idx}"
                        if url:
                            links[str(title)] = url
            elif isinstance(v, dict):
                url = v.get("accessURL") or v.get("url") or v.get("downloadURL")
                title = v.get("title") or v.get("mediaType") or str(k)
                if url:
                    links[str(title)] = url
            elif isinstance(v, str):
                links[str(k)] = v
    else:
        try:
            for idx, entry in enumerate(arr):
                if not isinstance(entry, dict):
                    continue
                url = entry.get("accessURL") or entry.get("url") or entry.get("downloadURL")
                title = entry.get("title") or entry.get("mediaType") or f"link{idx}"
                if url:
                    links[str(title)] = url
        except Exception:
            return {}

    return links


def search_datagov_catalog(
    q: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    org_type: Optional[str] = None,
    limit: int = 10,
) -> List[GeoAsset]:
    url = "https://catalog.data.gov/search"
    logger.info(f"data.gov Catalog search called with: q='{q}', bbox={bbox}, org_type={org_type}, limit={limit}")
    params: Dict[str, Any] = {"per_page": min(max(limit, 1), 100)}
    sess = session()
    if q:
        params["q"] = q
    if org_type:
        params["org_type"] = org_type
    if bbox:
        params["spatial_geometry"] = json.dumps(bbox_to_geojson_polygon(bbox))
        params["spatial_within"] = "false"  # intersects the box, rather than fully-contained-by
 
    response = sess.get(url, params=params)
    response.raise_for_status()
    results = (response.json() or {}).get("results", []) or []
 
    assets: List[GeoAsset] = []
    for item in results[:limit]:
        dcat = item.get("dcat", {}) or {}
        organization = item.get("organization", {}) or {}
 
        landing = dcat.get("landingPage") or item.get("harvest_record") or ""
        spatial_shape = item.get("spatial_shape")
        asset_bbox = norm_bbox(spatial_shape) if spatial_shape else None
        datetime_range = parse_dcat_temporal(dcat.get("temporal"))
        publisher = item.get("publisher") or organization.get("name")
        keywords = item.get("keyword") or dcat.get("keyword") or []
 
        lic = None
        if isinstance(dcat, dict):
            lic = dcat.get("license")
        pub = item.get("publisher")
        if not lic:
            if isinstance(pub, dict):
                lic = pub.get("license")
            elif isinstance(pub, str):
                lic = None

        assets.append(
            GeoAsset(
                id=str(item.get("identifier") or dcat.get("identifier") or landing or f"datagov-{len(assets)}"),
                title=item.get("title") or dcat.get("title") or "Untitled dataset",
                abstract=item.get("description") or dcat.get("description"),
                keywords=list(keywords),
                bbox=asset_bbox,
                datetime=datetime_range,
                license=lic,
                links=get_dcat_links(item),
                source="datagov",
                provider=publisher,
            )
        )
    return assets
