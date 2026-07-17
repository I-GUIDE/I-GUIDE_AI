from __future__ import annotations

from agent_runtime.supervisor.graph import (
    _collect_image_artifacts,
    _drop_previously_shown,
    _format_chat_history,
    _strip_image_markdown,
)


def test_strip_removes_url_keeps_caption():
    out = _strip_image_markdown(
        "Here it is: ![Plot of y = x^2](https://host/agent/files/file_abc/download)"
    )
    assert "https://host" not in out and "download" not in out
    assert "[image shown earlier: Plot of y = x^2]" in out


def test_strip_handles_empty_caption():
    assert _strip_image_markdown("![](http://h/x.png)") == "[image shown earlier: figure]"


def test_strip_multiple_and_non_image_links_preserved():
    out = _strip_image_markdown(
        "![a](u1) text [link](http://keep/me) ![b](u2)"
    )
    assert "u1" not in out and "u2" not in out
    assert "[link](http://keep/me)" in out  # ordinary links are untouched
    assert out.count("[image shown earlier:") == 2


def test_format_chat_history_strips_prior_turn_image():
    history = [
        {"role": "user", "content": "Plot y=x^2"},
        {"role": "assistant",
         "content": "Done: ![Plot of y = x^2](https://host/agent/files/file_abc/download)"},
        {"role": "user", "content": "How to visualize a heatmap of chicago crimes"},
    ]
    rendered = _format_chat_history(history)
    # The old plot's download_url must not survive into the synthesizer prompt.
    assert "file_abc" not in rendered and "download" not in rendered
    assert "image shown earlier" in rendered
    # Ordinary conversation text is preserved.
    assert "heatmap of chicago crimes" in rendered


# --- artifact scoping: an image belongs to the turn that produced it ----------

_PLOT = {"filename": "result.png", "file_id": "file_c57852b8f037",
         "download_url": "https://host/agent/files/file_c57852b8f037/download"}


def test_drop_previously_shown_by_url():
    history = [("assistant", f"Done: ![Plot]({_PLOT['download_url']})")]
    assert _drop_previously_shown([_PLOT], history) == []


def test_drop_previously_shown_by_file_id_path():
    # Prior answer referenced the file by id in a URL path, different caption.
    history = [{"role": "assistant", "content": "see /agent/files/file_c57852b8f037/download"}]
    assert _drop_previously_shown([_PLOT], history) == []


def test_keep_artifact_produced_this_turn():
    history = [("user", "Plot y=x^2"), ("assistant", "some earlier unrelated answer")]
    assert _drop_previously_shown([_PLOT], history) == [_PLOT]


def test_keep_when_no_history():
    assert _drop_previously_shown([_PLOT], []) == [_PLOT]
    assert _drop_previously_shown([_PLOT], None) == [_PLOT]


def test_collect_then_drop_end_to_end():
    # Simulates the synthesize path: code_result carries a stale artifact that was
    # already shown last turn; it must not be collected-and-embedded again.
    code_result = {"tool_results": [{"name": "execute_code",
                                     "content": {"artifacts": [_PLOT]}}]}
    collected = _collect_image_artifacts(None, code_result)
    assert collected and collected[0]["file_id"] == _PLOT["file_id"]
    history = [("assistant", f"![Plot of y = x^2]({_PLOT['download_url']})")]
    assert _drop_previously_shown(collected, history) == []
