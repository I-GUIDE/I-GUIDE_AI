"""Knowledge-element extractors for the I-GUIDE platform.

Given submitted assets, extract reusable knowledge and route it to three targets:
  - OpenSearch index   (searchable blocks/metadata for the agent's search peer)
  - MCP-tool manifests  (runnable functions/workflows, via one generic executor)
  - SKILL.md bundles    (the overall pipeline as agent guidance)

Two ingest sources (see ``ingest``):
  - GitHub URL    -> code + notebook extractors  (``ingest_from_github``)
  - Uploaded file -> dataset + publication extractors  (``ingest_uploaded_file``)

This package is SCAFFOLDING: interfaces, the unified manifest schema, fileclass
logic, and CLI/MCP/webhook entry points are present; extractor/emitter bodies are
stubs that raise ``NotImplementedError``. See ``EXTRACTOR_DESIGN.md`` for the full
contract and follow-on implementation order.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

__all__ = ["SCHEMA_VERSION"]
