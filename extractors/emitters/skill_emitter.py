"""SKILL emitter — write the overall pipeline as a discoverable SKILL.md bundle.

From ``manifest.skill`` (a SkillSpec dict): validate the front matter against
agent_runtime/skills.py rules, render ``SKILL.md`` (front matter + ordered-steps
body), and write it to a discovered skills root (default ``REPO_ROOT/.agents/skills``
— one of SkillRegistry's default roots — overridable via ``AGENT_GENERATED_SKILLS_ROOT``).
A malformed skill would land silently in ``SkillRegistry.errors``, so we validate
(and best-effort round-trip through the real registry) before/after writing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..manifest import UnifiedManifest

REPO_ROOT = Path(__file__).resolve().parents[2]
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _default_root() -> Path:
    return Path(os.getenv("AGENT_GENERATED_SKILLS_ROOT", str(REPO_ROOT / ".agents" / "skills")))


def _validate(name: str, description: str) -> None:
    if not name or not _NAME_RE.match(name):
        raise ValueError(f"invalid skill name '{name}' (must match ^[a-z0-9][a-z0-9-]{{0,63}}$)")
    if not (description or "").strip():
        raise ValueError("skill description is required")


def _render(skill: Dict[str, Any]) -> str:
    front = {
        "name": skill["name"],
        "description": " ".join(str(skill.get("description") or "").split()),
        "allowed-tools": list(skill.get("allowed_tools") or []),
        "tags": list(skill.get("tags") or []),
    }
    fm = yaml.safe_dump(front, sort_keys=False, default_flow_style=False, allow_unicode=True).strip()

    title = skill["name"].replace("-", " ").title()
    lines: List[str] = [f"---\n{fm}\n---", "", f"# {title}", "",
                        front["description"], "", "## Pipeline steps", ""]
    for i, step in enumerate(skill.get("ordered_steps") or [], 1):
        tools = ", ".join(step.get("tools") or []) or "—"
        summary = " ".join(str(step.get("summary") or "").split())[:140]
        lines.append(f"{i}. (cell {step.get('order')}) tools: {tools} — {summary}")
    if skill.get("allowed_tools"):
        lines += ["", "## Run", "",
                  f"Invoke `{skill['allowed_tools'][0]}` (the extracted workflow) to reproduce this pipeline."]
    else:
        # No callable tool exists for a promoted workflow: the per-workflow executor
        # names were fictional and the real executors are gated off (see
        # doc_ids.mcp_tool_name_for). Say what to do instead of naming nothing.
        lines += ["", "## Run", "",
                  "There is no single tool that runs this pipeline. Reproduce it by "
                  "reusing the functions extracted from this element and composing them "
                  "in `execute_code`; the steps above give the order."]
    return "\n".join(lines).rstrip() + "\n"


def _roundtrip_ok(root: Path, name: str) -> Optional[bool]:
    """Best-effort: confirm the real SkillRegistry discovers it without error."""
    try:
        from agent_runtime.skills import SkillRegistry
    except Exception:
        return None
    try:
        reg = SkillRegistry.discover([str(root)])
        names = {s.get("name") for s in reg.catalog()} if hasattr(reg, "catalog") else set()
        return name in names
    except Exception:
        return None


def emit(manifest: UnifiedManifest, *, skills_root: Optional[str] = None,
         dry_run: bool = False) -> Dict[str, Any]:
    d = manifest.to_dict() if isinstance(manifest, UnifiedManifest) else dict(manifest)
    skill = d.get("skill")
    if not skill:
        return {"written": None, "reason": "no skill in manifest"}

    _validate(skill.get("name"), skill.get("description"))
    md = _render(skill)
    if dry_run:
        return {"dry_run": True, "name": skill["name"], "bytes": len(md), "preview": md[:300]}

    root = Path(skills_root) if skills_root else _default_root()
    dest = root / skill["name"] / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(md, encoding="utf-8")
    return {"written": str(dest), "name": skill["name"], "root": str(root),
            "discoverable": _roundtrip_ok(root, skill["name"])}


__all__ = ["emit"]
