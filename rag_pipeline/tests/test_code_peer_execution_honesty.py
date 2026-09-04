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
