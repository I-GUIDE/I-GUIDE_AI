from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent_runtime.trace_analyzer import (
    filter_trace,
    format_summary,
    format_event,
    get_errors,
    parse_trace_events,
    summarize_trace,
)
from agent_runtime.trace_store import TraceStore


def make_sample_events():
    return [
        {"event": "status", "data": {"stage": "started", "sequence": 1}},
        {"event": "route_trace", "data": {"query": "test", "route": "search", "called_tools": ["search_agent_evidence"]}},
        {"event": "tool_call", "data": {"name": "search_agent_evidence", "args": {"q": "test"}, "sequence": 3}},
        {"event": "tool_result", "data": {"name": "search_agent_evidence", "content": "result", "sequence": 4}},
        {"event": "final_answer", "data": {"answer": "Test answer", "sequence": 5}},
    ]


def test_parse_and_summarize_trace():
    events = make_sample_events()
    parsed = parse_trace_events(events)
    assert parsed == events
    summary = summarize_trace(parsed)
    assert summary["query"] == "test"
    assert summary["route"] == "search"
    assert summary["tool_call_count"] == 1
    assert summary["final_answer"] == "Test answer"


def test_filter_trace_by_event_type():
    events = make_sample_events()
    tool_events = filter_trace(events, event_types=["tool_call", "tool_result"])
    assert len(tool_events) == 2
    assert tool_events[0]["event"] == "tool_call"
    assert tool_events[1]["event"] == "tool_result"


def test_formatters_include_key_information():
    events = make_sample_events()
    assert "[route_trace]" in format_event(events[1])
    summary = summarize_trace(events)
    result = format_summary(summary)
    assert "Route: search" in result
    assert "Tool calls: 1" in result


def test_trace_store_save_and_load(tmp_path: Path):
    store = TraceStore(max_size=10)
    events = make_sample_events()
    trace = store.add_trace("trace-1", query="test", events=events)
    assert store.get_trace("trace-1") is trace
    file_path = tmp_path / "trace_store.json"
    store.save_to_file(str(file_path))
    loaded = TraceStore.load_from_file(str(file_path))
    assert loaded.get_trace("trace-1") is not None
    assert loaded.get_trace("trace-1").query == "test"
    assert loaded.get_trace("trace-1").events == events
