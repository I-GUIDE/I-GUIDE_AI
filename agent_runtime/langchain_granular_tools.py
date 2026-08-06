from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .langchain_file_tools import make_langchain_file_tools
from rag_pipeline.search.opengeodata import get_opengeodata_results
from rag_pipeline.search.keyword import get_keyword_search_results
from rag_pipeline.search.utils import snippet_chars
from rag_pipeline.search.agents import (
    explore_neo4j_related_nodes,
    get_neo4j_agent_results,
    get_neo4j_element_by_id_results,
)
from rag_pipeline.search.semantic import semantic_search as run_semantic_search
from rag_pipeline.search.spatial import get_spatial_search_results
from rag_pipeline.search.agent_kb import agent_kb_search as run_agent_kb_search
from rag_pipeline.search.agent_kb import get_kb_block as run_get_kb_block
from rag_pipeline.qgis_headless_tools import (
    pyqgis_available,
    pyqgis_layer_summary_tool,
    pyqgis_render_map_tool,
    qgis_metric_buffer_tool,
    qgis_process_available,
    qgis_processing_help_tool,
    qgis_processing_run_tool,
)


def _safe_int(value: Any, default: int = 8, minimum: int = 1, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _landing_url(src: Dict[str, Any]) -> str:
    """External (e.g. OpenGeoData) hits carry their own landing URL; pull it from the
    source's ``url``/``landing_url`` or the first usable entry in ``links``. Internal
    knowledge elements have no url here — their link is built from element_type + doc_id."""
    url = src.get("url") or src.get("landing_url")
    if url:
        return str(url)
    links = src.get("links")
    if isinstance(links, dict):
        # OpenGeoData assets carry links as {label: url} (e.g. {"Digital Data": "...", "Original
        # Metadata": "..."}). Prefer a landing/data page over an XML metadata record, else the
        # first http(s) value.
        http_vals = [
            (str(k), str(v)) for k, v in links.items()
            if isinstance(v, str) and v.startswith(("http://", "https://"))
        ]
        if http_vals:
            non_meta = [v for k, v in http_vals if "metadata" not in k.lower()]
            return non_meta[0] if non_meta else http_vals[0][1]
    if isinstance(links, list):
        for ln in links:
            if isinstance(ln, dict) and (ln.get("url") or ln.get("href")):
                return str(ln.get("url") or ln.get("href"))
            if isinstance(ln, str) and ln:
                return ln
    return ""


def _normalize_hits(hits: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for hit in hits:
        doc = hit.get("_source") or {}
        doc_id = str(hit.get("_id") or doc.get("doc_id") or "")
        if not doc_id:
            continue
        from rag_pipeline.search.neo4j_graph_tools import is_public_visibility

        if not is_public_visibility(doc.get("visibility")):
            continue  # unlisted element -> never surfaced
        item: Dict[str, Any] = {
            "doc_id": doc_id,
            "source": source,
            "score": hit.get("_score", 0.0),
            "title": doc.get("title") or "Untitled",
            "element_type": doc.get("element_type") or doc.get("resource-type") or "resource",
            "contents": (doc.get("contents") or "")[:snippet_chars()],
            "url": _landing_url(doc),  # external landing url (OpenGeoData); "" for internal
        }
        # External (OpenGeoData) descriptions are user-facing: keep the FULL abstract alongside
        # the (capped) contents so the structured results a client renders are never cut short.
        full_text = str(doc.get("contents") or "")
        if full_text and str(item["element_type"]).lower() == "opengeodata":
            item["abstract"] = full_text
        # Preserve the structured geospatial metadata carried by external (OpenGeoData) hits so the
        # final response can surface them as rich JSON objects (map bbox, provider, license, ...).
        # These keys are absent on internal KB hits, so this is a no-op there.
        for field in ("bbox", "datetime", "provider", "license", "links", "keywords", "source_system"):
            value = doc.get(field)
            if value is not None:
                item[field] = value
        normalized.append(item)
    return normalized


def _build_payload(hits: List[Dict[str, Any]], source: str) -> str:
    docs = _normalize_hits(hits, source=source)
    payload = {
        "source": source,
        "count": len(docs),
        "documents": docs,
        "citation_ids": [doc["doc_id"] for doc in docs],
    }
    return json.dumps(payload, ensure_ascii=True, default=str)


def keyword_search_tool(query: str, limit: int = 8) -> str:
    hits = get_keyword_search_results(query, size=_safe_int(limit))
    return _build_payload(hits, source="keyword")


def semantic_search_tool(query: str, limit: int = 8) -> str:
    hits = run_semantic_search(query, size=_safe_int(limit))
    return _build_payload(hits, source="semantic")


def neo4j_search_tool(query: str, limit: int = 8) -> str:
    hits = get_neo4j_agent_results(query, limit=_safe_int(limit))
    return _build_payload(hits, source="neo4j")


def neo4j_get_element_by_id_tool(element_id: str) -> str:
    hits = get_neo4j_element_by_id_results(element_id)
    return _build_payload(hits, source="neo4j")


def neo4j_explore_related_nodes_tool(element_id: str, depth: int = 2, limit: int = 50) -> str:
    payload = explore_neo4j_related_nodes(
        element_id,
        depth=_safe_int(depth, default=2, maximum=3),
        limit=_safe_int(limit, default=50),
    )
    return json.dumps(payload, ensure_ascii=True, default=str)


def spatial_search_tool(query: str, limit: int = 8) -> str:
    hits = get_spatial_search_results(query, size=_safe_int(limit))
    return _build_payload(hits, source="spatial")


def agent_kb_search_tool(query: str, limit: int = 8) -> str:
    """Search the agent knowledge base (extracted blocks/method-specs from ingested submissions)."""
    payload = run_agent_kb_search(query, size=_safe_int(limit))
    return json.dumps(payload, ensure_ascii=True, default=str)


def get_kb_block_tool(doc_id: str) -> str:
    """Fetch the FULL agent-KB block by doc_id (complete code/method body for verbatim reuse)."""
    return json.dumps(run_get_kb_block(doc_id), ensure_ascii=True, default=str)


def opengeodata_search_tool(query: str, limit: int = 8, session_context_json: Optional[str] = None) -> str:
    session_ctx: Optional[Mapping[str, Any]] = None
    if session_context_json:
        try:
            parsed = json.loads(session_context_json)
            if isinstance(parsed, dict):
                session_ctx = parsed
        except json.JSONDecodeError:
            session_ctx = None
    hits = get_opengeodata_results(query, limit=_safe_int(limit), session_ctx=session_ctx)
    return _build_payload(hits, source="opengeodata")


_GEOCODE_MAX_PLACES = 40  # Nominatim is ~1 req/s (cached per process); bound a tool call's runtime


def geocode_places_tool(places: Any) -> str:
    """Geocode place/institution names to coordinates via Nominatim (agent-side network).

    Accepts a JSON list or a comma/newline-separated string of names. Returns JSON:
    ``{"results": [{"place", "found", "lat", "lon", "bbox"}], "not_found": [...], "count"}``
    where lat/lon is the center of the geocoded bounding box. Names that cannot be geocoded
    (organizations without a location, typos, "null") come back found=false — drop them.
    """
    # The code-exec sandbox has NO network, so geocoding must happen here, in the agent
    # process (which reuses the cached, rate-limited Nominatim helper from opengeodata).
    from rag_pipeline.search.opengeodata_new import geocode_place

    if isinstance(places, str):
        try:
            parsed = json.loads(places)
            names = parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            names = None
        if names is None:
            names = [p.strip() for chunk in places.split("\n") for p in chunk.split(",")]
    elif isinstance(places, (list, tuple)):
        names = list(places)
    else:
        names = [places]
    names = [str(n).strip() for n in names if str(n or "").strip()]
    dropped = max(0, len(names) - _GEOCODE_MAX_PLACES)
    names = names[:_GEOCODE_MAX_PLACES]

    results: List[Dict[str, Any]] = []
    not_found: List[str] = []
    for name in names:
        try:
            bbox = geocode_place(name)
        except Exception:
            bbox = None
        if bbox:
            minlon, minlat, maxlon, maxlat = bbox
            results.append({"place": name, "found": True,
                            "lat": round((minlat + maxlat) / 2.0, 6),
                            "lon": round((minlon + maxlon) / 2.0, 6),
                            "bbox": [minlon, minlat, maxlon, maxlat]})
        else:
            results.append({"place": name, "found": False})
            not_found.append(name)
    payload: Dict[str, Any] = {"results": results, "not_found": not_found,
                               "count": sum(1 for r in results if r["found"])}
    if dropped:
        payload["note"] = f"input truncated: only the first {_GEOCODE_MAX_PLACES} names geocoded ({dropped} dropped)"
    return json.dumps(payload, ensure_ascii=True)


def make_langchain_geocode_tools() -> List[Any]:
    """The geocoding tool for peers that plot named places (code/analysis)."""
    try:
        from langchain_core.tools import StructuredTool
    except Exception:  # pragma: no cover - optional dependency
        return []
    return [
        StructuredTool.from_function(
            func=geocode_places_tool,
            name="geocode_places",
            description=(
                "Geocode place or institution names to coordinates (Nominatim). Input: a JSON "
                "list (or comma-separated string) of names; returns per-name lat/lon (bbox "
                "center) with found=false for names that aren't real places. USE THIS to get "
                "coordinates for maps (e.g. a bubble map from a CSV of institutions) and pass "
                "them into execute_code as literal data — sandboxed code has NO network and "
                "cannot geocode itself. Never ask the user for coordinates. "
                f"Max {_GEOCODE_MAX_PLACES} names per call (~1s per uncached name)."
            ),
            metadata={"category": "geospatial"},
        )
    ]


def make_langchain_qgis_tools(*, session_id: Optional[str] = None) -> List[Any]:
    # QGIS is not installed in the default agent image (only GDAL, for the geopandas-backed
    # geo tools). Expose each QGIS tool only when its backend is actually present, so the agent
    # falls back to the working `plot_vector`/`inspect_vector` geo tools instead of attempting
    # QGIS calls that fail at runtime. The processing/buffer tools need the `qgis_process` CLI;
    # render_map / layer_summary need the PyQGIS Python module — a deployment may have one and
    # not the other. Forceable via AGENT_QGIS_ENABLED. See qgis_headless_tools.qgis_available().
    have_cli = qgis_process_available()
    have_pyqgis = pyqgis_available()
    if not (have_cli or have_pyqgis):
        return []
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    def qgis_processing_run(algorithm: str, parameters_json: str, timeout_sec: int = 300) -> str:
        return qgis_processing_run_tool(
            algorithm=algorithm,
            parameters_json=parameters_json,
            session_id=session_id,
            timeout_sec=timeout_sec,
        )

    def qgis_metric_buffer(
        input_layer: str,
        distance_meters: float,
        output_filename: str = "buffer.geojson",
        projected_crs: str = "EPSG:26916",
        target_crs: str = "EPSG:4326",
        dissolve: bool = False,
        segments: int = 12,
        timeout_sec: int = 300,
    ) -> str:
        return qgis_metric_buffer_tool(
            input_layer=input_layer,
            distance_meters=distance_meters,
            output_filename=output_filename,
            projected_crs=projected_crs,
            target_crs=target_crs,
            dissolve=dissolve,
            segments=segments,
            session_id=session_id,
            timeout_sec=timeout_sec,
        )

    def pyqgis_layer_summary(
        layer_path: str,
        provider: str = "ogr",
        layer_name: Optional[str] = None,
        sample_limit: int = 5,
        timeout_sec: int = 180,
    ) -> str:
        return pyqgis_layer_summary_tool(
            layer_path=layer_path,
            provider=provider,
            layer_name=layer_name,
            sample_limit=sample_limit,
            session_id=session_id,
            timeout_sec=timeout_sec,
        )

    def pyqgis_render_map(
        layers_json: str,
        output_filename: str = "map.png",
        width: int = 1200,
        height: int = 800,
        extent_json: Optional[str] = None,
        basemap: str = "none",
        basemap_url: Optional[str] = None,
        crs: Optional[str] = None,
        timeout_sec: int = 180,
    ) -> str:
        return pyqgis_render_map_tool(
            layers_json=layers_json,
            output_filename=output_filename,
            width=width,
            height=height,
            extent_json=extent_json,
            basemap=basemap,
            basemap_url=basemap_url,
            crs=crs,
            session_id=session_id,
            timeout_sec=timeout_sec,
        )

    tools: List[Any] = []
    if have_cli:  # qgis_process CLI: processing + metric buffer
        tools += [
            StructuredTool.from_function(
                func=qgis_processing_help_tool,
                name="qgis_processing_help",
                description=(
                    "Inspect a QGIS Processing algorithm's JSON help by id, such as native:buffer. "
                    "Use before qgis_processing_run when parameter names are uncertain."
                ),
                metadata={"category": "spatial_analysis"},
            ),
            StructuredTool.from_function(
                func=qgis_processing_run,
                name="qgis_processing_run",
                description=(
                    "Run one QGIS Processing algorithm headlessly in an isolated per-session job directory. "
                    "parameters_json must be a JSON object using QGIS parameter names; relative output paths "
                    "on OUTPUT-style parameters are written under the job directory. Returns JSON with job_dir, "
                    "effective_parameters, stdout_json, stdout, and stderr. For meter-based buffers on GeoJSON "
                    "or EPSG:4326 layers, prefer qgis_metric_buffer instead of native:buffer."
                ),
                metadata={"category": "spatial_analysis"},
            ),
            StructuredTool.from_function(
                func=qgis_metric_buffer,
                name="qgis_metric_buffer",
                description=(
                    "Create a meter-based buffer safely with QGIS. This resolves uploaded file ids, reprojects "
                    "the input layer to a projected CRS, runs native:buffer using distance_meters, then reprojects "
                    "the output to target_crs. Use this for requests like 'buffer by 500 meters', especially when "
                    "the input is GeoJSON/EPSG:4326. Returns output_path and managed_output with file_id/download_url."
                ),
                metadata={"category": "spatial_analysis"},
            ),
        ]
    if have_pyqgis:  # standalone PyQGIS: layer summary + map rendering
        tools += [
            StructuredTool.from_function(
                func=pyqgis_layer_summary,
                name="pyqgis_layer_summary",
                description=(
                    "Inspect one vector or raster layer with standalone headless PyQGIS. "
                    "Returns JSON with CRS, extent, fields, feature count, and sample features for vector layers."
                ),
                metadata={"category": "spatial_analysis"},
            ),
            StructuredTool.from_function(
                func=pyqgis_render_map,
                name="pyqgis_render_map",
                description=(
                    "Render vector/raster layer paths to a PNG using standalone headless PyQGIS in an isolated "
                    "per-session job directory. layers_json may contain uploaded file_id strings or objects with "
                    "path/layer_path, optional name, and provider ('ogr' for vector or 'gdal' for raster). Set "
                    "basemap='osm' to draw an OpenStreetMap XYZ background under the data. Returns managed_output "
                    "with file_id and download_url when rendering succeeds."
                ),
                metadata={"category": "spatial_analysis"},
            ),
        ]
    return tools


def make_langchain_granular_tools(
    enabled_search_methods: Optional[Sequence[str]] = None,
    *,
    include_file_tools: bool = True,
    session_id: Optional[str] = None,
) -> List[Any]:
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
        ) from exc

    retrieval_tools = [
        StructuredTool.from_function(
            func=keyword_search_tool,
            name="keyword_search",
            description="Keyword/BM25 search of the I-GUIDE knowledge base. USE FOR: exact terms, names, acronyms, titles, IDs, or rare jargon the user typed verbatim. Complements semantic_search (which misses exact tokens) — for a topical query call BOTH. Returns JSON with doc_ids and snippets.",
            metadata={"category": "retrieval_internal"},
        ),
        StructuredTool.from_function(
            func=semantic_search_tool,
            name="semantic_search",
            description="Meaning-based (vector) search of the I-GUIDE knowledge base. USE FOR: concepts, paraphrases, and 'about X' questions where the user's wording differs from the documents'. Complements keyword_search (which misses paraphrases) — for a topical query call BOTH. Returns JSON with doc_ids and snippets.",
            metadata={"category": "retrieval_internal"},
        ),
        StructuredTool.from_function(
            func=neo4j_search_tool,
            name="neo4j_search",
            description="Knowledge-GRAPH search. USE FOR: questions keyed on relationships or metadata rather than text — work BY an author or organization, items with a given tag, a specific resource type, members of a collection, and POPULARITY ('most popular/viewed/clicked', 'trending', ranked by real usage counts). Prefer it over semantic_search for those; do not use it for free-text topical questions. Returns JSON with doc_ids and snippets.",
            metadata={"category": "retrieval_internal"},
        ),
        StructuredTool.from_function(
            func=neo4j_get_element_by_id_tool,
            name="neo4j_get_element_by_id",
            description="Fetch one public I-GUIDE knowledge element by exact Neo4j id. Use when the user provides an element id. Returns JSON with doc_ids and snippets.",
            metadata={"category": "retrieval_internal"},
        ),
        StructuredTool.from_function(
            func=neo4j_explore_related_nodes_tool,
            name="neo4j_explore_related_nodes",
            description="Explore public RELATED nodes from an exact I-GUIDE knowledge element id. Returns JSON with seed, related documents, edges, and citation_ids.",
            metadata={"category": "retrieval_internal"},
        ),
        StructuredTool.from_function(
            func=spatial_search_tool,
            name="spatial_search",
            description="Place-aware search of the I-GUIDE knowledge base: infers the location in the query and biases results to it. USE WHENEVER the request names a place (city, state, county, river, basin, region, country) — e.g. 'flood data for Illinois', 'wildfires in California'. Call it IN ADDITION to keyword/semantic search, not instead. Returns JSON with doc_ids and snippets.",
            metadata={"category": "retrieval_internal"},
        ),
        StructuredTool.from_function(
            func=opengeodata_search_tool,
            name="opengeodata_search",
            description=(
                "Federated search of EXTERNAL open-data catalogs (NASA CMR, Data.gov, Socrata) — data "
                "that is NOT in the I-GUIDE knowledge base. USE FOR: satellite/remote-sensing imagery, "
                "climate and weather, elevation/DEM/lidar, land cover, census and other government open "
                "data, or any request for 'open', 'public' or 'external' data. Call it IN ADDITION to "
                "the internal searches so the user sees both; do NOT use it to answer questions about "
                "existing I-GUIDE elements. Optional session_context_json supplies bbox/time/provider "
                "hints. Returns JSON with doc_ids and snippets."
            ),
            metadata={"category": "retrieval_external"},
        ),
        StructuredTool.from_function(
            func=agent_kb_search_tool,
            name="agent_kb_search",
            description=(
                "Search the agent knowledge base: fine-grained, runnable-aware evidence extracted from "
                "ingested submissions (notebook code blocks, code-asset API surfaces, dataset metadata, "
                "publication method-specs). Each hit is linked to its original knowledge element "
                "(citation_ids = the source element ids) and may carry a runnable workflow tool. "
                "Use for implementation-level grounding and to find runnable workflows."
            ),
            metadata={"category": "retrieval_internal"},
        ),
        StructuredTool.from_function(
            func=get_kb_block_tool,
            name="get_kb_block",
            description=(
                "Fetch the FULL agent-KB block by its doc_id (returned by agent_kb_search). "
                "Use to read a block's complete code / method body for verbatim reuse, since "
                "search results are truncated."
            ),
            metadata={"category": "retrieval_internal"},
        ),
    ]
    if enabled_search_methods is not None:
        enabled = {str(name).strip() for name in enabled_search_methods if str(name).strip()}
        neo4j_companion_tools = {"neo4j_get_element_by_id", "neo4j_explore_related_nodes"}
        retrieval_tools = [
            tool for tool in retrieval_tools
            if getattr(tool, "name", "") in enabled
            or ("neo4j_search" in enabled and getattr(tool, "name", "") in neo4j_companion_tools)
        ]

    tools = [*retrieval_tools, *make_langchain_qgis_tools(session_id=session_id)]
    if include_file_tools:
        tools.extend(make_langchain_file_tools())
    return tools


__all__ = [
    "keyword_search_tool",
    "semantic_search_tool",
    "neo4j_search_tool",
    "neo4j_get_element_by_id_tool",
    "neo4j_explore_related_nodes_tool",
    "spatial_search_tool",
    "opengeodata_search_tool",
    "pyqgis_layer_summary_tool",
    "pyqgis_render_map_tool",
    "qgis_metric_buffer_tool",
    "qgis_processing_help_tool",
    "qgis_processing_run_tool",
    "geocode_places_tool",
    "make_langchain_geocode_tools",
    "make_langchain_qgis_tools",
    "make_langchain_granular_tools",
]
