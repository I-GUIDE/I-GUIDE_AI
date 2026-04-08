"""
neo4j_graph_tools.py
--------------------
Prewritten, parameterized Cypher tools for common relational query patterns
in the I-GUIDE knowledge graph.

These are deterministic, schema-aware queries that cover high-frequency
graph traversal patterns. They run BEFORE Text2Cypher and are much faster,
cheaper, and more reliable for known query shapes.

Hierarchy (called in order by GraphQueryDispatcher):
  1. Pattern match  → one of the tools below (deterministic, no LLM)
  2. Text2Cypher    → LLM-generated Cypher (flexible, catches everything else)
  3. Basic fallback → search_neo4j.get_neo4j_search_results (keyword-only)

Each tool returns List[Dict] in the same raw-hit shape expected by
_records_to_hits() in search_neo4j.py.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("neo4j_graph_tools")


# ---------------------------------------------------------------------------
# Cypher constants
# ---------------------------------------------------------------------------

# Shared score expression reused across queries for consistency.
_SCORE_EXPR = (
    "coalesce(log10(toFloat(coalesce(r.click_count, 0)) + 1), 0) * 0.3 "
    "+ coalesce(toFloat(count { (r)--() }), 0) * 0.05"
)

_CYPHER_BY_AUTHOR = """
MATCH (c:Contributor)-[:CONTRIBUTED]->(r)
WHERE toLower(coalesce(c.display_first_name, '')) CONTAINS toLower($first)
  AND toLower(coalesce(c.display_last_name, ''))  CONTAINS toLower($last)
WITH r, c,
     {SCORE_EXPR} AS score
RETURN r AS node, score, c.display_first_name + ' ' + c.display_last_name AS matched_author
ORDER BY score DESC
LIMIT $limit
""".replace("{SCORE_EXPR}", _SCORE_EXPR)

# Secondary: search User nodes (same split-name approach)
_CYPHER_BY_USER_AUTHOR = """
MATCH (u:User)-[:CONTRIBUTED]->(r)
WHERE toLower(coalesce(u.display_first_name, '')) CONTAINS toLower($first)
  AND toLower(coalesce(u.display_last_name, ''))  CONTAINS toLower($last)
WITH r, u,
     {SCORE_EXPR} AS score
RETURN r AS node, score, u.display_first_name + ' ' + u.display_last_name AS matched_author
ORDER BY score DESC
LIMIT $limit
""".replace("{SCORE_EXPR}", _SCORE_EXPR)

_CYPHER_BY_ORGANIZATION = """
MATCH (c:Contributor)-[:CONTRIBUTED]->(r)
WHERE toLower(coalesce(c.organization, '')) CONTAINS toLower($org)
   OR toLower(coalesce(c.affiliation, ''))  CONTAINS toLower($org)
   OR toLower(coalesce(c.institution, ''))  CONTAINS toLower($org)
WITH r, c,
     {SCORE_EXPR} AS score
RETURN r AS node, score, c.organization AS matched_org
ORDER BY score DESC
LIMIT $limit
""".replace("{SCORE_EXPR}", _SCORE_EXPR)

_CYPHER_BY_TAG = """
MATCH (r)
WHERE any(tag IN coalesce(r.tags, []) WHERE toLower(tag) CONTAINS toLower($tag))
WITH r,
     1.5 + {SCORE_EXPR} AS score
RETURN r AS node, score
ORDER BY score DESC
LIMIT $limit
""".replace("{SCORE_EXPR}", _SCORE_EXPR)

_CYPHER_BY_RESOURCE_TYPE = """
MATCH (r)
WHERE toLower(coalesce(r.element_type, r.`resource-type`, '')) CONTAINS toLower($rtype)
WITH r,
     {SCORE_EXPR} AS score
RETURN r AS node, score
ORDER BY score DESC
LIMIT $limit
""".replace("{SCORE_EXPR}", _SCORE_EXPR)

_CYPHER_RELATED_TO = """
MATCH (seed)-[:RELATED]-(r)
WHERE toLower(coalesce(seed.title, '')) CONTAINS toLower($title)
WITH r, seed,
     {SCORE_EXPR} AS score
