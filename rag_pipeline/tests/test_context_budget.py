"""A ceiling on the outgoing model payload.

Nothing measured it before: a peer thread accumulates every tool result for the life of a
conversation (BoundedInMemorySaver caps THREADS, not messages inside one), and the deployed
default has no fallback, so the overflow reached the user as a raw provider error —
"Input length (66,275) exceeds model's maximum context length (65,536)". One clay turn
reached 199,605.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from agent_runtime import executor_factory as ef


class _Req:
    def __init__(self, messages):
        self.messages = messages

    def override(self, messages):
        return _Req(messages)


def _send(messages, budget, monkeypatch):
    monkeypatch.setattr(ef, "DEFAULT_CONTEXT_BUDGET", budget)
    monkeypatch.delenv(ef.CONTEXT_BUDGET_ENV, raising=False)
    mw = ef._make_context_budget_middleware()
    seen = {}
    mw.wrap_model_call(_Req(list(messages)), lambda r: seen.setdefault("out", r.messages))
    return seen["out"]


def _geojson_blob(tracts=12):
    """A tool result shaped like what this agent actually moves around.

    The fixture used to be "x" * 4000, which is the single most FAVOURABLE content possible for
    a chars/4 estimator: measured, count_tokens_approximately OVERcounts it 2.01x. Real payloads
    go the other way — GeoJSON boundaries undercount 1.72x and per-zone embedding vectors 2.25x
    (o200k_base) — so the budget believes it is under while the provider sees up to 2.25x more.
    A test built on "x" * n can never exercise that, which is why it passed while the estimator
    was the actual overflow cause.
    """
    import json
    import random

    random.seed(7)
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"GEOID": f"170190{i:04d}", "NAME": f"Census Tract {i}"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [-88.3 + random.random() / 100, 40.1 + random.random() / 100]
             for _ in range(40)]]}}
        for i in range(tracts)]})


def _fat_thread(pairs=40, chars=4000, realistic=True):
    body = _geojson_blob() if realistic else "x" * chars
    msgs = [SystemMessage("you are an agent"), HumanMessage("the original question")]
    for i in range(pairs):
        msgs.append(AIMessage(content="", tool_calls=[{"name": "t", "id": f"c{i}", "args": {}}]))
        msgs.append(ToolMessage(content=body, name="t", tool_call_id=f"c{i}"))
    msgs.append(HumanMessage("what resolution was that?"))
    return msgs


def test_the_estimator_undercounts_the_payloads_this_agent_actually_moves():
    """Pins the mechanism, so the budget's headroom is never mistaken for real headroom.

    Not a hypothesis: geospatial tool results tokenize far worse than chars/4 predicts, and the
    budget middleware is built on that estimator. Skipped where tiktoken is unavailable, since
    the point is the comparison against a real tokenizer.
    """
    tiktoken = pytest.importorskip("tiktoken")
    enc = tiktoken.get_encoding("o200k_base")

    blob = _geojson_blob(30)
    approx = count_tokens_approximately([("user", blob)])
    real = len(enc.encode(blob))
    assert approx < real, "GeoJSON must undercount; if this flips, revisit the budget maths"
    assert real / approx > 1.5, f"undercount only {real / approx:.2f}x — recheck the fixture"

    # and the reason the old fixture hid it
    filler = "x" * 4000
    assert count_tokens_approximately([("user", filler)]) > len(enc.encode(filler)), \
        "a filler string OVERcounts, so a thread built from it cannot exercise the undercount"


def test_an_oversized_thread_is_brought_under_budget(monkeypatch):
    msgs = _fat_thread()
    assert count_tokens_approximately(msgs) > 5000
    out = _send(msgs, 5000, monkeypatch)
    assert count_tokens_approximately(out) <= 5000
    assert len(out) < len(msgs)


def test_the_system_prompt_and_the_current_question_always_survive(monkeypatch):
    """Losing either makes the call pointless rather than merely shorter."""
    out = _send(_fat_thread(), 2000, monkeypatch)
    assert any(isinstance(m, SystemMessage) for m in out)
    assert out[-1].content == "what resolution was that?"


def test_the_oldest_messages_go_first(monkeypatch):
    """A follow-up is nearly always about recent work, and the turn ledger re-injects the
    older facts at ~130 tokens instead of the thousands the raw tool results cost."""
    out = _send(_fat_thread(pairs=20), 6000, monkeypatch)
    kept_ids = {getattr(m, "tool_call_id", None) for m in out}
    assert "c0" not in kept_ids            # oldest dropped
    assert "c19" in kept_ids               # newest kept


def test_a_thread_under_budget_is_passed_through_untouched(monkeypatch):
    msgs = [SystemMessage("s"), HumanMessage("hello")]
    assert _send(msgs, 5000, monkeypatch) == msgs


def test_the_trimmed_list_has_no_orphaned_tool_results(monkeypatch):
    """A ToolMessage whose AIMessage tool_call was trimmed away is a 400 on most providers."""
    out = _send(_fat_thread(), 3000, monkeypatch)
    call_ids = {c["id"] for m in out for c in (getattr(m, "tool_calls", None) or [])}
    for m in out:
        tcid = getattr(m, "tool_call_id", None)
        if tcid is not None:
            assert tcid in call_ids, f"orphaned tool result {tcid}"


def test_trimming_can_be_disabled(monkeypatch):
    msgs = _fat_thread()
    monkeypatch.setenv(ef.CONTEXT_BUDGET_ENV, "0")
    mw = ef._make_context_budget_middleware()
    seen = {}
    mw.wrap_model_call(_Req(list(msgs)), lambda r: seen.setdefault("out", r.messages))
    assert len(seen["out"]) == len(msgs)


def test_the_budget_env_tolerates_junk(monkeypatch):
    for junk in ("", "   ", "not-a-number"):
        monkeypatch.setenv(ef.CONTEXT_BUDGET_ENV, junk)
        assert ef._context_budget() == ef.DEFAULT_CONTEXT_BUDGET
    monkeypatch.setenv(ef.CONTEXT_BUDGET_ENV, "12000")
    assert ef._context_budget() == 12000


def test_the_counter_is_the_approximate_one_not_the_model_one():
    """ChatOpenAI.get_num_tokens_from_messages raises NotImplementedError for the AnvilGPT
    model ids this deployment defaults to, so an exact counter breaks the default provider."""
    import inspect

    src = inspect.getsource(ef._make_context_budget_middleware)
    assert "count_tokens_approximately" in src
    assert "get_num_tokens" not in src


def test_it_is_wired_into_every_agent():
    import inspect

    src = inspect.getsource(ef.build_agent_executor)
    assert "_make_context_budget_middleware()" in src
    # repair must run FIRST so the budgeter sees a coherent list
    assert src.index("_make_history_repair_middleware") < src.index("_make_context_budget_middleware")


def test_counting_failures_never_break_the_call(monkeypatch):
    """A trimmer that raises is worse than one that does nothing."""
    import langchain_core.messages.utils as u

    monkeypatch.setattr(ef, "DEFAULT_CONTEXT_BUDGET", 10)
    monkeypatch.delenv(ef.CONTEXT_BUDGET_ENV, raising=False)
    monkeypatch.setattr(u, "count_tokens_approximately",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    msgs = [SystemMessage("s"), HumanMessage("q")]
    mw = ef._make_context_budget_middleware()
    seen = {}
    mw.wrap_model_call(_Req(list(msgs)), lambda r: seen.setdefault("out", r.messages))
    assert seen["out"] == msgs
