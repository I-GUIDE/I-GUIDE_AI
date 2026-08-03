"""Deterministic self-description: answer "what tools do you have" from the live registries.

In the supervisor architecture no LLM sees the full tool registry (the decider is a bare
router, the search peer's prose is discarded, and the synthesizer is tool-free and
grounded-only), so a meta question like "what tools do you have" either retrieved irrelevant
KB documents about "tools" or hit the no-grounding reply. This module detects capability/meta
questions at triage and composes the answer directly from the actual tool factories — so it is
always truthful for the running deployment and the request's configuration (search-method
allowlist, code-exec flag, skills, MCP modules).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Meta/self-descriptive phrasings only. Deliberately requires the question to be about the
# ASSISTANT (you/your, or a bare anchored "list/show tools") so domain questions like
# "what tools are available for flood mapping" still go through normal retrieval.
_CAPABILITY_RE = re.compile(
    r"(?:\b(?:what|which)\s+tools?\s+(?:do\s+you|can\s+you|are\s+you|you)\b"
    r"|\byour\s+(?:tools?|capabilities|skills?)\b"
    r"|^\s*what\s+can\s+you\s+do\b"
    r"|^\s*what\s+are\s+you\s+able\s+to\s+do\b"
    r"|\bwhat\s+(?:skills?|capabilities)\s+do\s+you\s+have\b"
    r"|^\s*(?:list|show)(?:\s+me)?\s+(?:your\s+)?(?:available\s+)?(?:tools?|capabilities|skills?)\s*\??\s*$)",
    re.IGNORECASE,
)

# Grouping of the granular toolset by capability, for a readable answer.
_SEARCH_TOOLS = {
    "keyword_search", "semantic_search", "neo4j_search", "neo4j_get_element_by_id",
    "neo4j_explore_related_nodes", "spatial_search", "agent_kb_search", "get_kb_block",
}
_EXTERNAL_TOOLS = {"opengeodata_search"}
_GEOCODE_TOOLS = {"geocode_places"}


def is_capability_query(query: str) -> bool:
    """True when *query* asks about the assistant's own tools/capabilities/skills."""
    return bool(_CAPABILITY_RE.search(query or ""))


def _line(tool: Any) -> str:
    name = getattr(tool, "name", "") or "tool"
    desc = str(getattr(tool, "description", "") or "").strip()
    first = desc.split(". ")[0].rstrip(".")
    if len(first) > 140:
        first = first[:137] + "..."
    return f"- **{name}** — {first}." if first else f"- **{name}**"


def describe_capabilities(
    *,
    enabled_search_methods: Optional[List[str]] = None,
    include_mcp_tools: bool = False,
    mcp_modules: Optional[List[str]] = None,
    code_exec: Optional[bool] = None,
    skill_roots: Optional[List[str]] = None,
) -> str:
    """Compose a markdown self-description from the LIVE tool registries.

    Best-effort and never raises: each registry is probed independently, so a missing optional
    backend (e.g. QGIS not installed) simply drops out of the answer instead of breaking it.
    """
    groups: Dict[str, List[str]] = {"search": [], "external": [], "qgis": [], "files": [], "geocode": []}

    try:
        from agent_runtime.langchain_granular_tools import make_langchain_granular_tools

        for tool in make_langchain_granular_tools(enabled_search_methods, include_file_tools=True):
            name = getattr(tool, "name", "")
            if name in _SEARCH_TOOLS:
                groups["search"].append(_line(tool))
            elif name in _EXTERNAL_TOOLS:
                groups["external"].append(_line(tool))
            elif name.startswith(("qgis_", "pyqgis_")):
                groups["qgis"].append(_line(tool))
            else:
                groups["files"].append(_line(tool))
    except Exception:
        pass
    try:
        from agent_runtime.langchain_granular_tools import make_langchain_geocode_tools

        groups["geocode"].extend(_line(t) for t in make_langchain_geocode_tools())
    except Exception:
        pass

    exec_enabled = None
    try:
        from agent_runtime.code_execution import get_code_executor, is_code_exec_enabled

        exec_enabled = bool(code_exec) if code_exec is not None else is_code_exec_enabled()
        backend = getattr(get_code_executor(), "backend", "unknown") if exec_enabled else None
    except Exception:
        backend = None

    skills: List[str] = []
    try:
        from agent_runtime.skills import SkillRegistry

        skills = [str(s.get("name")) for s in SkillRegistry.discover(skill_roots).catalog() if s.get("name")]
    except Exception:
        pass

    parts: List[str] = ["Here is what I can do, grouped by capability:"]
    if groups["search"]:
        parts.append("\n**Knowledge-base search (I-GUIDE platform)**\n" + "\n".join(groups["search"]))
    if groups["external"]:
        parts.append("\n**External open-data discovery**\n" + "\n".join(groups["external"]))
    if groups["qgis"]:
        parts.append("\n**Geospatial analysis (QGIS, headless)**\n" + "\n".join(groups["qgis"]))
    parts.append(
        "\n**Geospatial file handling**\nUploaded vector data (shapefiles, GeoJSON, TIGER zips) can be "
        "inspected, plotted, reprojected, and exported (inspect_vector, plot_vector, "
        "reproject_vector, vector_to_geojson); extracted shapefile components are auto-discovered."
    )
    if groups["geocode"]:
        parts.append("\n**Geocoding**\n" + "\n".join(groups["geocode"]))
    if exec_enabled is not None:
        parts.append(
            "\n**Code execution**\n- **execute_code** — "
            + (f"enabled (sandboxed, backend: {backend}); runs Python with dependency installs, "
               "reads uploaded files, and returns plots/files as downloadable artifacts."
               if exec_enabled else "currently disabled on this deployment.")
        )
    if groups["files"]:
        parts.append("\n**Files & outputs**\n" + "\n".join(groups["files"]))
    if include_mcp_tools:
        mods = ", ".join(mcp_modules or ["spatial_analysis_tools"])
        parts.append(f"\n**MCP tools**\nAdditional analysis tools from MCP modules: {mods}.")
    if skills:
        parts.append("\n**Skills**\n" + "\n".join(f"- {s}" for s in skills))
    parts.append(
        "\nI also keep conversation memory across turns, look up knowledge elements by id, list "
        "contributor-specified related elements, rank elements by real usage (popularity), and "
        "cite every answer with links to its sources."
    )
    return "\n".join(parts)


__all__ = ["is_capability_query", "describe_capabilities"]
