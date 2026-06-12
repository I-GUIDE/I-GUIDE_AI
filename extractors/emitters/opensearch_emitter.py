"""OpenSearch emitter — index extracted assets into AGENT-ONLY indices.

For each asset with target ``opensearch``: build a doc reusing the platform
``_source`` fields (doc_id, title, contents, resource-type + inherited
``source_fields`` = tags/authors/contributor/abstract) + an additive ``extracted``
object (block/runnable/file_io/provenance); index into the agent-only index for its
resource-type via ``indices.index_for`` (separate from the general ``OPENSEARCH_INDEX``
so platform search can't see it). Docs are indexed FIRST, embeddings second, so a
down embedder never loses docs.

``build_docs`` is pure/testable (no I/O). ``emit`` does the I/O and accepts an
injected ``client`` (for tests) or a ``dry_run`` flag. The agent's search peer must
query ``indices.all_agent_indices()`` to retrieve these (search-side follow-on).

Embedding text is prose-first (markdown context + title + tools + imports), NOT raw
code, so the shared kNN field retrieves well against natural-language queries.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from ..indices import index_for
from ..manifest import UnifiedManifest

DocTuple = Tuple[str, str, Dict[str, Any]]  # (index, doc_id, _source)


# --------------------------------------------------------------------------- #
# Pure doc construction (testable without a cluster)
# --------------------------------------------------------------------------- #
def _embed_text(asset: Dict[str, Any]) -> str:
    """Prose-first text to embed (avoid embedding raw code)."""
    block = asset.get("block") or {}
    parts: List[str] = [asset.get("title") or ""]
    if block:
        parts.append(block.get("markdown_context") or "")
        parts.append(" ".join(block.get("resolved_tools") or []))
        parts.append(" ".join(block.get("imports") or []))
    else:
        parts.append(asset.get("contents") or "")
    text = " ".join(p for p in parts if p).strip()
    return text or (asset.get("contents") or asset.get("title") or "")


def _build_source(asset: Dict[str, Any], edges: List[Dict[str, Any]]) -> Dict[str, Any]:
    doc_id = asset["doc_id"]
    src: Dict[str, Any] = {
        "doc_id": doc_id,
        "title": asset.get("title") or "",
        "contents": asset.get("contents") or "",
        "resource-type": asset.get("resource_type"),
        "element_type": asset.get("resource_type"),
    }
    # inherited platform form fields (tags/authors/contributor/abstract/...) at top level
    src.update(asset.get("source_fields") or {})
    # spatial geo_shape, if present
    spatial = asset.get("spatial") or {}
    if spatial.get("spatial-bounding-box-geojson"):
        src["spatial-bounding-box-geojson"] = spatial["spatial-bounding-box-geojson"]
    # agent-specific structured payload (stored, not the general schema)
    src["extracted"] = {
        **(asset.get("extracted") or {}),
        "kind": asset.get("kind"),
        "source_rel_path": asset.get("source_rel_path"),
        "block": asset.get("block"),
        "runnable": asset.get("runnable"),
        "spatial": spatial or None,
        "embed_text": _embed_text(asset),
    }
    related = [e for e in edges if e.get("src") == doc_id or e.get("dst") == doc_id]
    if related:
        src["extracted"]["provenance"] = related
    return src


def build_docs(manifest: UnifiedManifest) -> List[DocTuple]:
    """Pure: turn a manifest into (index, doc_id, _source) tuples for OpenSearch."""
    d = manifest.to_dict() if isinstance(manifest, UnifiedManifest) else dict(manifest)
    edges = d.get("provenance_edges") or []
    docs: List[DocTuple] = []
    for asset in d.get("assets") or []:
        if "opensearch" not in (asset.get("emit_targets") or []):
            continue
        index = index_for(asset.get("resource_type"))
        docs.append((index, asset["doc_id"], _build_source(asset, edges)))
    return docs


# --------------------------------------------------------------------------- #
# I/O (live cluster)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _os_client():
    from opensearchpy import OpenSearch
    node = os.getenv("OPENSEARCH_NODE", "")
    user = os.getenv("OPENSEARCH_USERNAME", "")
    pwd = os.getenv("OPENSEARCH_PASSWORD", "")
    return OpenSearch(
        hosts=[node],
        http_auth=(user, pwd) if (user or pwd) else None,
        use_ssl=node.lower().startswith("https"),
        verify_certs=False, ssl_assert_hostname=False, ssl_show_warn=False,
        timeout=30, max_retries=2, retry_on_timeout=True,
    )


def _embedding_url() -> str:
    url = os.getenv("FLASK_EMBEDDING_URL", "http://127.0.0.1:5000")
    return url if url.rstrip("/").endswith("get_embedding") else url.rstrip("/") + "/get_embedding"


def _embed_dim() -> int:
    return int(os.getenv("AGENT_KB_EMBED_DIM", "384"))  # all-MiniLM-L6-v2


def ensure_index(client, index: str) -> None:
    """Create the agent index with a kNN mapping for contents-embedding if missing."""
    if client.indices.exists(index=index):
        return
    body = {
        "settings": {"index": {"knn": True}},
        "mappings": {"properties": {
            "doc_id": {"type": "keyword"},
            "resource-type": {"type": "keyword"},
            "element_type": {"type": "keyword"},
            "title": {"type": "text"},
            "contents": {"type": "text"},
            "spatial-bounding-box-geojson": {"type": "geo_shape"},
            "extracted": {"type": "object", "enabled": True},
            "contents-embedding": {"type": "knn_vector", "dimension": _embed_dim()},
        }},
    }
    client.indices.create(index=index, body=body)


def _get_embedding(text: str) -> Optional[List[float]]:
    import requests
    try:
        r = requests.post(_embedding_url(), json={"text": text}, timeout=30)
        r.raise_for_status()
        return r.json().get("embedding")
    except Exception:
        return None


def emit(manifest: UnifiedManifest, *, client=None, embed: bool = True,
         dry_run: bool = False) -> Dict[str, Any]:
    """Index extracted assets into the agent KB. Backend defaults to LOCAL
    (file-backed) — nothing reaches the real OpenSearch unless AGENT_KB_BACKEND=
    opensearch (or a client is injected). Returns a summary."""
    from .. import kb_store

    docs = build_docs(manifest)
    by_index: Dict[str, int] = {}
    for index, _id, _src in docs:
        by_index[index] = by_index.get(index, 0) + 1

    if dry_run:
        return {"dry_run": True, "backend": "dry_run", "doc_count": len(docs),
                "indices": by_index, "doc_ids": [d[1] for d in docs]}

    # LOCAL backend (default): write to the file-backed store, never the real DB.
    if client is None and kb_store.kb_backend() != "opensearch":
        for index, doc_id, source in docs:
            kb_store.local_upsert(index, doc_id, source)
        return {"dry_run": False, "backend": "local", "doc_count": len(docs),
                "indices": by_index, "indexed": len(docs), "embedded": 0,
                "store_dir": str(kb_store.store_dir())}

    # OpenSearch backend (explicit opt-in / injected client).
    client = client or _os_client()
    # 1) index docs first (so a down embedder never loses docs)
    for index in by_index:
        ensure_index(client, index)
    indexed = 0
    for index, doc_id, source in docs:
        client.index(index=index, id=doc_id, body=source)
        indexed += 1
    # 2) embed second
    embedded = 0
    if embed:
        for index, doc_id, source in docs:
            vec = _get_embedding(source["extracted"].get("embed_text") or source.get("contents") or "")
            if vec is None:
                continue
            client.update(index=index, id=doc_id, body={"doc": {"contents-embedding": vec}})
            embedded += 1
    return {"dry_run": False, "backend": "opensearch", "doc_count": len(docs),
            "indices": by_index, "indexed": indexed, "embedded": embedded}


__all__ = ["build_docs", "emit", "ensure_index"]
