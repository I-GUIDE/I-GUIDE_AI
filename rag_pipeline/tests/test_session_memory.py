"""Tests for session-local (process-local) conversation memory.

Covers (1) the session_memory store directly and (2) the agent_chat_service
integration: with persistent (OpenSearch) memory OFF, a follow-up turn must
still see the prior conversation, keyed by thread_id.

The orchestrator seam (run_agent_query / stream_agent_query_events) is stubbed,
so there is no real LLM, search backend, or OpenSearch.
"""

from __future__ import annotations

import pytest

import agent_runtime.agent_chat_service as acs
from agent_runtime import session_memory as sm


@pytest.fixture(autouse=True)
def _clean_store():
    sm.reset_all()
    yield
    sm.reset_all()


# --- session_memory store --------------------------------------------------

def test_append_and_get_round_trip():
    sm.append_session_turn("t1", "what is the capital of france?", "Paris.")
    hist = sm.get_session_history("t1")
    assert hist == [{"userQuery": "what is the capital of france?", "answer": "Paris."}]


def test_threads_are_isolated():
    sm.append_session_turn("t1", "q1", "a1")
    sm.append_session_turn("t2", "q2", "a2")
    assert sm.get_session_history("t1") == [{"userQuery": "q1", "answer": "a1"}]
    assert sm.get_session_history("t2") == [{"userQuery": "q2", "answer": "a2"}]


def test_falsy_thread_id_is_noop():
    sm.append_session_turn(None, "q", "a")
    sm.append_session_turn("", "q", "a")
    assert sm.get_session_history(None) == []
    assert sm.get_session_history("") == []


def test_unknown_thread_returns_empty():
    assert sm.get_session_history("never-seen") == []


def test_get_returns_a_copy_not_the_internal_list():
    sm.append_session_turn("t1", "q1", "a1")
    hist = sm.get_session_history("t1")
    hist.append({"userQuery": "x", "answer": "y"})  # mutate the copy
    assert sm.get_session_history("t1") == [{"userQuery": "q1", "answer": "a1"}]


