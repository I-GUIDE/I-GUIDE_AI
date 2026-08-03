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


def mcp_tools_enabled_default() -> bool:
    """The server's MCP default (AGENT_INCLUDE_MCP_TOOLS) when a request doesn't specify."""
    try:
        from agent_runtime.langchain_mcp_tools import mcp_tools_enabled

        return bool(mcp_tools_enabled())
    except Exception:
        return False


def is_capability_query(query: str) -> bool:
    """True when *query* asks about the assistant's own tools/capabilities/skills."""
    return bool(_CAPABILITY_RE.search(query or ""))


def _names(factory, **kwargs) -> set:
    """Tool names from a registry factory; empty set when the registry is unavailable."""
    try:
        return {str(getattr(t, "name", "")) for t in (factory(**kwargs) or [])}
    except Exception:
        return set()


def describe_capabilities(
    *,
    enabled_search_methods: Optional[List[str]] = None,
    include_mcp_tools: Optional[bool] = None,
    mcp_modules: Optional[List[str]] = None,
    code_exec: Optional[bool] = None,
    skill_roots: Optional[List[str]] = None,
) -> str:
    """Compose a capability-level self-description from the LIVE tool registries.

    Written for a user, not a developer: capabilities are described in plain language rather
    than by internal tool name. Probes the FULL registries (a per-request search-method
    allowlist or MCP flag is a client choice, not a limit on what the assistant can do), but
    stays honest about DEPLOYMENT-level gates — an optional backend that is genuinely absent
    (QGIS, MCP server, code sandbox) is reported as unavailable instead of promised.
    Best-effort per registry; never raises.
    """
    from agent_runtime.langchain_granular_tools import (
        make_langchain_geocode_tools,
        make_langchain_granular_tools,
    )

    # Probe unfiltered: what this deployment can actually do.
    granular = _names(make_langchain_granular_tools, enabled_search_methods=None,
                      include_file_tools=True)
    has_qgis = any(n.startswith(("qgis_", "pyqgis_")) for n in granular)
    has_geocode = bool(_names(make_langchain_geocode_tools))
    geo = set()
    try:
        from agent_runtime.langchain_geo_tools import make_langchain_geo_tools

        geo = _names(make_langchain_geo_tools, default_input_file_ids=None)
    except Exception:
        pass
    kbflows = set()
    try:
        from extractors.geo_handles import make_geo_analysis_tools

        kbflows = _names(make_geo_analysis_tools)
    except Exception:
        pass
    mcp = set()
    if mcp_tools_enabled_default() if include_mcp_tools is None else bool(include_mcp_tools):
        try:
            from agent_runtime.langchain_mcp_tools import make_langchain_mcp_tools

            mcp = _names(make_langchain_mcp_tools,
                         include_modules=mcp_modules or ["spatial_analysis_tools"])
        except Exception:
            pass
    exec_enabled = None
    try:
        from agent_runtime.code_execution import is_code_exec_enabled

        exec_enabled = bool(code_exec) if code_exec is not None else is_code_exec_enabled()
    except Exception:
        pass
    skills: List[str] = []
    try:
        from agent_runtime.skills import SkillRegistry

        skills = [str(sk.get("name")) for sk in SkillRegistry.discover(skill_roots).catalog()
                  if sk.get("name")]
    except Exception:
        pass

    parts: List[str] = ["Here is what I can help with:"]

    finding: List[str] = []
    if {"keyword_search", "semantic_search"} & granular:
        finding.append("search the I-GUIDE knowledge base by keyword and by meaning, so you "
                       "can find datasets, notebooks, publications, and OERs even when your "
                       "wording differs from theirs")
    if "neo4j_search" in granular:
        finding.append("follow the knowledge graph — find work by a given author, "
                       "organization, tag, or resource type, and rank elements by how much "
                       "they are actually used")
    if "spatial_search" in granular:
        finding.append("bias a search toward a place you mention")
    if {"neo4j_get_element_by_id", "neo4j_explore_related_nodes"} & granular:
        finding.append("look up a specific element by its id, explain it, and list the related "
                       "elements its contributor curated")
    if "opengeodata_search" in granular:
        finding.append("look beyond the platform for open geospatial data, searching public "
                       "catalogs such as NASA CMR, Data.gov, and Socrata")
    if {"agent_kb_search", "get_kb_block"} & granular:
        finding.append("dig into the code and methods extracted from ingested submissions when "
                       "you need implementation-level detail")
    if finding:
        parts.append("\n**Finding things**\nI can " + _join(finding) + ".")

    analysis: List[str] = []
    if geo:
        analysis.append("inspect an uploaded vector dataset (its coordinate system, extent, "
                        "geometry type, columns, and feature count), draw it as a map or a "
                        "choropleth, reproject it, convert it to GeoJSON, and spatially join "
                        "two datasets — shapefiles split across several files are reassembled "
                        "automatically, and zipped or TIGER/Line data is read directly")
    if kbflows:
        analysis.append("build hexbin heat maps and choropleths, and run spatial functions "
                        "extracted from knowledge-base notebooks on your data")
    if has_qgis:
        analysis.append("run QGIS itself, headless — metric buffers, any QGIS processing "
                        "algorithm, layer summaries, and rendered map images")
    if mcp:
        analysis.append(f"use {len(mcp)} additional spatial-analysis tools provided by the "
                        "connected MCP service")
    if has_geocode:
        analysis.append("turn place or institution names into coordinates, so named locations "
                        "can be mapped without you supplying latitudes and longitudes")
    if analysis:
        parts.append("\n**Working with geospatial data**\nI can " + _join(analysis) + ".")

    if exec_enabled:
        parts.append("\n**Computing and coding**\nI write Python and actually run it in a "
                     "sandbox — installing the libraries it needs, reading the files you "
                     "upload, and returning the plots, maps, and data files it produces as "
                     "downloads. I read the errors and revise until it works.")
    elif exec_enabled is False:
        parts.append("\n**Computing and coding**\nI can write and explain code, but running "
                     "it is disabled on this deployment, so I cannot execute it for you.")

    if not has_qgis:
        parts.append("\n_QGIS is not installed on this deployment, so QGIS-specific operations "
                     "run through the equivalent Python geospatial tools instead._")

    if skills:
        parts.append("\n**Task-specific workflows**\nI can load and follow these packaged "
                     "workflows: " + ", ".join(skills) + ".")

    parts.append("\n**How I answer**\nI remember the conversation, so you can refer back to "
                 "earlier results or files; I keep your uploads available across turns; I "
                 "ground answers in what I actually retrieved or computed and cite each source "
                 "as a link; and I tell you when the evidence does not support an answer "
                 "rather than guessing.")
    parts.append("\nJust describe what you are after — I pick the right tools for it.")
    return "\n".join(parts)


def _join(items: List[str]) -> str:
    """Join clauses into readable prose ('a, b, and c')."""
    if len(items) == 1:
        return items[0]
    return "; ".join(items[:-1]) + "; and " + items[-1]


__all__ = ["is_capability_query", "describe_capabilities"]
