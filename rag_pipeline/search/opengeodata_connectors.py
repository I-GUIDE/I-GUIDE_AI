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

DATACITE_API = "https://api.datacite.org/dois"


def _datacite_bbox(geo_locations: Any) -> Optional[Tuple[float, float, float, float]]:
    """First usable extent from DataCite ``geoLocations``.

    Prefers an explicit ``geoLocationBox``; falls back to a ``geoLocationPoint`` expressed as a
    degenerate box so point-located records still carry coordinates. ``geoLocationPlace`` (a bare
    place name) yields nothing — geocoding names here would invent precision the record lacks.
    """
    if not isinstance(geo_locations, list):
        return None
    for entry in geo_locations:
        if not isinstance(entry, dict):
            continue
        box = entry.get("geoLocationBox")
        if isinstance(box, dict):
            try:
                west = float(box["westBoundLongitude"]); east = float(box["eastBoundLongitude"])
                south = float(box["southBoundLatitude"]); north = float(box["northBoundLatitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if -180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= 90 and -90 <= north <= 90:
                return (min(west, east), min(south, north), max(west, east), max(south, north))
        point = entry.get("geoLocationPoint")
        if isinstance(point, dict):
            try:
                lon = float(point["pointLongitude"]); lat = float(point["pointLatitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if -180 <= lon <= 180 and -90 <= lat <= 90:
                return (lon, lat, lon, lat)
    return None


def _datacite_dates(attributes: Mapping[str, Any]) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """(start, end) from DataCite ``dates`` — a Collected/Created range when present, else the
    publication year as a single point in time."""
    dates = attributes.get("dates")
    start = end = None
    if isinstance(dates, list):
        for item in dates:
            if not isinstance(item, dict):
                continue
            value = str(item.get("date") or "").strip()
            if not value:
                continue
            if str(item.get("dateType") or "").lower() in ("collected", "created", "coverage"):
                if "/" in value:                      # ISO interval "2001-01-01/2010-12-31"
                    left, _, right = value.partition("/")
                    start, end = start or left.strip() or None, end or right.strip() or None
                else:
                    start = start or value
            elif not start:
                start = value
    if not start:
        year = attributes.get("publicationYear")
        if year:
            start = f"{year}-01-01"
    return (start, end) if (start or end) else None


def _datacite_license(attributes: Mapping[str, Any]) -> Optional[str]:
    rights = attributes.get("rightsList")
    if isinstance(rights, list):
        for item in rights:
            if isinstance(item, dict):
                label = item.get("rightsIdentifier") or item.get("rights") or item.get("rightsUri")
                if label:
                    return str(label)
    return None


def _datacite_text(entries: Any, *, prefer: str = "Abstract") -> Optional[str]:
    """Longest description, preferring the Abstract type."""
    if not isinstance(entries, list):
        return None
    preferred, others = [], []
    for item in entries:
        if not isinstance(item, dict):
            continue
        text = str(item.get("description") or "").strip()
        if not text:
            continue
        (preferred if str(item.get("descriptionType") or "") == prefer else others).append(text)
    pool = preferred or others
    return max(pool, key=len) if pool else None


def search_datacite(
    q: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    time_range: Optional[Tuple[Optional[str], Optional[str]]] = None,
    limit: int = 10,
    resource_type: str = "dataset",
) -> List[GeoAsset]:
    """Free-text search across DOI-registered datasets via DataCite (keyless, global).

    Broadens discovery far beyond the hard-coded portals — DataCite indexes millions of datasets
    from Zenodo, PANGAEA, USGS, NERC, Dryad and thousands of other repositories — and maps 1:1
    onto ``GeoAsset``: DOI as a stable id, full abstract, subjects as keywords, ``geoLocationBox``
    as the extent, ``rightsList`` as the license, publisher as the provider. Note that many
    records (Zenodo especially) declare no geolocation, so ``bbox`` is often None; it is used here
    only as a relevance hint, since the API offers no reliable bbox filter.
    """
    logger.info(f"DataCite search called with: q='{q}', bbox={bbox}, limit={limit}")
    if not (q or "").strip():
        return []
    params: Dict[str, Any] = {
        "query": q,
        "page[size]": min(max(limit, 1), 50),
        "affiliation": "false",
    }
    if resource_type:
        params["resource-type-id"] = resource_type
    if time_range and time_range[0]:
        year = str(time_range[0])[:4]
        if year.isdigit():
            params["query"] = f"{q} AND publicationYear:[{year} TO *]"

    response = session().get(DATACITE_API, params=params, headers={"Accept": "application/json"})
    response.raise_for_status()
    records = (response.json() or {}).get("data") or []

    assets: List[GeoAsset] = []
    # Repositories mint a DOI per VERSION (Zenodo especially), so the same dataset can occupy
    # several result slots. Keep the first (best-ranked) of each title+publisher pair.
    seen: set = set()
    for record in records:
        if len(assets) >= limit:
            break
        attributes = (record or {}).get("attributes") or {}
        doi = str(attributes.get("doi") or record.get("id") or "").strip()
        titles = attributes.get("titles")
        title = ""
        if isinstance(titles, list) and titles:
            first = titles[0]
            title = str(first.get("title") if isinstance(first, dict) else first or "").strip()
        dedupe_key = (title.strip().lower(), str(attributes.get("publisher") or "").strip().lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        subjects = attributes.get("subjects")
        keywords = [
            str(item.get("subject") if isinstance(item, dict) else item).strip()
            for item in (subjects if isinstance(subjects, list) else [])
            if (item.get("subject") if isinstance(item, dict) else item)
        ]
        landing = str(attributes.get("url") or (f"https://doi.org/{doi}" if doi else "")).strip()
        links: Dict[str, str] = {}
        if landing:
            links["Landing Page"] = landing
        content_url = attributes.get("contentUrl")
        if isinstance(content_url, list) and content_url:
            links["Content"] = str(content_url[0])
        elif isinstance(content_url, str) and content_url:
            links["Content"] = content_url

        assets.append(
            GeoAsset(
                id=doi or landing or f"datacite-{len(assets)}",
                title=title or "Untitled dataset",
                abstract=_datacite_text(attributes.get("descriptions")),
                keywords=keywords,
                bbox=_datacite_bbox(attributes.get("geoLocations")),
                datetime=_datacite_dates(attributes),
                license=_datacite_license(attributes),
                links=links,
                source="datacite",
                provider=str(attributes.get("publisher") or "") or None,
            )
        )
    logger.info(f"DataCite returned {len(assets)} assets")
    return assets
