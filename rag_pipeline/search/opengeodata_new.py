from __future__ import annotations

import os
import os
from dataclasses import dataclass, asdict
from pathlib import Path
import logging
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import requests
from requests.adapters import HTTPAdapter, Retry

try:  # pragma: no cover - optional dependency
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None  # type: ignore

from .opengeodata_utils import *
from .opengeodata_connectors import search_cmr_collections, search_datagov_catalog, search_socrata


def hydrate_api_credentials_from_env_files() -> None:
    if not dotenv_values:
        return
    env_candidates = [
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().with_name(".env"),
        Path(__file__).resolve().parents[1] / "opengeodata_prototype" / ".env",
    ]
    target_keys = tuple(set(API_BASE_ENV_VARS + API_KEY_ENV_VARS))
    for env_path in env_candidates:
        if not env_path.exists():
            continue
        try:
            values = dotenv_values(env_path)
        except Exception:
            continue
        if not isinstance(values, dict):
            continue
        for key in target_keys:
            if key in os.environ:
                continue
            val = values.get(key)
            if val:
                os.environ[key] = val
        break


hydrate_api_credentials_from_env_files()

def discover(
    query: str = "",
    bbox: Optional[Tuple[float, float, float, float]] = None,
    time_range: Optional[Tuple[Optional[str], Optional[str]]] = None,
    limit: int = 6,
    providers: Optional[Dict[str, Any]] = None,
) -> List[GeoAsset]:
    providers = providers or dict(DEFAULT_PROVIDERS)
    logger.info(f"OpenGeoData discover() called: query='{query}', limit={limit}, providers={list(providers.keys())}")
    results: List[GeoAsset] = []

    cmr_count = 0
    if providers.get("cmr"):
        try:
            cmr_results = search_cmr_collections(query, bbox=bbox, time_range=time_range, limit=limit)
            cmr_count = len(cmr_results)
            results += cmr_results
            logger.info(f"OpenGeoData CMR provider returned {cmr_count} results")
        except Exception:
            logger.exception("CMR search failed")

    datagov_count = 0
    datagov_cfg = providers.get("datagov")
    if datagov_cfg:
        datagov_org_type = datagov_cfg.get("org_type") if isinstance(datagov_cfg, Mapping) else None
        try:
            datagov_results = search_datagov_catalog(
                q=query, bbox=bbox, org_type=datagov_org_type, limit=limit
            )
            datagov_count = len(datagov_results)
            results += datagov_results
            logger.info(f"OpenGeoData data.gov Catalog provider returned {datagov_count} results")
        except Exception:
            logger.exception("data.gov Catalog search failed")

    
    socrata_count = 0
    socrata_cfg = providers.get("socrata")
    if socrata_cfg:
        socrata_categories = socrata_cfg.get("categories") if isinstance(socrata_cfg, Mapping) else None
        socrata_domains = socrata_cfg.get("domains") if isinstance(socrata_cfg, Mapping) else None
        try:
            socrata_results = search_socrata(
                q=query, categories=socrata_categories, domains=socrata_domains, limit=limit
            )
            socrata_count = len(socrata_results)
            results += socrata_results
            logger.info(f"OpenGeoData Socrata provider returned {socrata_count} results")
        except Exception:
            logger.exception("Socrata search failed")

    
    logger.info(f"OpenGeoData discover() completed: total results={len(results)} (CMR:{cmr_count}) (Data.gov: {datagov_count}) (Socrata: {socrata_count})")

    max_results = limit * (
        int(bool(providers.get("cmr")))
        + int(bool(providers.get("datagov")))
        + int(bool(providers.get("socrata")))
    )

    return results[: max(1, max_results)]


