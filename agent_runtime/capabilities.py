"""Self-description: answer "what tools do you have" from the LIVE tool registries.

In the supervisor architecture no LLM sees the full tool registry (the decider is a bare
router, the search peer's prose is discarded, and the synthesizer is tool-free and
grounded-only), so a meta question like "what tools do you have" retrieved irrelevant KB
documents or hit the no-grounding reply.

This module answers those questions WITHOUT hardcoded capability prose:

1. ``collect_capability_inventory`` reads the actual tool factories — every tool's real name and
   description, plus deployment flags (code execution, skills, MCP) — so a tool added, removed,
   or gated anywhere in the codebase changes this answer with no edit here.
2. ``describe_capabilities`` hands that inventory to the LLM (``CAPABILITY_SUMMARY_PROMPT``),
   which answers the user's ACTUAL question over it.

``describe_capabilities`` is a ReAct loop, not a one-shot composer: the agent introspects its
own surface by topic (``list_my_capabilities``), can look again with a different word, and can
call the real listing tools to name actual models. A fixed prompt could only ever answer the
question it was written for — the previous version received no query at all and returned the
same grouped catalogue however specific the question was.

The only non-LLM path is a mechanical fallback that lists the inventory verbatim when no model
is reachable — still derived from the registries, nothing to maintain by hand.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Meta/self-descriptive phrasings only. Deliberately requires the question to be about the
# ASSISTANT (you/your, or a bare anchored "list/show tools") so domain questions like
# "what tools are available for flood mapping" still go through normal retrieval.
_CAPABILITY_RE = re.compile(
    r"(?:\b(?:what|which)\s+tools?\s+(?:do\s+you|can\s+you|are\s+you|you)\b"
    r"|\byour\s+(?:tools?|capabilities|skills?)\b"
    r"|^\s*what\s+can\s+you\s+do\b"
    r"|^\s*what\s+are\s+you\s+able\s+to\s+do\b"
    r"|\bwhat\s+(?:kind\s+of\s+)?(?:tools?|skills?|capabilities)\s+do\s+you\s+have\b"
    r"|^\s*(?:list|show)(?:\s+me)?\s+(?:your\s+)?(?:available\s+)?(?:tools?|capabilities|skills?)\s*\??\s*$)",
    re.IGNORECASE,
)


def is_capability_query(query: str) -> bool:
    """True when *query* asks about the assistant's own tools/capabilities/skills."""
    return bool(_CAPABILITY_RE.search(query or ""))


def _tool_entries(factory, **kwargs) -> List[Dict[str, str]]:
    """``[{name, description}]`` from a registry factory; [] when it is unavailable."""
    out: List[Dict[str, str]] = []
    try:
        for tool in (factory(**kwargs) or []):
            name = str(getattr(tool, "name", "") or "").strip()
            if not name:
                continue
            desc = " ".join(str(getattr(tool, "description", "") or "").split())
            out.append({"name": name, "description": desc[:400]})
    except Exception:
        return out
    return out


