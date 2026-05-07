from __future__ import annotations

import json

import pytest

from agent_runtime.skills import SkillError, SkillRegistry, make_skill_tools, parse_frontmatter
from agent_runtime.tool_policy import select_allowed_tools


def _write_skill(root, name: str, description: str = "Test workflow skill.") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: {description}
allowed-tools: keyword_search, semantic_search
tags: [test, docs]
---

# {name}

Use this skill for test workflows.
""",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("Reference details.", encoding="utf-8")


def test_parse_frontmatter_requires_name_and_description(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    text = """---
name: sample-skill
description: Useful for sample tasks.
---

# Sample
"""
    frontmatter, body = parse_frontmatter(text, skill_file=skill_file)

    assert frontmatter["name"] == "sample-skill"
    assert frontmatter["description"] == "Useful for sample tasks."
    assert body == "# Sample"


def test_parse_frontmatter_supports_block_lists(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    text = """---
name: list-skill
description: Useful for list frontmatter.
allowed-tools:
  - keyword_search
  - spatial_search
tags:
  - reports
  - geospatial
---

# List Skill
"""
    frontmatter, _body = parse_frontmatter(text, skill_file=skill_file)

    assert frontmatter["allowed-tools"] == ["keyword_search", "spatial_search"]
    assert frontmatter["tags"] == ["reports", "geospatial"]


def test_skill_registry_discovers_loads_and_lists_resources(tmp_path):
    _write_skill(tmp_path, "geo-workflow", "Use for geospatial workflows.")

    registry = SkillRegistry.discover([tmp_path])

    assert [skill.name for skill in registry.list()] == ["geo-workflow"]
    loaded = registry.load_skill("geo-workflow")
    assert loaded["status"] == "ok"
    assert loaded["skill"]["description"] == "Use for geospatial workflows."
    assert "Use this skill for test workflows." in loaded["instructions"]
    assert loaded["resources"] == [
        {
            "path": "references/guide.md",
            "size_bytes": len("Reference details."),
            "loadable": True,
        }
    ]

    resource = registry.load_resource("geo-workflow", "references/guide.md")
    assert resource["content"] == "Reference details."


def test_skill_registry_rejects_path_escape(tmp_path):
    _write_skill(tmp_path, "safe-skill")
    registry = SkillRegistry.discover([tmp_path])

    with pytest.raises(SkillError):
        registry.load_resource("safe-skill", "../secret.txt")


def test_skill_registry_reports_invalid_skill_files(tmp_path):
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: Bad_Skill
description: Invalid because the name is not normalized.
---

# Bad
""",
        encoding="utf-8",
    )

    registry = SkillRegistry.discover([tmp_path])

    assert registry.catalog() == []
    assert registry.errors
    assert "invalid skill name" in registry.errors[0]["error"]


def test_make_skill_tools_returns_loader_tools(tmp_path):
    _write_skill(tmp_path, "analysis-skill")

    tools = make_skill_tools(skill_roots=[tmp_path])
    tool_names = [tool.name for tool in tools]

    assert tool_names == ["list_available_skills", "load_skill"]
    load_tool = next(tool for tool in tools if tool.name == "load_skill")
    payload = json.loads(load_tool.invoke({"skill_name": "analysis-skill"}))
    assert payload["status"] == "ok"
    assert payload["skill"]["name"] == "analysis-skill"


def test_make_skill_tools_empty_when_no_skills(tmp_path):
    assert make_skill_tools(skill_roots=[tmp_path]) == []


def test_skill_tools_survive_intent_filtering():
    allowed = select_allowed_tools(
        "analysis_task",
        ["keyword_search", "load_skill", "list_available_skills"],
    )

    assert "load_skill" in allowed
    assert "list_available_skills" in allowed