def test_per_thread_turn_cap(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_MEMORY_MAX_TURNS", "3")
    for i in range(5):
        sm.append_session_turn("t1", f"q{i}", f"a{i}")
    hist = sm.get_session_history("t1")
    assert [h["userQuery"] for h in hist] == ["q2", "q3", "q4"]  # oldest dropped


def test_global_thread_lru_eviction(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_MEMORY_MAX_THREADS", "2")
    sm.append_session_turn("t1", "q", "a")
    sm.append_session_turn("t2", "q", "a")
    sm.append_session_turn("t3", "q", "a")  # evicts least-recently-used (t1)
    assert sm.get_session_history("t1") == []
    assert sm.get_session_history("t2")
    assert sm.get_session_history("t3")


def test_build_session_memory_doc_shape():
    sm.append_session_turn("t1", "q1", "a1")
    doc = sm.build_session_memory_doc("t1")
    assert doc == {"chat_history": [{"userQuery": "q1", "answer": "a1"}]}


def test_clear_session():
    sm.append_session_turn("t1", "q", "a")
    sm.clear_session("t1")
    assert sm.get_session_history("t1") == []


# --- agent_chat_service integration (persistence OFF) ----------------------

def _stub_run_agent_query(monkeypatch, answer="ANSWER", record=None):
    """Patch run_agent_query to capture chat_history and return a canned result."""

    def fake(query, *, chat_history=None, thread_id=None, **kwargs):
        if record is not None:
            record.append({"chat_history": list(chat_history or []), "thread_id": thread_id})
        return {
            "final_answer": answer,
            "thread_id": thread_id,
            "available_skills": [],
            "route_trace": {},
        }

    monkeypatch.setattr(acs, "run_agent_query", fake)


def test_session_memory_preserved_when_persistent_off(monkeypatch):
    """Turn 1 records; turn 2 (same thread_id) sees the prior turn as chat_history,
    even though use_persistent_memory is False."""
    record = []
    _stub_run_agent_query(monkeypatch, answer="def fib(): ...", record=record)

    # Turn 1: no prior history.
    r1 = acs.run_agent_chat(
        user_input="write python for the first 15 fibonacci numbers",
        thread_id="sess-1",
        use_persistent_memory=False,
    )
    assert record[0]["chat_history"] == []  # nothing yet
    assert r1["memory_id"] is None  # OpenSearch not used
    assert r1["thread_id"] == "sess-1"

    # Turn 2: a follow-up on the SAME thread must carry turn-1 context.
    acs.run_agent_chat(
        user_input="show me the code",
        thread_id="sess-1",
        use_persistent_memory=False,
    )
    turn2_history = record[1]["chat_history"]
    assert {"role": "user", "content": "write python for the first 15 fibonacci numbers"} in turn2_history
    assert {"role": "assistant", "content": "def fib(): ..."} in turn2_history


def test_no_session_record_when_thread_id_absent(monkeypatch):
    """Without a stable thread_id there is nothing to key session memory on; the
    auto-generated thread_id from the result is used for the write side."""
    record = []
    _stub_run_agent_query(monkeypatch, answer="A", record=record)

    out = acs.run_agent_chat(user_input="hi", use_persistent_memory=False)
    # run_agent_query received None thread_id (stub echoes it back as None) so
    # nothing is recorded; this is acceptable (no session continuity w/o an id).
    assert out["memory_id"] is None
    assert sm.get_session_history(None) == []


def test_persistent_on_does_not_double_write_session(monkeypatch):
    """When OpenSearch persistence succeeds, the turn is NOT also written to the
    session store (avoids duplication); chat_history comes from the memory_doc."""
    record = []
    _stub_run_agent_query(monkeypatch, answer="A", record=record)

    seen = {}

    def fake_get_or_create(mid):
        return {"chat_history": [{"userQuery": "earlier", "answer": "prior-answer"}]}

    def fake_update(mid, **kwargs):
        seen["updated"] = mid

    monkeypatch.setattr(acs, "get_or_create_memory", fake_get_or_create)
    monkeypatch.setattr(acs, "update_memory", fake_update)

    acs.run_agent_chat(
        user_input="follow up",
        memory_id="mem-1",
        use_persistent_memory=True,
    )
    # chat_history sourced from OpenSearch memory_doc
    assert {"role": "user", "content": "earlier"} in record[0]["chat_history"]
    assert seen.get("updated") == "mem-1"
    # session store left untouched
    assert sm.get_session_history("mem-1") == []


def test_stream_session_memory_preserved_when_persistent_off(monkeypatch):
    """Streaming twin: turn 2 over the same thread sees turn-1 context with
    persistence OFF."""
    record = []

    def fake_stream(query, *, chat_history=None, thread_id=None, **kwargs):
        record.append({"chat_history": list(chat_history or []), "thread_id": thread_id})
        yield {"event": "status", "data": {"stage": "orchestrate"}}
        yield {
            "event": "completed",
            "data": {"final_answer": "STREAM-ANSWER", "thread_id": thread_id,
                     "available_skills": [], "route_trace": {}},
        }

    monkeypatch.setattr(acs, "stream_agent_query_events", fake_stream)

    list(acs.stream_agent_chat_events(
        user_input="first question", thread_id="s-stream", use_persistent_memory=False))
    events2 = list(acs.stream_agent_chat_events(
        user_input="second question", thread_id="s-stream", use_persistent_memory=False))

    turn2_history = record[1]["chat_history"]
    assert {"role": "user", "content": "first question"} in turn2_history
    assert {"role": "assistant", "content": "STREAM-ANSWER"} in turn2_history
    # a session-scoped memory_saved event is emitted
    saved = [e for e in events2 if e.get("event") == "memory_saved"]
    assert saved and saved[0]["data"].get("scope") == "session"


# --- session-level file tracking (uploads carry across turns) ----------------

def test_session_files_round_trip_and_dedup():
    sm.append_session_files("t1", ["a", "b", "a"])
    sm.append_session_files("t1", ["b", "c"])
    assert sm.get_session_files("t1") == ["a", "b", "c"]   # deduped, order preserved
    assert sm.get_session_files("unknown") == []
    sm.append_session_files(None, ["x"])
    assert sm.get_session_files(None) == []                 # falsy thread no-op
    sm.clear_session("t1")
    assert sm.get_session_files("t1") == []


def test_uploaded_files_carry_to_later_turns(monkeypatch):
    """Files attached on one turn stay accessible on later turns of the SAME session,
    even when the later turn attaches none (the prototype consumes attachments per turn)."""
    rec = []

    def fake(query, *, chat_history=None, thread_id=None, input_file_ids=None, **kw):
        rec.append({"file_ids": list(input_file_ids or []), "thread_id": thread_id})
        return {"final_answer": "ok", "thread_id": thread_id, "available_skills": [], "route_trace": {}}

    monkeypatch.setattr(acs, "run_agent_query", fake)

    # turn 1: attach the shapefile components
    acs.run_agent_chat(user_input="use these", thread_id="s1",
                       file_ids=["file_shp", "file_shx", "file_dbf"], use_persistent_memory=False)
    assert set(rec[0]["file_ids"]) == {"file_shp", "file_shx", "file_dbf"}

    # turn 2: "execute it" with NO new attachment -> session still has the files
    acs.run_agent_chat(user_input="execute it", thread_id="s1", use_persistent_memory=False)
    assert set(rec[1]["file_ids"]) == {"file_shp", "file_shx", "file_dbf"}

    # turn 3: attaching a new file unions with the carried ones
    acs.run_agent_chat(user_input="and this", thread_id="s1", file_ids=["file_new"], use_persistent_memory=False)
    assert set(rec[2]["file_ids"]) == {"file_shp", "file_shx", "file_dbf", "file_new"}

    # a DIFFERENT session does not see them
    acs.run_agent_chat(user_input="hi", thread_id="s2", use_persistent_memory=False)
    assert rec[3]["file_ids"] == []


# --- opengeodata results surfaced as structured JSON objects in the response ---

def test_extract_opengeodata_results_projects_only_opengeodata():
    result = {"orchestration_result": {"evidence": [
        {"doc_id": "og1", "source": "opengeodata", "element_type": "opengeodata", "title": "US Dams",
         "url": "https://doi.org/x", "bbox": [-90, 40, -88, 42], "provider": "USGS"},
        {"doc_id": "kb1", "source": "keyword", "element_type": "dataset", "title": "Internal DS"},
    ]}}
    ogd = acs._extract_opengeodata_results(result)
    assert [d["doc_id"] for d in ogd] == ["og1"]                       # internal KB hit excluded
    assert ogd[0]["title"] == "US Dams" and ogd[0]["bbox"] == [-90, 40, -88, 42]
    assert acs._extract_opengeodata_results({}) == []
    assert acs._extract_opengeodata_results(None) == []


def test_run_agent_chat_surfaces_opengeodata_results(monkeypatch):
    """The client response exposes OpenGeoData hits as structured JSON objects projected from the
    run's evidence, alongside the markdown answer."""
    def fake(query, *, chat_history=None, thread_id=None, **kwargs):
        return {
            "final_answer": "See [US Dams](https://doi.org/x).",
            "thread_id": thread_id, "available_skills": [], "route_trace": {},
            "orchestration_result": {"evidence": [
                {"doc_id": "og1", "source": "opengeodata", "element_type": "opengeodata",
                 "title": "US Dams", "url": "https://doi.org/x", "provider": "USGS"},
                {"doc_id": "kb1", "source": "semantic", "element_type": "dataset", "title": "KB DS"},
            ]},
        }
    monkeypatch.setattr(acs, "run_agent_query", fake)
    resp = acs.run_agent_chat(user_input="open datasets for dams", thread_id="s1",
                              use_persistent_memory=False)
    ogd = resp["opengeodata_results"]
    assert [d["doc_id"] for d in ogd] == ["og1"] and ogd[0]["url"] == "https://doi.org/x"
