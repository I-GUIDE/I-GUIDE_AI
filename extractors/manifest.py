"""The unified manifest: one record per ingest run.

A ``UnifiedManifest`` is the single artifact the harness produces before fanning
out to emitters. The per-asset ``runnable`` sub-block mirrors the shape of
``MCP_server/notebook_workflow_builder.py`` manifests so the existing
``generated_notebook_tools._run_generated_manifest`` can consume it unchanged.

See EXTRACTOR_DESIGN.md §1 for the field-level contract.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from . import SCHEMA_VERSION
from .base import AssetRecord, ExtractionResult, ProvenanceEdge, SkillSpec


@dataclass
class UnifiedManifest:
    element_id: str = ""                     # platform knowledge-element id (the anchor)
    element_type: str = ""                   # notebook | code | dataset | publication
    repo_id: str = ""
    source_url: str = ""
    commit_sha: str = ""
    cloned_at: str = ""                      # ISO-8601; stamped by the harness (not at import — no Date.now in scripts)
    schema_version: int = SCHEMA_VERSION
    extractors_run: List[str] = field(default_factory=list)
    assets: List[Dict[str, Any]] = field(default_factory=list)        # serialized AssetRecord dicts
    provenance_edges: List[Dict[str, Any]] = field(default_factory=list)
    skill: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)

    # ---- assembly --------------------------------------------------------
    def add_result(self, extractor_name: str, result: ExtractionResult) -> None:
        """Merge one extractor's ExtractionResult into this manifest."""
        if extractor_name not in self.extractors_run:
            self.extractors_run.append(extractor_name)
        self.assets.extend(asdict(a) for a in result.assets)
        self.provenance_edges.extend(asdict(e) for e in result.edges)
        self.warnings.extend(f"[{extractor_name}] {w}" for w in result.warnings)
        if result.skill is not None and self.skill is None:
            self.skill = asdict(result.skill)

    # ---- (de)serialization ----------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnifiedManifest":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    @classmethod
    def from_json(cls, text: str) -> "UnifiedManifest":
        return cls.from_dict(json.loads(text))


__all__ = ["UnifiedManifest"]
