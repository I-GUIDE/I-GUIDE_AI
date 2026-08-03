"""Answers must never carry links a client cannot fetch: the LLM's `sandbox:` pseudo-scheme or a
tool's internal filesystem path (observed live: sandbox:/app/agent_chat_files/qgis_jobs/.../x.png).
"""

from __future__ import annotations

import agent_runtime.agent_chat_service as acs
from agent_runtime.runtime_utils import sanitize_answer_links, strip_sandbox_uris


def test_strips_sandbox_scheme_but_keeps_the_url():
    assert sanitize_answer_links("[map](sandbox:/agent/files/file_x/download)") == \
        "[map](/agent/files/file_x/download)"
    assert strip_sandbox_uris("see sandbox:https://ex.com/f.png") == "see https://ex.com/f.png"
    assert sanitize_answer_links("SANDBOX:/agent/files/f/download") == "/agent/files/f/download"


def test_defuses_internal_filesystem_paths():
    # image pointing at a container path -> removed entirely (the real embed is appended elsewhere)
    out = sanitize_answer_links(
        "![m](sandbox:/app/agent_chat_files/qgis_jobs/a/rendered_map.png) "
        "![ok](https://h/agent/files/f/download)")
    assert "/app/agent_chat_files" not in out
    assert "![ok](https://h/agent/files/f/download)" in out
    # text link -> degrades to its label, no dead link
    assert sanitize_answer_links("[Download Buffer](sandbox:/app/agent_chat_files/x/buffer.geojson)") \
        == "Download Buffer"
    assert sanitize_answer_links("[cfg](file:///etc/hosts)") == "cfg"


def test_leaves_servable_targets_and_prose_untouched():
    for keep in (
        "[el](https://platform.i-guide.io/datasets/abc)",
        "![png](/agent/files/file_1/download)",
        "[mail](mailto:a@b.c)", "[anchor](#section)",
        "![inline](data:image/png;base64,AAA)",
    ):
        assert sanitize_answer_links(keep) == keep
    prose = "the headless sandbox cannot display windows; sandbox: rules apply"
    assert sanitize_answer_links(prose) == prose
    assert sanitize_answer_links("") == "" and sanitize_answer_links(None) == ""


def test_applied_at_the_service_boundary(monkeypatch):
    def fake(query, *, chat_history=None, thread_id=None, **kwargs):
        return {"final_answer": "Map: ![m](sandbox:/app/agent_chat_files/j/rendered_map.png) "
                                "and ![real](https://h/agent/files/file_9/download)",
                "thread_id": thread_id, "available_skills": [], "route_trace": {}}
    monkeypatch.setattr(acs, "run_agent_query", fake)
    ans = acs.run_agent_chat(user_input="map it", thread_id="s", use_persistent_memory=False)["answer"]
    assert "sandbox:" not in ans and "/app/agent_chat_files" not in ans
    assert "![real](https://h/agent/files/file_9/download)" in ans