def score(
    asset: GeoAsset,
    query_terms: List[str],
    bbox: Optional[Tuple[float, float, float, float]] = None,
    time_range: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> float:
    text = " ".join(
        [
            asset.title or "",
            asset.abstract or "",
            " ".join(asset.keywords or []),
        ]
    ).lower()
    text_score = sum(1.0 for term in query_terms if term and term.lower() in text)
    spatiotemporal_score = 0.0
    if bbox and asset.bbox:
        ax1, ay1, ax2, ay2 = asset.bbox
        bx1, by1, bx2, by2 = bbox
        inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
        inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
        inter = inter_w * inter_h
        if inter > 0:
            area = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
            if area > 0:
                spatiotemporal_score += inter / area
    if time_range and asset.datetime and (asset.datetime[0] or asset.datetime[1]):
        spatiotemporal_score += 0.2
    license_bonus = 0.2 if (asset.license and "by" in asset.license.lower()) else 0.0
    return text_score + spatiotemporal_score + license_bonus



def iso_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", value.strip())
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def valid_bbox(b: Optional[List[float]]) -> Optional[Tuple[float, float, float, float]]:
    if not b or len(b) < 4:
        return None
    x1, y1, x2, y2 = map(float, b[:4])
    if not (-180.0 <= x1 <= 180.0 and -180.0 <= x2 <= 180.0 and -90.0 <= y1 <= 90.0 and -90.0 <= y2 <= 90.0):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def extract_json_payload(text: str) -> str:
    """Strip markdown code fences and surrounding prose so json.loads doesn't choke
    on otherwise-valid LLM output (e.g. a response wrapped in ```json ... ```)."""
    if not text:
        return text
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return cleaned


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "opengeodata-prototype (contact: help@i-guide.io)"
geocode_cache: Dict[str, Optional[Tuple[float, float, float, float]]] = {}
last_geocode_call = 0.0

def geocode_place(place: str) -> Optional[Tuple[float, float, float, float]]:
    """Resolve a place name (e.g. 'Chicago, IL', 'Illinois') to an approximate
    (minlon, minlat, maxlon, maxlat) bbox via OpenStreetMap's Nominatim search.
 
    Free and keyless, but rate-limited to ~1 req/sec per Nominatim's usage
    policy, so results are cached per place name for the life of the process.
    """
    global last_geocode_call
    place = (place or "").strip()
    if not place:
        return None
    if place in geocode_cache:
        return geocode_cache[place]
 
    elapsed = time.monotonic() - last_geocode_call
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
 
    try:
        resp = session().get(
            NOMINATIM_URL,
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": NOMINATIM_USER_AGENT},
        )
        last_geocode_call = time.monotonic()
        resp.raise_for_status()
        results = resp.json()
        if not results:
            logger.info(f"Geocoding found no match for place={place!r}")
            geocode_cache[place] = None
            return None
        raw_bbox = results[0].get("boundingbox")  # [south, north, west, east] as strings
        if not raw_bbox or len(raw_bbox) < 4:
            geocode_cache[place] = None
            return None
        south, north, west, east = map(float, raw_bbox)
        bbox = valid_bbox([west, south, east, north])
        geocode_cache[place] = bbox
        logger.info(f"Geocoded place={place!r} -> bbox={bbox}")
        return bbox
    except Exception:
        logger.exception("Geocoding failed for place=%r", place)
        geocode_cache[place] = None
        return None
    

from ..llm_utils import call_llm as _call_llm


def call_my_llm(prompt: str) -> str:
    """Normalize an OpenGeoData NL query via the system's configured LLM (VLLM / AnvilGPT /
    OpenAI-compatible, resolved by ``llm_utils.call_llm``) — the same model the rest of the agent
    uses — instead of a hardcoded OpenAI gpt-4o-mini client."""
    logger.info("OpenGeoData NL: calling internal LLM for query normalization")
    return _call_llm(prompt)

 
def get_q_bbox_timer_openai(
    user_query: str,
    current_date: str,
    api_base: Optional[str] = None,  # Ignored, uses llm_utils
    api_key: Optional[str] = None,    # Ignored, uses llm_utils
    model: Optional[str] = None,      # Ignored, uses llm_utils
    timeout: int = 20,                # Ignored
    default_bbox: Optional[Tuple[float,float,float,float]] = None,
    default_timer: Optional[Tuple[Optional[str],Optional[str]]] = None,
    max_retries: int = 2              # Ignored
) -> Tuple[str, Optional[Tuple[float,float,float,float]], Optional[Tuple[Optional[str],Optional[str]]]]:
    print("Extracting query, bbox, temporal data")

    prompt = f"""You are a geospatial query normalizer. Today is {current_date}.

        Given a user query, extract:
        - q: compact keyword query (no locations, no dates) for catalog search, not a natural-language phrase.
            Remove filler words like "of", "for", "about", "show", "find", "risk of".
            Keep only the core dataset/search terms.
            Prefer 1-3 important keywords.
        - place: a human-readable place name if a location is mentioned (e.g. "Chicago, IL", "Illinois"), else null
        - timer: [start_date, end_date] if time mentioned, else [null, null]
        
        Examples:
        - "risk of aging dams in Chicago" → {{"q": "dams risk", "place": "Chicago, IL", "timer": [null, null]}}
        - "how do crime rates differ across Illinois" → {{"q": "crime rates", "place": "Illinois", "timer": [null, null]}}
        - "air quality monitoring datasets in Chicago" → {{"q": "air quality monitoring", "place": "Chicago, IL", "timer": [null, null]}}
        - "recent wildfire datasets in California after 2020" → {{"q": "wildfire", "place": "California", "timer": ["2020-01-01", null]}}
        - "dams in Illinois" → {{"q": "dams", "place": "Illinois", "timer": [null, null]}}

        User query: {user_query}
        
        Respond with ONLY JSON: {{"q":"...","place":"..."|null,"timer":[...]}}"""

    try:
        response = call_my_llm(prompt)
        data = json.loads(extract_json_payload(response))
        
        q = str(data.get("q", "")).strip()
        if not q:
            raise ValueError("Empty q")
        
        place = data.get("place")
        bbox = geocode_place(place) if place else None
        timer_raw = data.get("timer")
        timer: Optional[Tuple[Optional[str], Optional[str]]] = None
        if isinstance(timer_raw, list) and len(timer_raw) >= 2:
            s = iso_date(timer_raw[0])
            e = iso_date(timer_raw[1])
            timer = (s, e)
        
        if bbox is None:
            bbox = default_bbox
        if (timer is None or (timer[0] is None and timer[1] is None)) and default_timer:
            timer = default_timer
        
        return q, bbox, timer
        
    except Exception as e:
        raise NLQueryError(f"NL parse failed: {e}")


def asset_to_dict(asset: GeoAsset) -> Dict[str, Any]:
    data = asdict(asset)
    if data.get("bbox") is not None:
        data["bbox"] = list(data["bbox"])
    return data


def run_opengeodata(
    query: Optional[str] = None,
    bbox: Optional[List[float]] = None,
    timer: Optional[List[Optional[str]]] = None,
    call_llm: Optional[int] = 1,
    limit: int = 10,
    providers: Optional[Dict[str, Any]] = None,
    nl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    logger.info("OpenGeoData run_opengeodata() called")
    try:
        if nl or call_llm:
            try:
                logger.info("OpenGeoData NL parsing enabled; processing NL query.")
                q, bb, tt = get_q_bbox_timer_openai(
                    query,
                    datetime.now().strftime("%Y-%m-%d")
                    # current_date=nl["current_date"],
                    # api_base=nl.get("api_base"),
                    # api_key=nl.get("api_key"),
                    # model=nl["model"],
                    # default_bbox=tuple(nl.get("default_bbox")) if nl.get("default_bbox") else None,
                    # default_timer=tuple(nl.get("default_timer")) if nl.get("default_timer") else None,
                )
                logger.info(
                    "OpenGeoData NL augmented query: %s bbox:%s timer:%s",
                    q,
                    bb,
                    tt,
                )
                logger.info(f"OpenGeoData will call discover() with: query='{q}', bbox={bb}, timer={tt}")
            except NLQueryError as nl_exc:
                logger.warning(f"OpenGeoData NL parsing failed: {nl_exc}. Falling back to direct query.")
                # nl may be None (call_llm-driven parse with no nl dict); guard so the fallback
                # degrades to the raw query instead of raising AttributeError.
                _nl = nl or {}
                q = _nl.get("user_query") or query or ""
                bb = valid_bbox(_nl.get("default_bbox") or bbox) if (_nl.get("default_bbox") or bbox) else None
                tt: Optional[Tuple[Optional[str], Optional[str]]] = None
                timer_to_use = _nl.get("default_timer") or timer
                if timer_to_use and len(timer_to_use) >= 2:
                    tt = (iso_date(timer_to_use[0]), iso_date(timer_to_use[1]))
        else:
            logger.info("OpenGeoData NL parsing not used; proceeding with direct query.")
            q = query or ""
            bb = valid_bbox(bbox) if bbox else None
            tt: Optional[Tuple[Optional[str], Optional[str]]] = None
            if timer and len(timer) >= 2:
                tt = (iso_date(timer[0]), iso_date(timer[1]))
        assets = discover(q, bb, tt, limit=limit, providers=providers)
        assets = sorted(assets, key=lambda a: -score(a, q.lower().split(), bb, tt))[:limit]
        result = {
            "query": q,
            "bbox": list(bb) if bb else None,
            "timer": [tt[0], tt[1]] if tt else [None, None],
            "count": len(assets),
            "assets": [asset_to_dict(asset) for asset in assets],
        }
        return result
    except NLQueryError as exc:
        raise OpenGeoDataError(str(exc))
    except Exception as exc:
        raise OpenGeoDataError(str(exc))

