"""Emitters: fan a UnifiedManifest out to the three storage targets.

  - opensearch_emitter : index searchable docs (+ embeddings, + provenance)
  - mcp_emitter        : write runnable manifests (consumed by the generic executor)
  - skill_emitter      : write SKILL.md bundles

Each exposes ``emit(manifest, ...) -> dict`` (a summary). Bodies are stubs in this
scaffolding branch.
"""

from __future__ import annotations

__all__ = ["opensearch_emitter", "mcp_emitter", "skill_emitter"]
