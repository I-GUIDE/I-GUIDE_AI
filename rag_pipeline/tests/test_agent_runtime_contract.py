"""Characterization tests locking the public agent-runtime contract.

These stub the orchestrator-invocation seam (no real LLM or search backends)
so they exercise the *contract assembly* in ``graph_runtime`` -- the response
dict keys and the streaming events -- which must survive the LangGraph
migration.  Intermediate SSE event *names* are allowed to change across the
migration; what is asserted here is:

* ``run_agent_query`` returns the documented top-level keys + final answer.
* ``stream_agent_query_events`` reaches a terminal ``completed`` event whose
  payload carries the response, and emits ordered execution-state stages.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import agent_runtime.graph_runtime as gr


# ---------------------------------------------------------------------------
# Canned orchestrator result (shape returned by create_agent: {"messages": [...]})
# ---------------------------------------------------------------------------

FINAL_ANSWER = "Here are the flood datasets you asked about."


def _human(content):
    return SimpleNamespace(content=content, type="human", tool_calls=[])


def _ai(content, tool_calls=None):
    return SimpleNamespace(content=content, type="ai", tool_calls=tool_calls or [])


def _tool(name, content, call_id):
    return SimpleNamespace(content=content, name=name, tool_call_id=call_id, type="tool", tool_calls=[])


def _canned_orchestration_result():
    return {
        "messages": [
            _human("What datasets exist for floods?"),
            _ai(
                "",
                tool_calls=[
                    {"name": "search_agent_evidence", "args": {"query": "floods"}, "id": "c1"}
                ],
            ),
            _tool("search_agent_evidence", '{"search_agent_summary": "found 2 docs"}', "c1"),
            _ai(FINAL_ANSWER),
        ]
    }


@pytest.fixture()
def stub_orchestrator(monkeypatch):
    """Replace the orchestrate-node invocation seam with canned data.

    The legacy arm now lives in agent_runtime.legacy.orchestration, which imports these
    functions into its namespace, so they are patched there.
    """
    import agent_runtime.legacy.orchestration as lo

    # These tests characterize the agents-as-tools orchestrate path; the supervisor
    # path is now the default, so pin it off here.
    monkeypatch.setenv("AGENT_SUPERVISOR", "0")

    def fake_collect(**kwargs):
        return [
            SimpleNamespace(name="search_agent_evidence"),
            SimpleNamespace(name="analysis_agent_answer"),
        ]

    def fake_build(**kwargs):
        return object()

    def fake_invoke(*args, **kwargs):
        return _canned_orchestration_result()

    monkeypatch.setattr(lo, "collect_orchestration_tools", fake_collect)
    monkeypatch.setattr(lo, "build_orchestrator_agent_executor", fake_build)
    monkeypatch.setattr(lo, "invoke_agent_with_payload_fallback", fake_invoke)
    return None


# ---------------------------------------------------------------------------
# Non-streaming contract
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self, text):
        self._text = text

    def invoke(self, messages):
        return SimpleNamespace(content=self._text)


def test_triage_fast_paths_trivial_queries():
    from agent_runtime.orchestrator_graph import is_trivial_query

    assert is_trivial_query("hi")
    assert is_trivial_query("Hello!")
    assert is_trivial_query("what can you do?")
    assert not is_trivial_query("What datasets exist for floods?")
    assert not is_trivial_query("find crime hotspots in Chicago")


def test_fast_path_answers_without_orchestrator(monkeypatch):
    import agent_runtime.strategy as strat

    # orchestrate_node resolves the path via the strategy registry; if it ran, this
    # blows up — proving the trivial query was fast-pathed and never orchestrated.
    def explode(*args, **kwargs):
        raise AssertionError("orchestrate path must not run for a greeting")

    monkeypatch.setattr(strat, "get_orchestration_strategy", explode)

    result = gr.run_agent_query("hi", llm=_FakeLLM("Hello! I'm the I-GUIDE assistant."))
    assert result["final_answer"].startswith("Hello!")
    assert result["orchestration_result"] is None


def test_run_agent_query_response_contract(stub_orchestrator):
    result = gr.run_agent_query("What datasets exist for floods?")

    # Documented top-level keys
    assert "orchestration_result" in result
    assert "route_trace" in result
    assert "available_skills" in result
    assert result.get("final_answer") == FINAL_ANSWER
    # checkpointer is set by default -> a thread id is always resolved
    assert isinstance(result.get("thread_id"), str) and result["thread_id"]

    # route_trace is a dict that reflects the called tool
    route_trace = result["route_trace"]
    assert isinstance(route_trace, dict)
    assert "route" in route_trace
    assert "search_agent_evidence" in (route_trace.get("called_tools") or [])


# ---------------------------------------------------------------------------
# Streaming contract
# ---------------------------------------------------------------------------

def _collect_events(query="What datasets exist for floods?"):
    return list(gr.stream_agent_query_events(query))


def test_stream_reaches_terminal_completed_with_payload(stub_orchestrator):
    events = _collect_events()
    names = [e["event"] for e in events]

    assert "completed" in names, f"no terminal completed event; saw {names}"
    completed = [e for e in events if e["event"] == "completed"][-1]["data"]
    assert completed.get("final_answer") == FINAL_ANSWER
    assert "route_trace" in completed
    assert "available_skills" in completed
    assert isinstance(completed.get("thread_id"), str) and completed["thread_id"]

    # final_answer event is emitted with the answer
    final_events = [e for e in events if e["event"] == "final_answer"]
    assert final_events and final_events[-1]["data"].get("answer") == FINAL_ANSWER


def test_stream_emits_ordered_execution_state_stages(stub_orchestrator):
    events = _collect_events()
    stages = [e["data"].get("stage") for e in events if e["event"] == "status"]

    # Execution-state references must appear in order (names may evolve, but
    # the lifecycle started -> initialized -> agent started -> agent completed
    # must be observable for the UI to reflect progress).
    for expected in ("started", "initialized", "orchestration_agent_started", "orchestration_agent_completed"):
        assert expected in stages, f"missing stage {expected!r}; saw {stages}"
    assert stages.index("started") < stages.index("orchestration_agent_completed")


def test_stream_emits_graph_node_lifecycle_events(stub_orchestrator):
    events = _collect_events()
    node_stages = {
        e["data"].get("stage")
        for e in events
        if e["event"] in {"node_started", "node_completed"}
    }
    assert "triage" in node_stages
    assert "orchestrate" in node_stages


# ---------------------------------------------------------------------------
# Fix #2/#3: shared, deduplicated evidence store
# ---------------------------------------------------------------------------

def test_search_tool_dedups_against_shared_store(monkeypatch):
    import agent_runtime.graph_nodes as gn

    calls = {"n": 0}

    monkeypatch.setattr(gn, "collect_tools", lambda **k: [])
    monkeypatch.setattr(gn, "build_search_agent_executor", lambda **k: object())

    def fake_invoke(*args, **kwargs):
        calls["n"] += 1
        return {"messages": []}

    def fake_payload(query, search_response, route_trace):
        return {"user_query": query, "search_agent_summary": f"summary::{query}"}

    monkeypatch.setattr(gn, "invoke_agent_with_payload_fallback", fake_invoke)
    monkeypatch.setattr(gn, "build_search_evidence_payload", fake_payload)

    shared = []
    tool = gn.make_search_agent_evidence_tool(
        llm=None,
        verbose=False,
        return_intermediate_steps=False,
        tool_strategy="granular",
        include_mcp_tools=False,
        mcp_modules=None,
        enabled_search_methods=None,
        smart_tool_routing=False,
        forced_intent=None,
        search_invocations=shared,
        thread_id=None,
        checkpointer=None,
    )

    tool.invoke({"query": "Floods in Texas"})
    tool.invoke({"query": "floods in texas"})  # same query (normalized) -> cache hit
    assert calls["n"] == 1, "duplicate query should not re-invoke the search executor"

    tool.invoke({"query": "Droughts in California"})  # new query -> real search
    assert calls["n"] == 2
    assert len(shared) == 2, "shared store should hold one entry per unique query"


# ---------------------------------------------------------------------------
# Phase 1: AGENT_DEV gates detail-tier SSE events
# ---------------------------------------------------------------------------

def test_agent_dev_off_emits_status_only(stub_orchestrator, monkeypatch):
    monkeypatch.delenv("AGENT_DEV", raising=False)
    events = _collect_events()
    names = {e["event"] for e in events}

    # Detail-tier events suppressed...
    assert "tool_call" not in names
    assert "llm_interaction" not in names
    assert "route_trace" not in names
    assert "decision" not in names
    # ...but status + answer references remain.
    assert "completed" in names
    assert "final_answer" in names
    assert "status" in names


def test_agent_dev_on_emits_detail(stub_orchestrator, monkeypatch):
    monkeypatch.setenv("AGENT_DEV", "true")
    events = _collect_events()
    names = {e["event"] for e in events}

    # Dev-tier post-run summary (route trace + routing decision). Per-step
    # tool_call/tool_result/llm_interaction now come from the LIVE callback handler
    # during the run (not a post-run replay), which the stubbed orchestrator here
    # does not exercise.
    assert "route_trace" in names
    assert "decision" in names
    assert "completed" in names


def test_agent_dev_request_flag_overrides_env(stub_orchestrator, monkeypatch):
    # env OFF but per-request flag True -> detail events appear
    monkeypatch.delenv("AGENT_DEV", raising=False)
    names_on = {e["event"] for e in gr.stream_agent_query_events("q", agent_dev=True)}
    assert "route_trace" in names_on and "decision" in names_on

    # env ON but per-request flag False -> detail suppressed
    monkeypatch.setenv("AGENT_DEV", "true")
    names_off = {e["event"] for e in gr.stream_agent_query_events("q", agent_dev=False)}
    assert "route_trace" not in names_off and "decision" not in names_off
    assert "completed" in names_off  # status tier still present


# ---------------------------------------------------------------------------
# Robustness: tool failures return a result (never leave a dangling tool_call)
# ---------------------------------------------------------------------------

def test_search_tool_failure_returns_error_string(monkeypatch):
    import agent_runtime.graph_nodes as gn

    monkeypatch.setattr(gn, "collect_tools", lambda **k: [])
    monkeypatch.setattr(gn, "build_search_agent_executor", lambda **k: object())

    def boom(*args, **kwargs):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(gn, "invoke_agent_with_payload_fallback", boom)

    shared = []
    tool = gn.make_search_agent_evidence_tool(
        llm=None, verbose=False, return_intermediate_steps=False,
        tool_strategy="granular", include_mcp_tools=False, mcp_modules=None,
        enabled_search_methods=None, smart_tool_routing=False, forced_intent=None,
        search_invocations=shared, thread_id=None, checkpointer=None,
    )

    out = tool.invoke({"query": "anything"})  # must NOT raise
    parsed = json.loads(out)
    assert parsed["error"] == "search_agent_failed"
    assert "backend exploded" in parsed["message"]
    assert shared == [], "failed search must not be cached as evidence"


def test_fallback_does_not_retry_on_tool_ordering_400():
    from agent_runtime.executor_factory import invoke_agent_with_payload_fallback

    class _OrderingErrExecutor:
        def __init__(self):
            self.calls = 0

        def invoke(self, payload, config=None):
            self.calls += 1
            raise RuntimeError(
                "Error code: 400 - invalid_request_error: An assistant message with "
                "'tool_calls' must be followed by tool messages responding to each "
                "'tool_call_id'."
            )

    ex = _OrderingErrExecutor()
    with pytest.raises(RuntimeError):
        invoke_agent_with_payload_fallback(
            ex, query="hi", chat_history=None,
            config={"configurable": {"thread_id": "t"}},
        )
    assert ex.calls == 1, "a tool-ordering 400 must not trigger a legacy-payload retry"


# ---------------------------------------------------------------------------
# Phase 2: history self-healing (tool_call / tool-message repair)
# ---------------------------------------------------------------------------

def _ai_tc(call_id, name="search_agent_evidence"):
    return SimpleNamespace(content="", type="ai", tool_calls=[{"name": name, "args": {}, "id": call_id}])


def _toolmsg(call_id, content="{}"):
    return SimpleNamespace(content=content, type="tool", name="search_agent_evidence", tool_call_id=call_id)


def test_repair_drops_dangling_tool_call():
    from agent_runtime.runtime_utils import repair_tool_call_sequence

    msgs = [_human("hi"), _ai_tc("call_X")]  # tool_call never answered
    fixed, changed = repair_tool_call_sequence(msgs)
    assert changed is True
    assert fixed == [msgs[0]]  # dangling assistant message dropped


def test_repair_drops_orphan_tool_message():
    from agent_runtime.runtime_utils import repair_tool_call_sequence

    msgs = [_human("hi"), _toolmsg("call_ghost"), _ai("answer")]  # tool msg with no AI tool_call
    fixed, changed = repair_tool_call_sequence(msgs)
    assert changed is True
    assert _toolmsg("call_ghost") not in fixed
    assert msgs[0] in fixed and msgs[2] in fixed


def test_repair_passes_through_valid_history():
    from agent_runtime.runtime_utils import repair_tool_call_sequence

    msgs = [_human("hi"), _ai_tc("call_A"), _toolmsg("call_A"), _ai("final answer")]
    fixed, changed = repair_tool_call_sequence(msgs)
    assert changed is False
    assert fixed is msgs  # unchanged -> same object


def test_history_repair_middleware_sanitizes_request():
    from agent_runtime.executor_factory import _make_history_repair_middleware

    mw = _make_history_repair_middleware()

    class _Req:
        def __init__(self, messages):
            self.messages = messages

        def override(self, **kw):
            return _Req(kw.get("messages", self.messages))

    captured = {}

    def handler(req):
        captured["messages"] = req.messages
        return "ok"

    dangling = [_human("hi"), _ai_tc("call_X")]
    result = mw.wrap_model_call(_Req(dangling), handler)
    assert result == "ok"
    assert captured["messages"] == [dangling[0]]  # repaired before model call


# ---------------------------------------------------------------------------
# Supervisor arm contract (the legacy tests pin AGENT_SUPERVISOR=0; this guards
# the DEFAULT supervisor arm through build_orchestrator_graph end-to-end).
# ---------------------------------------------------------------------------

@pytest.fixture()
def stub_supervisor(monkeypatch):
    """Replace run_supervisor (and the peer-fn builders) with canned data so the
    supervisor arm of orchestrate_node runs without an LLM/backends.

    orchestrate_node does `from agent_runtime.supervisor_graph import run_supervisor,
    default_*_fn` at call time, so these are patched on the supervisor_graph module.
    """
    import agent_runtime.supervisor_graph as sg

    monkeypatch.setenv("AGENT_SUPERVISOR", "1")

    def fake_run_supervisor(query, **kwargs):
        return {"final_answer": FINAL_ANSWER, "audit": {}, "evidence": [], "actions": ["search", "done"]}

    monkeypatch.setattr(sg, "run_supervisor", fake_run_supervisor)
    monkeypatch.setattr(sg, "default_search_fn", lambda **k: (lambda *a, **kw: []))
    monkeypatch.setattr(sg, "default_analyze_fn", lambda **k: (lambda *a, **kw: {}))
    monkeypatch.setattr(sg, "default_code_fn", lambda **k: (lambda *a, **kw: {}))
    return None


def test_supervisor_arm_response_contract(stub_supervisor):
    result = gr.run_agent_query("What datasets exist for floods?")
    assert "orchestration_result" in result
    assert "route_trace" in result
    assert "available_skills" in result
    assert result.get("final_answer") == FINAL_ANSWER
    assert isinstance(result.get("thread_id"), str) and result["thread_id"]
    # supervisor advertises the peer names
    assert set(result.get("available_agents") or result.get("available_agent_names") or []) >= {"search", "analyze", "code"} \
        or "search" in str(result.get("route_trace"))


def test_supervisor_arm_stream_reaches_completed(stub_supervisor):
    events = list(gr.stream_agent_query_events("What datasets exist for floods?"))
    names = [e["event"] for e in events]
    assert "completed" in names, f"no terminal completed; saw {names}"
    completed = [e for e in events if e["event"] == "completed"][-1]["data"]
    assert completed.get("final_answer") == FINAL_ANSWER
    node_stages = {e["data"].get("stage") for e in events if e["event"] in {"node_started", "node_completed"}}
    assert "orchestrate" in node_stages