RETURN r AS node, score, seed.title AS seed_title
ORDER BY score DESC
LIMIT $limit
""".replace("{SCORE_EXPR}", _SCORE_EXPR)

_CYPHER_IN_COLLECTION = """
MATCH (r)-[:BELONGS_TO]->(col:Collection)
WHERE toLower(coalesce(col.title, col.name, '')) CONTAINS toLower($collection)
WITH r, col,
     {SCORE_EXPR} AS score
RETURN r AS node, score, col.title AS collection_name
ORDER BY score DESC
LIMIT $limit
""".replace("{SCORE_EXPR}", _SCORE_EXPR)


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

# Each entry: (pattern_name, compiled_regex, list_of_capture_group_names)
#
# ORDER MATTERS — first match wins. Rules:
#   1. by_author        — must come before by_organization (both can match "by X")
#   2. in_collection    — must come before by_resource_type ("resources in X collection"
#                         would otherwise match "resources" as a resource type)
#   3. related_to       — must come before by_resource_type for the same reason
#   4. by_organization  — before by_resource_type (org names can look like type words)
#   5. by_tag           — explicit trigger words, safe anywhere
#   6. by_resource_type — last resort; only fires on explicit verb + type word
_PATTERNS: List[Tuple[str, re.Pattern, List[str]]] = [
    # 1. Author / person — matches "Firstname Lastname" or "Firstname Middle Lastname"
    #    Excludes institutional trigger words so "University of Illinois" routes to
    #    by_organization instead.
    (
        "by_author",
        re.compile(
            r"\b(?:by|from|authored\s+by|written\s+by|published\s+by|"
            r"publications?\s+(?:by|from)|notebooks?\s+(?:by|from)|"
            r"resources?\s+(?:by|from)|contributions?\s+(?:by|from)|"
            r"what\s+has\s+|work\s+(?:by|from))\s+"
            r"(?!(?:university|institute|lab|laboratory|center|centre|"
            r"agency|department|dept|college|school|nasa|noaa|usgs|epa)\b)"
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"  # 2-3 words, all Title Case
            r"(?!\s+(?:university|institute|lab|of\s+))",
            re.I,
        ),
        ["name"],
    ),
    # 2. Collection membership — "in/within the X collection"
    (
        "in_collection",
        re.compile(
            r"\b(?:in|within|part\s+of|inside|belonging\s+to)\s+"
            r"(?:the\s+)?(?:collection\s+)?"
            r"[\"']?([a-zA-Z0-9 _\-]{3,60})[\"']?"
            r"(?:\s+collection)?",
            re.I,
        ),
        ["collection"],
    ),
    # 3. Related-to — "related to / similar to / associated with X"
    (
        "related_to",
        re.compile(
            r"\b(?:related\s+to|similar\s+to|associated\s+with)\s+"
            r"[\"']?([^\"'\n]{3,60})[\"']?",
            re.I,
        ),
        ["title"],
    ),
    # 4. Organization — requires capitalized org name to reduce false matches
    (
        "by_organization",
        re.compile(
            r"\b(?:from|affiliated\s+with|contributed\s+by|submitted\s+by|at|by)\s+"
            r"((?:[A-Z][a-zA-Z&,.\-]+\s*){1,6})"
            r"(?:\s+(?:university|institute|lab|laboratory|center|centre|"
            r"agency|department|dept|college|school|program|project))?",
            re.I,
        ),
        ["org"],
    ),
    # 5. Tag — explicit trigger words only
    (
        "by_tag",
        re.compile(
            r"\b(?:tagged\s+(?:with\s+)?|tag[:\s]+|"
            r"labeled\s+(?:with\s+)?|category[:\s]+|topic[:\s]+)"
            r"[\"']?([a-zA-Z0-9 _\-]+)[\"']?",
            re.I,
        ),
        ["tag"],
    ),
    # 6. Resource type — requires an explicit action verb before the type word
    #    so bare mentions like "resources" or "datasets from NASA" don't trigger it
    (
        "by_resource_type",
        re.compile(
            r"\b(?:show\s+(?:me\s+)?(?:all\s+)?|find\s+(?:all\s+)?|"
            r"list\s+(?:all\s+)?|get\s+(?:all\s+)?|browse\s+(?:all\s+)?|"
            r"all\s+)"
            r"(datasets?|notebooks?|publications?|maps?|code|oers?|"
            r"documentation|collections?)\b",
            re.I,
        ),
        ["rtype"],
    ),
]


def detect_pattern(query: str) -> Optional[Tuple[str, Dict[str, str]]]:
    """
    Return (pattern_name, captured_params) for the first matching pattern,
    or None if no pattern matches.

    >>> detect_pattern("publications by John Smith")
    ('by_author', {'name': 'John Smith'})
    >>> detect_pattern("datasets from NASA")
    ('by_organization', {'org': 'NASA'})
    >>> detect_pattern("what is climate change")
    None
    """
    for pattern_name, regex, group_names in _PATTERNS:
        m = regex.search(query)
        if m:
            # Map positional groups to named keys
            captured = {}
            for i, key in enumerate(group_names, 1):
                try:
                    val = m.group(i)
                except IndexError:
                    val = ""
                captured[key] = (val or "").strip()

            # Skip if the captured value is empty or too short
            main_val = captured.get(group_names[0], "")
            if len(main_val) < 2:
                continue

            log.debug("Pattern '%s' matched query '%s' → %s", pattern_name, query, captured)
            return pattern_name, captured

    return None


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

_TOOL_MAP: Dict[str, str] = {
    "by_author": _CYPHER_BY_AUTHOR,
    "by_organization": _CYPHER_BY_ORGANIZATION,
    "by_tag": _CYPHER_BY_TAG,
    "by_resource_type": _CYPHER_BY_RESOURCE_TYPE,
    "related_to": _CYPHER_RELATED_TO,
    "in_collection": _CYPHER_IN_COLLECTION,
}


def _split_name(full_name: str) -> Tuple[str, str]:
    """
    Split a name string into (first, last) for independent matching.
    Handles middle names: "Smit Bharat Vasani" → first="Smit", last="Vasani"
    Single word: "Vasani" → first="", last="Vasani"
    """
    parts = full_name.strip().split()
    if len(parts) == 0:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]          # treat single word as last name
    return parts[0], parts[-1]       # first word = first name, last word = last name


def build_tool_query(
    pattern_name: str,
    captured: Dict[str, str],
    limit: int,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Return (cypher, params) for a matched pattern, or None if unsupported.
    Author queries search Contributor nodes first; User nodes are the fallback
    handled by run_user_author_fallback.
    """
    cypher = _TOOL_MAP.get(pattern_name)
    if not cypher:
        return None

    params: Dict[str, Any] = {"limit": max(1, min(limit, 100))}

    if pattern_name == "by_author":
        first, last = _split_name(captured.get("name", ""))
        params["first"] = first
        params["last"] = last
    elif pattern_name == "by_organization":
        params["org"] = captured.get("org", "")
    elif pattern_name == "by_tag":
        params["tag"] = captured.get("tag", "")
    elif pattern_name == "by_resource_type":
        params["rtype"] = captured.get("rtype", "")
    elif pattern_name == "related_to":
        params["title"] = captured.get("title", "")
    elif pattern_name == "in_collection":
        params["collection"] = captured.get("collection", "")

    return cypher, params


def run_user_author_fallback(
    captured: Dict[str, str],
    limit: int,
    run_fn,
) -> List[Dict[str, Any]]:
    """
    Secondary author lookup via User nodes when Contributor lookup returns nothing.
    Uses the same first/last split so middle names don't break matching.
    """
    first, last = _split_name(captured.get("name", ""))
    params = {"first": first, "last": last, "limit": max(1, min(limit, 100))}
    try:
        return run_fn(_CYPHER_BY_USER_AUTHOR, params)
    except Exception as exc:
        log.warning("User author fallback failed: %s", exc)
        return []