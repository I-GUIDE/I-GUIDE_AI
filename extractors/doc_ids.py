"""Stable, deterministic id derivation + the workflow_id <-> MCP tool-name bridge.

All ids are content-independent (position/path/name based) so re-ingesting a
repo upserts the same OpenSearch doc / manifest instead of duplicating. The
``mcp_tool_name_for`` function is the single source of truth for the find<->run
linkage: the extractor stores it in ``runnable.runnable_tool`` and the MCP
executor must register the workflow under the same name. See EXTRACTOR_DESIGN.md §4.
"""

from __future__ import annotations

import hashlib
import re

from .base import (
    KIND_CODE_BLOCK,
    KIND_DATASET,
    KIND_NOTEBOOK_BLOCK,
    KIND_PUBLICATION,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# kind -> OpenSearch resource-type (drives element_type synthesis + spatial filter)
_RESOURCE_TYPE = {
    KIND_NOTEBOOK_BLOCK: "NotebookBlock",
    KIND_CODE_BLOCK: "CodeAsset",
    KIND_DATASET: "Dataset",
    KIND_PUBLICATION: "PublicationMethodSpec",
}


def _sha1(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def normalize_repo_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    u = re.sub(r"\.git$", "", u)
    return u.lower()


def repo_id(url: str) -> str:
    return f"repo_{_sha1(normalize_repo_url(url))}"


def resource_type_for(kind: str) -> str:
    return _RESOURCE_TYPE.get(kind, "Resource")


# ---- deterministic doc ids (see EXTRACTOR_DESIGN.md §2) -------------------
def notebook_block_doc_id(notebook_doc_id: str, order: int) -> str:
    return f"{notebook_doc_id}::block::{order}"


def code_asset_doc_id(repo_doc_id: str, rel_path: str, qualified_name: str) -> str:
    return f"{repo_doc_id}::code::{rel_path}::{qualified_name}"


def dataset_doc_id(element_id_or_filename: str) -> str:
    # Datasets are first-class platform elements; prefer the platform id when known.
    return str(element_id_or_filename)


def publication_methodspec_doc_id(publication_doc_id: str, n: int = 0) -> str:
    return f"{publication_doc_id}::methodspec" + (f"::{n}" if n else "")


def parent_doc_id(doc_id: str) -> str:
    """Recover the parent element id from a derived block/code/methodspec id."""
    return doc_id.split("::", 1)[0] if "::" in doc_id else doc_id


# ---- find<->run bridge ---------------------------------------------------
def workflow_id_for(asset_id: str, *, code: bool = False) -> str:
    prefix = "cwf" if code else "nbwf"
    return f"{prefix}_{_sha1(asset_id, 16)}"


def mcp_tool_name_for(workflow_id: str) -> str:
    """Tool name the generic executor exposes for a promoted workflow.

    MUST match what the agent sees (langchain_mcp_tools prefixes remote tools
    with ``mcp_``). The executor registers ``run_<workflow_id>``; the agent-side
    name is therefore ``mcp_run_<workflow_id>``.
    """
    return f"mcp_run_{workflow_id}"


def slugify(text: str, *, max_len: int = 64) -> str:
    """Lowercase-hyphen slug matching skills.py name regex ^[a-z0-9][a-z0-9-]{0,63}$."""
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    if not s:
        s = "skill"
    if not s[0].isalnum():
        s = f"s{s}"
    return s[:max_len].rstrip("-")


__all__ = [
    "normalize_repo_url", "repo_id", "resource_type_for",
    "notebook_block_doc_id", "code_asset_doc_id", "dataset_doc_id",
    "publication_methodspec_doc_id", "parent_doc_id",
    "workflow_id_for", "mcp_tool_name_for", "slugify",
]
