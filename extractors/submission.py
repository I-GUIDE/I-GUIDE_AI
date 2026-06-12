"""Knowledge-element submission — the payload the webhook receives.

A user submits a knowledge element through the platform form; the platform assigns
an ``element_id`` and POSTs a submission to the ingestion webhook. The submission
carries the id, the element type, a source pointer (GitHub URL for code/notebook,
file/object for dataset/publication), and the form metadata ``fields`` (title,
authors, tags, contributor, abstract, …) which are inherited into the extracted
OpenSearch docs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .base import (
    KIND_CODE_BLOCK,
    KIND_DATASET,
    KIND_NOTEBOOK_BLOCK,
    KIND_PUBLICATION,
    VALID_TARGETS,
)

# Form element_type -> internal extractor kind.
ELEMENT_TYPES = {
    "notebook": KIND_NOTEBOOK_BLOCK,
    "code": KIND_CODE_BLOCK,
    "dataset": KIND_DATASET,
    "publication": KIND_PUBLICATION,
}
GITHUB_TYPES = {"notebook", "code"}
FILE_TYPES = {"dataset", "publication"}


@dataclass
class Submission:
    element_id: str
    element_type: str                         # notebook | code | dataset | publication
    # GitHub source (code/notebook)
    github_url: str = ""
    ref: str = ""
    notebook_file: str = ""                   # relative path for a notebook element
    # File source (dataset/publication)
    file_path: str = ""                       # local/temp path once materialized
    bucket: str = ""
    key: str = ""
    # Form metadata, inherited into the extracted docs (title, authors, tags, ...)
    fields: Dict[str, Any] = field(default_factory=dict)
    targets: List[str] = field(default_factory=lambda: list(VALID_TARGETS))

    def validate(self) -> None:
        if not self.element_id:
            raise ValueError("submission.element_id is required")
        if self.element_type not in ELEMENT_TYPES:
            raise ValueError(f"unknown element_type: {self.element_type}; valid: {sorted(ELEMENT_TYPES)}")
        if self.element_type in GITHUB_TYPES and not self.github_url:
            raise ValueError(f"{self.element_type} submission requires source.github_url")
        if self.element_type in FILE_TYPES and not (self.file_path or self.key):
            raise ValueError(f"{self.element_type} submission requires a source file (file_path or bucket/key)")

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Submission":
        """Parse the webhook JSON.

        Expected shape (tolerant to flat or nested ``source``):
          {
            "element_id": "...", "element_type": "notebook|code|dataset|publication",
            "source": {"github_url": "...", "ref": "...", "notebook_file": "...",
                       "file_path": "...", "bucket": "...", "key": "..."},
            "fields": {title, authors, tags, contributor, abstract, ...},
            "targets": ["opensearch","mcp","skill"]
          }
        """
        p = payload or {}
        src = p.get("source") or {}

        def pick(*names: str, default: str = "") -> str:
            for n in names:
                if src.get(n):
                    return str(src[n])
                if p.get(n):
                    return str(p[n])
            return default

        sub = cls(
            element_id=str(p.get("element_id") or p.get("id") or ""),
            element_type=str(p.get("element_type") or p.get("type") or "").strip().lower(),
            github_url=pick("github_url", "url", "repo_url"),
            ref=pick("ref", "branch"),
            notebook_file=pick("notebook_file", "notebook-file", "path"),
            file_path=pick("file_path", "path"),
            bucket=pick("bucket"),
            key=pick("key", "object_key"),
            fields=dict(p.get("fields") or {}),
            targets=list(p.get("targets") or VALID_TARGETS),
        )
        return sub


__all__ = ["Submission", "ELEMENT_TYPES", "GITHUB_TYPES", "FILE_TYPES"]
