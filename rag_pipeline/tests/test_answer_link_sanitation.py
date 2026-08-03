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


# --- artifact verification: only files the run produced may be linked ------------

_IDS = ["file_58a9ca3c73d1"]
_URLS = ["https://h/agent/files/file_58a9ca3c73d1/download"]


def _s(text):
    return sanitize_answer_links(text, allowed_file_ids=_IDS, allowed_urls=_URLS)


def test_drops_fabricated_artifact_hosts():
    """The live failure: the model invented an S3 host from the internal job path, producing a
    broken <img> in the client."""
    assert _s("![m](https://agent-chat-files.s3.amazonaws.com/qgis_jobs/x/rendered_map.png)") == ""
    assert _s("[dl](https://agent-chat-files.s3.amazonaws.com/outputs/buffer.geojson)") == "dl"
    # an /agent/files/ URL for a file this run did NOT produce is equally unverifiable
    assert _s("![m](https://h/agent/files/file_deadbeef99/download)") == ""


def test_keeps_real_artifacts_and_ordinary_citations():
    assert _s("![ok](https://h/agent/files/file_58a9ca3c73d1/download)") == \
        "![ok](https://h/agent/files/file_58a9ca3c73d1/download)"
    for keep in ("[NID](https://platform.i-guide.io/datasets/abc)",
                 "[GRanD](https://sedac.ciesin.columbia.edu/data/set/x)",
                 "[mail](mailto:a@b.c)", "![inline](data:image/png;base64,AAA)"):
        assert _s(keep) == keep


def test_verification_is_opt_in():
    """Without an artifact set only the path/scheme rules apply (no false drops)."""
    txt = "![m](https://some.host/img.png)"
    assert sanitize_answer_links(txt) == txt


def test_collect_download_refs_finds_nested_managed_outputs():
    from agent_runtime.supervisor.graph import _collect_download_refs
    ar = {"steps": [{"result": {"managed_output": {
        "file_id": "file_a", "filename": "buffer.geojson",
        "download_url": "https://h/agent/files/file_a/download"}}}]}
    cr = '{"artifacts": [{"file_id": "file_b", "download_url": "/agent/files/file_b/download"}]}'
    refs = _collect_download_refs(ar, cr)
    assert refs["file_ids"] == ["file_a", "file_b"]          # incl. JSON-encoded tool output
    assert "https://h/agent/files/file_a/download" in refs["urls"]


def test_drops_fabricated_download_offers_on_any_host():
    """Second live leak: after the S3 host was blocked the model produced
    [Download buffer GeoJSON](https://example.com/path-to-buffer.geojson) — a placeholder that
    dodges the agent-file heuristic. A file OFFER (artifact extension or 'download' label) must
    resolve to a real artifact or evidence URL."""
    ids = ["file_x"]
    urls = ["https://h/agent/files/file_x/download",
            "https://sedac.ciesin.columbia.edu/data/set/grand-v1-dams"]

    def s(t):
        return sanitize_answer_links(t, allowed_file_ids=ids, allowed_urls=urls)

    assert s("[Download buffer GeoJSON](https://example.com/path-to-buffer.geojson)") == \
        "Download buffer GeoJSON"
    assert s("[Download it](https://foo.invalid/x)") == "Download it"
    assert s("![plot](https://foo.invalid/out.png)") == ""
    # real artifact + real evidence URLs survive
    assert s("[Download](https://h/agent/files/file_x/download)") == \
        "[Download](https://h/agent/files/file_x/download)"
    assert s("[GRanD](https://sedac.ciesin.columbia.edu/data/set/grand-v1-dams)") == \
        "[GRanD](https://sedac.ciesin.columbia.edu/data/set/grand-v1-dams)"
    # ordinary element citation (no file extension, no download label) is untouched
    assert s("[NID](https://platform.i-guide.io/datasets/abc)") == \
        "[NID](https://platform.i-guide.io/datasets/abc)"
