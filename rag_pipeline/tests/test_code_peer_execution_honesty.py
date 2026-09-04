"""A call is not a run, a dead end gets an intervention, and a dead peer is not a dead turn.

`result["executed"]` is read downstream as "the code ran", and it was derived from whether
execute_code was CALLED — so a non-zero exit was reported as a success and synthesis described
a failed run as a working one. The sandbox reports failure as DATA (`ok` is computed from
exit_code/timeout/error), so the outcome has to be read out of the payload.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_runtime.supervisor import graph as g


def _exec_result(ok, error="", exit_code=0):
    return {"name": "execute_code", "tool_call_id": "c1",
            "content": json.dumps({"ok": ok, "exit_code": exit_code, "stdout": "",
                                   "stderr": error, "error": error or None})}


# --- 1. a call is not a run ----------------------------------------------------------------

def test_a_failed_run_is_not_reported_as_executed():
    ran, err = g._execution_outcome({"tool_results": [_exec_result(False, "ModuleNotFoundError: pysal", 1)]})
    assert ran is False
    assert "pysal" in err


def test_a_successful_run_is():
    ran, err = g._execution_outcome({"tool_results": [_exec_result(True)]})
    assert ran is True and err == ""


def test_a_failure_followed_by_a_success_counts_as_run():
    """The run/read/fix loop is the intended path — fixing it is success, not failure."""
    ran, _err = g._execution_outcome({"tool_results": [
        _exec_result(False, "NameError", 1), _exec_result(True)]})
    assert ran is True


def test_the_call_check_still_answers_its_own_question():
    """_has_execution_record gates the did-you-run-it retry, which is about a peer that never
    tried. A failed run DID try, so telling it "you did not run it" would be false."""
    arts = {"tool_calls": [{"name": "execute_code"}],
            "tool_results": [_exec_result(False, "boom", 1)]}
    assert g._has_execution_record(arts) is True
    assert g._execution_outcome(arts)[0] is False


def test_an_unparseable_result_is_not_a_success():
    ran, _ = g._execution_outcome({"tool_results": [
        {"name": "execute_code", "content": "<not json>"}]})
    assert ran is False


# --- 2. the dead-end detector reads what execute_code emits ---------------------------------

def test_two_identical_sandbox_failures_are_a_dead_end():
    """The detector already parsed the `ok` key execute_code carries; it was simply never
    wired into this peer."""
    stuck = g._repeatedly_failed_tools({"tool_results": [
        _exec_result(False, "ModuleNotFoundError: pysal", 1),
        _exec_result(False, "ModuleNotFoundError: pysal", 1)]})
    assert "execute_code" in stuck
    assert "pysal" in stuck["execute_code"]


def test_one_failure_is_not_a_dead_end():
    stuck = g._repeatedly_failed_tools({"tool_results": [_exec_result(False, "boom", 1)]})
    assert stuck == {}


# --- 3. a dead peer is not a dead turn ------------------------------------------------------


def test_a_spiralling_code_peer_does_not_kill_the_turn():
    """The real graph, with a peer that raises the way the recursion limit does. code_node had
    no handler, so this propagated out of the supervisor and the user got an SSE error and no
    answer at all — even when the other peers had produced something worth saying."""
    from langgraph.errors import GraphRecursionError

    from agent_runtime.supervisor.graph import run_supervisor
    from rag_pipeline.tests.test_supervisor_graph import _fake_llm, _scripted

    def _spiral(q, ev, st):
        raise GraphRecursionError("Recursion limit of 60 reached")

    state = run_supervisor(
        "write code", llm=_fake_llm,
        decide_fn=_scripted(["code", "done"]),
        search_fn=lambda q, s: [],
        code_fn=_spiral,
        synthesize_fn=lambda q, ev, ar, cr, ch, pa=None: f"final:{(cr or {}).get('error', '')}",
        do_audit=False,
    )
    # The contract is that the TURN survives and says so — not that any particular synthesis
    # path runs. With no evidence and an empty code answer the supervisor legitimately falls to
    # its general-answer route, which is still an answer where there used to be an SSE error.
    assert isinstance(state.get("final_answer"), str) and state["final_answer"]
    assert "GraphRecursionError" in state["code_result"]["error"]
    assert state["code_result"]["executed"] is False


def test_a_working_code_peer_is_unaffected():
    """The guard must not swallow a normal run."""
    from agent_runtime.supervisor.graph import run_supervisor
    from rag_pipeline.tests.test_supervisor_graph import _fake_llm, _scripted

    state = run_supervisor(
        "write code", llm=_fake_llm,
        decide_fn=_scripted(["code", "done"]),
        search_fn=lambda q, s: [],
        code_fn=lambda q, ev, st: {"answer": "code-answer", "executed": True},
        synthesize_fn=lambda q, ev, ar, cr, ch, pa=None: f"final:{(cr or {}).get('answer', '')}",
        do_audit=False,
    )
    assert state["final_answer"] == "final:code-answer"
    assert "error" not in state["code_result"]


# --- the guard follows the TOOL, not the peer ---------------------------------------------
#
# execute_code is bound to the analyze peer as well, and the router sends most code-shaped
# work there — measured live, 7 of 7 turns. A benchmark run returned a Socrata loader as "the
# code you actually ran" with no execute_code record anywhere in the turn: the code peer's
# guard existed, and analyze never consulted it.

class _FakeSession:
    """Enough of PeerSession for the honesty helper."""

    def __init__(self, reply="ran it", tool_results=None):
        self._reply, self._results = reply, tool_results or []
        self.runs = []
        self.turn_artifacts = {"tool_calls": [], "tool_results": []}

    def run(self, text):
        self.runs.append(text)
        self.turn_artifacts = {"tool_calls": [{"name": "execute_code"}],
                               "tool_results": self._results}

        class _R:
            resp = {"messages": []}
        return _R()


def _apply(result, session, prose_key="answer", caps=(), exec_available=True):
    return g._apply_execution_honesty(
        session, result, prose_key=prose_key, exec_available=exec_available,
        caps=list(caps), node="test")


def test_a_shipped_code_block_with_no_run_is_retried(monkeypatch):
    monkeypatch.setattr(g, "extract_final_answer", lambda *a, **k: "ran it", raising=False)
    session = _FakeSession(tool_results=[_exec_result(True)])
    result = {"answer": "Here:\n```python\nprint(1)\n```", "tool_calls": [], "tool_results": []}
    assert _apply(result, session) is True
    assert session.runs and "never run" in session.runs[0]
    assert result["executed"] is True


def test_the_analyze_peer_gets_the_same_guard(monkeypatch):
    """The whole point: same helper, different prose key."""
    monkeypatch.setattr(g, "extract_final_answer", lambda *a, **k: "ran it", raising=False)
    session = _FakeSession(tool_results=[_exec_result(True)])
    result = {"summary": "Here:\n```python\nprint(1)\n```", "tool_calls": [], "tool_results": []}
    assert _apply(result, session, prose_key="summary") is True
    assert result["executed"] is True


def test_an_answer_with_no_code_block_is_left_alone():
    session = _FakeSession()
    result = {"summary": "Twelve counties border Champaign County.",
              "tool_calls": [], "tool_results": []}
    assert _apply(result, session, prose_key="summary") is False
    assert session.runs == []
    assert result["executed"] is False


def test_a_capability_request_suppresses_the_rerun_but_not_the_accounting():
    """A peer that asked for a capability cannot run what it does not have — but `executed` is
    a fact about this turn either way, and that is what was being reported wrongly."""
    session = _FakeSession()
    result = {"answer": "```python\nprint(1)\n```", "tool_calls": [], "tool_results": []}
    assert _apply(result, session, caps=["search"]) is False
    assert session.runs == []
    assert result["executed"] is False


def test_a_failed_run_is_reported_with_its_error():
    session = _FakeSession()
    result = {"answer": "done", "tool_calls": [{"name": "execute_code"}],
              "tool_results": [_exec_result(False, "ModuleNotFoundError: pysal", 1)]}
    _apply(result, session)
    assert result["executed"] is False
    assert "pysal" in result["execution_error"]


def test_a_later_success_clears_a_stale_error():
    session = _FakeSession()
    result = {"answer": "done", "tool_calls": [{"name": "execute_code"}],
              "tool_results": [_exec_result(False, "NameError", 1), _exec_result(True)],
              "execution_error": "NameError"}
    _apply(result, session)
    assert result["executed"] is True
    assert "execution_error" not in result


def test_no_sandbox_means_no_retry():
    session = _FakeSession()
    result = {"answer": "```python\nprint(1)\n```", "tool_calls": [], "tool_results": []}
    assert _apply(result, session, exec_available=False) is False
    assert session.runs == []