def collect_capability_inventory(
    *,
    enabled_search_methods: Optional[List[str]] = None,
    include_mcp_tools: Optional[bool] = None,
    mcp_modules: Optional[List[str]] = None,
    code_exec: Optional[bool] = None,
    skill_roots: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """The assistant's live capability inventory, read from the real tool registries.

    Probes the registries UNFILTERED: a per-request search-method allowlist or MCP flag is a
    client choice, not a limit on what the assistant can do. Deployment-level gates are reported
    as facts instead (code execution on/off, which optional backends are present), so the answer
    is truthful for the running deployment. Every registry is probed independently and failures
    degrade to omission — this never raises.
    """
    tools: List[Dict[str, str]] = []
    try:
        from agent_runtime.langchain_granular_tools import (
            make_langchain_geocode_tools,
            make_langchain_granular_tools,
        )

        tools += _tool_entries(make_langchain_granular_tools,
                               enabled_search_methods=None, include_file_tools=True)
        tools += _tool_entries(make_langchain_geocode_tools)
    except Exception:
        pass
    try:
        from agent_runtime.langchain_geo_tools import make_langchain_geo_tools

        tools += _tool_entries(make_langchain_geo_tools, default_input_file_ids=None)
    except Exception:
        pass
    # Overlay / aggregation / temporal analysis registries. Probed unfiltered like the
    # rest: the analyze and code peers only load these once files are attached, but
    # "can you clip a layer?" is true of the deployment regardless of what is attached
    # right now, so the inventory must report them.
    try:
        from agent_runtime.analysis_overlay_tools import make_overlay_tools

        tools += _tool_entries(make_overlay_tools, default_input_file_ids=None)
    except Exception:
        pass
    try:
        from agent_runtime.analysis_aggregate_tools import make_aggregate_tools

        tools += _tool_entries(make_aggregate_tools, default_input_file_ids=None)
    except Exception:
        pass
    try:
        from agent_runtime.analysis_temporal_tools import make_temporal_tools

        tools += _tool_entries(make_temporal_tools, default_input_file_ids=None)
    except Exception:
        pass
    try:
        from agent_runtime.analysis_spatial_stats_tools import make_spatial_stats_tools

        tools += _tool_entries(make_spatial_stats_tools, default_input_file_ids=None)
    except Exception:
        pass
    try:
        from extractors.geo_handles import make_geo_analysis_tools

        tools += _tool_entries(make_geo_analysis_tools)
    except Exception:
        pass
    # These five are bound by EVERY peer and were invisible to "what can you do" — so the
    # capability answer omitted the entire satellite-embedding surface (embed_region,
    # embed_zones, fit_zone_model, predict_for_region, segment_region), the named-area
    # boundary lookup every no-upload map workflow runs through, QGIS, and code execution.
    # Each in its own try/except, matching the per-registry pattern above: a registry that
    # cannot be constructed must leave the others intact.
    try:
        from agent_runtime.rs_embed_tools import make_rs_embed_tools

        tools += _tool_entries(make_rs_embed_tools, default_input_file_ids=None)
    except Exception:
        pass
    try:
        from agent_runtime.rs_embed_tools import make_rs_embed_zonal_tools

        tools += _tool_entries(make_rs_embed_zonal_tools, default_input_file_ids=None)
    except Exception:
        pass
    try:
        from agent_runtime.admin_boundary_tools import make_admin_boundary_tools

        tools += _tool_entries(make_admin_boundary_tools)
    except Exception:
        pass
    try:
        from agent_runtime.langchain_qgis_tools import make_langchain_qgis_tools

        tools += _tool_entries(make_langchain_qgis_tools)
    except Exception:
        pass
    try:
        from agent_runtime.langchain_exec_tools import make_code_execution_tools

        tools += _tool_entries(make_code_execution_tools)
    except Exception:
        pass

    mcp_on = include_mcp_tools
    if mcp_on is None:
        try:
            from agent_runtime.langchain_mcp_tools import mcp_tools_enabled

            mcp_on = bool(mcp_tools_enabled())
        except Exception:
            mcp_on = False
    if mcp_on:
        try:
            from agent_runtime.langchain_mcp_tools import make_langchain_mcp_tools

            tools += _tool_entries(make_langchain_mcp_tools,
                                   include_modules=mcp_modules or ["spatial_analysis_tools"])
        except Exception:
            pass

    exec_enabled: Optional[bool] = None
    exec_backend: Optional[str] = None
    try:
        from agent_runtime.code_execution import get_code_executor, is_code_exec_enabled

        exec_enabled = bool(code_exec) if code_exec is not None else is_code_exec_enabled()
        if exec_enabled:
            exec_backend = str(getattr(get_code_executor(), "backend", "") or "") or None
    except Exception:
        pass

    skills: List[Dict[str, str]] = []
    try:
        from agent_runtime.skills import SkillRegistry

        for entry in SkillRegistry.discover(skill_roots).catalog():
            name = str(entry.get("name") or "").strip()
            if name:
                skills.append({"name": name,
                               "description": " ".join(str(entry.get("description") or "").split())[:300]})
    except Exception:
        pass

    # De-duplicate by tool name, preserving first-seen order.
    seen: set = set()
    unique: List[Dict[str, str]] = []
    for entry in tools:
        if entry["name"] in seen:
            continue
        seen.add(entry["name"])
        unique.append(entry)

    return {
        "tools": unique,
        "code_execution": {"enabled": exec_enabled, "sandbox_backend": exec_backend},
        "skills": skills,
    }


def _mechanical_summary(inventory: Dict[str, Any]) -> str:
    """Last-resort listing of the inventory when no LLM is reachable.

    Mechanically derived from the registries (no authored capability text), so it also stays
    correct as tools change.
    """
    lines: List[str] = ["Available capabilities:"]
    for entry in inventory.get("tools") or []:
        desc = entry.get("description") or ""
        first = desc.split(". ")[0].rstrip(".")
        lines.append(f"- {entry['name']}" + (f" — {first}." if first else ""))
    code = inventory.get("code_execution") or {}
    if code.get("enabled") is not None:
        lines.append(f"- code execution: {'enabled' if code.get('enabled') else 'disabled'}"
                     + (f" (sandbox: {code.get('sandbox_backend')})" if code.get("sandbox_backend") else ""))
    for skill in inventory.get("skills") or []:
        lines.append(f"- skill: {skill['name']}")
    return "\n".join(lines)


def make_capability_tools(**config: Any) -> List[Any]:
    """Tools that let the agent inspect its OWN tool surface.

    Filterable ON PURPOSE. The previous design dumped the whole inventory into a prompt and cut
    it at 12,000 chars — 56% of a 27,397-char blob for this deployment, always the registries
    added last, and sliced mid-object so the model got malformed JSON. A tool the agent can
    query by topic removes the budget problem instead of tuning it, and lets the agent look
    again with a different word when the first look finds nothing.
    """
    try:
        from langchain_core.tools import StructuredTool
    except Exception:
        return []

    def list_my_capabilities(topic: str = "") -> str:
        """List the tools THIS deployment actually has, to ground an answer about what you can do.

        Pass a `topic` to filter — a domain word ("satellite imagery", "flood", "census"), a
        format ("geojson", "csv"), or an action ("cluster", "buffer", "predict"). Matching is
        LITERAL against the tool name and description, so the user's wording may miss: the full
        list of tool names always comes back too, and you should scan it and call again with a
        better word rather than answering from a weak match. Omit `topic` for everything.
        """
        inv = collect_capability_inventory(**config)
        tools = list(inv.get("tools") or [])
        needle = (topic or "").strip().lower()
        if needle:
            words = [w for w in re.split(r"[^a-z0-9]+", needle) if len(w) > 2]
            def hit(e: Dict[str, Any]) -> bool:
                blob = f"{e.get('name','')} {e.get('description','')}".lower()
                return any(w in blob for w in words) if words else False
            tools = [e for e in tools if hit(e)]
        every = list(inv.get("tools") or [])
        out: Dict[str, Any] = {
            "topic": topic or "(everything)",
            "matched": len(tools),
            "total_available": len(every),
            "tools": [{"name": e.get("name"),
                       "description": str(e.get("description") or "")[:_INVENTORY_DESC_CHARS]}
                      for e in tools[:40]],
            # ALWAYS the full name list — it is under a thousand characters and it is what
            # stops a weak topic match from reading as the whole answer. Substring matching
            # cannot bridge the user's words and the developer's: "satellite imagery" hit only
            # 2 of the 8 embedding tools because their descriptions say "remote-sensing
            # foundation model". With every name visible the agent can spot the right ones and
            # ask again, instead of confidently answering from a third of the surface.
            "all_tool_names": [str(e.get("name")) for e in every],
            "code_execution": inv.get("code_execution"),
            "skills": inv.get("skills"),
        }
        out["descriptions_shown"] = len(out["tools"])
        if needle:
            out["hint"] = ("a topic filter is a STARTING POINT, not the whole answer: matching "
                           "is literal, so the user's words may not be the developer's. Scan "
                           "all_tool_names and call again with a better word before concluding "
                           "anything is unsupported.")
        elif len(out["tools"]) < len(every):
            # The same "always the last registries" cut, in miniature: descriptions are capped
            # at 40 of 66, and the embedding tools are last. Say so, so the agent filters for
            # the rest instead of treating the first 40 as the whole surface.
            out["hint"] = (f"descriptions shown for {len(out['tools'])} of {len(every)} tools; "
                           "all_tool_names lists them all — call again with a `topic` to get "
                           "descriptions for the rest.")
        return json.dumps(out, ensure_ascii=True, separators=(",", ":"))

    return [StructuredTool.from_function(list_my_capabilities)]


# The prompt's inventory budget. The old code did json.dumps(..., indent=1)[:12000] on a blob
# that is 27,397 chars for this deployment — so 56% of the tool surface was cut, ALWAYS the
# registries appended last, and the model received JSON truncated mid-object. That is why the
# capability answer never mentioned satellite embeddings even after those registries were added:
# they were at the end of the list and never arrived.
_INVENTORY_PROMPT_CHARS = 24000
_INVENTORY_DESC_CHARS = 220


def _inventory_for_prompt(inventory: Any, *, max_chars: int = _INVENTORY_PROMPT_CHARS) -> str:
    """The inventory as compact JSON that FITS, dropping whole tools rather than mid-object.

    Descriptions are trimmed first (they average ~340 chars and lead with the punchy summary),
    then whole entries are dropped from the end with a count the model can see — so a squeeze is
    visible to the reader and to the log instead of producing malformed JSON.
    """
    payload = dict(inventory) if isinstance(inventory, dict) else {"tools": list(inventory or [])}
    tools = list(payload.get("tools") or [])
    trimmed = []
    for t in tools:
        entry = dict(t) if isinstance(t, dict) else {"name": str(t)}
        desc = str(entry.get("description") or "")
        if len(desc) > _INVENTORY_DESC_CHARS:
            entry["description"] = desc[:_INVENTORY_DESC_CHARS].rstrip() + "…"
        trimmed.append(entry)

    dropped = 0
    while True:
        payload["tools"] = trimmed
        if dropped:
            payload["tools_omitted_for_length"] = dropped
        text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        if len(text) <= max_chars or len(trimmed) <= 1:
            if dropped:
                logger.warning(
                    "capability inventory too large for the prompt: %d of %d tools omitted",
                    dropped, len(tools))
            return text
        trimmed = trimmed[:-1]
        dropped += 1


def describe_capabilities(*, llm: Optional[Any] = None, query: Optional[str] = None,
                          **config: Any) -> str:
    """Answer a capability question by REASONING over live self-introspection.

    A ReAct loop rather than a one-shot composer, because a fixed prompt can only answer the
    question it was written for. The old version took the inventory and no query at all, was
    told to "cover everything", and returned the same grouped catalogue however specific the
    question — "what can you do with satellite imagery?" got five generic headings and never
    mentioned embeddings, though the inventory carried eight tools for exactly that.

    With a loop the agent can look up its own surface by topic, look again with a different
    word, call the real listing tools to name actual models, and answer a question nobody
    anticipated. Falls back to the mechanical listing when no model is reachable.
    """
    tools = make_capability_tools(**config)
    # The genuinely cheap read-only listers, so "which models can you use?" gets the REAL names
    # from the service rather than a guess. Guarded: if rs-embed is unreachable the tool returns
    # an error and the agent can say so instead of inventing a list.
    try:
        from agent_runtime.rs_embed_tools import make_rs_embed_tools

        tools += [t for t in make_rs_embed_tools(default_input_file_ids=None)
                  if str(getattr(t, "name", "")) in
                  {"list_embedding_models", "list_prediction_heads"}]
    except Exception:
        pass

    try:
        from agent_runtime.executor_factory import (
            build_agent_executor,
            build_default_llm,
            invoke_agent_with_payload_fallback,
        )
        from agent_runtime.prompts import CAPABILITY_AGENT_PROMPT
        from agent_runtime.runtime_utils import extract_final_answer

        active = llm or build_default_llm()
        executor = build_agent_executor(
            llm=active, preloaded_tools=tools,
            system_prompt_override=CAPABILITY_AGENT_PROMPT,
        )
        resp = invoke_agent_with_payload_fallback(
            executor, query=(query or "What can you do?").strip(), chat_history=None)
        answer = extract_final_answer(resp) or ""
        if answer.strip():
            return answer.strip()
    except Exception:
        logger.warning("capability agent unavailable; falling back to a mechanical listing",
                       exc_info=True)
    return _mechanical_summary(collect_capability_inventory(**config))


__all__ = ["is_capability_query", "collect_capability_inventory",
           "make_capability_tools", "describe_capabilities"]
