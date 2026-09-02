"""The conversation remembers what it DID, not just what it said.

From a live failure: turn 1 computed a clay embedding whose result carried
`pixel_ground_m`; turn 2 asked "do you use the original resolution of clay or do you
downsample it". The supervisor's decision payload is built from per-turn state, so it reported
has_evidence=False / has_analysis=False / artifacts_produced=[] and routed to `search` —
correct, given what it was shown. Forty-nine keyword searches later the payload exceeded the
model's context window and the turn died with a 400. The answer had been in hand all along.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime import session_memory as sm
from agent_runtime.supervisor import graph as g

CLAY_RESULT = {
    "model": "clay", "dim": 1024, "file_id": "file_d0bb",
    "provenance": {"model": "clay", "scale_m": 10, "pixel_ground_m": 10, "year": 2025},
    "map_layer": {"url": "/f/1", "label": "clay embedding (PCA-RGB)"},
}


def _real_artifacts():
    """Built through extract_search_artifacts, NOT hand-written.

    The first version of these tests invented the shape ({"name", "output"}) and passed while
    the feature was inert against production artifacts, which use
    {"name", "tool_call_id", "content"} with content as a JSON *string*. Going through the
    real extractor is what makes this a contract test instead of a mirror of my assumptions.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    from agent_runtime.runtime_utils import extract_search_artifacts

    call = AIMessage(content="", tool_calls=[{
        "name": "embed_region", "id": "c1",
        "args": {"lon": -88.192988, "lat": 40.115105, "model": "clay",
                 "start": "2025-03-01", "end": "2025-05-01"}}])
    result = ToolMessage(content=json.dumps(CLAY_RESULT), name="embed_region", tool_call_id="c1")
    return extract_search_artifacts({"messages": [call, result]})


CLAY_TURN = _real_artifacts()


@pytest.fixture(autouse=True)
def clean():
    sm.reset_all()
    yield
    sm.reset_all()


def test_a_call_and_its_result_become_one_row():
    rows = g._ledger_rows(CLAY_TURN)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["tool"] == "embed_region"
    assert row["args"]["model"] == "clay" and row["args"]["start"] == "2025-03-01"
    assert row["map_layer"] == "clay embedding (PCA-RGB)"
    assert row["file_id"] == "file_d0bb"


def test_the_row_carries_the_answer_to_the_follow_up():
    """`pixel_ground_m` is what "original resolution or downsampled?" is asking for."""
    facts = g._ledger_rows(CLAY_TURN)[0]["facts"]
    assert facts["pixel_ground_m"] == 10
    assert facts["scale_m"] == 10 and facts["model"] == "clay" and facts["dim"] == 1024


def test_provenance_is_flattened_into_the_row():
    """rs-embed nests the fields that say what a number MEANS under `provenance`."""
    rows = g._ledger_rows({"tool_results": [{
        "name": "embed_region", "tool_call_id": "c1",
        "content": json.dumps({"provenance": {"scale_m": 30, "pixel_ground_m": 30}})}]})
    assert rows[0]["facts"] == {"scale_m": 30, "pixel_ground_m": 30}


def test_the_decider_is_told_what_the_conversation_already_did():
    g._record_actions({"thread_id": "t1"}, CLAY_TURN)
    state = {"thread_id": "t1", "query": "what resolution was that", "evidence": [],
             "analysis_results": None, "code_result": None, "step": 0}
    payload = g._distill(state, for_decision=True)
    # Per-turn state still reports an empty conversation — that is the bug being compensated.
    assert payload["has_evidence"] is False and payload["has_analysis"] is False
    prior = payload["prior_turns_in_this_conversation"]
    assert prior[0]["facts"]["pixel_ground_m"] == 10
    assert "done" in payload["available_actions"]     # …and it may act on it
    # The note must warn against blind reuse, or this becomes a staleness bug.
    assert "NOT as inputs to reuse blindly" in payload["prior_turns_note"]


def test_the_current_turn_is_not_in_its_own_prior_context():
    """_record_actions runs at synthesis; the decision runs before it, every step."""
    state = {"thread_id": "t1", "query": "q", "evidence": [], "step": 0}
    assert "prior_turns_in_this_conversation" not in g._distill(state, for_decision=True)
    g._record_actions(state, CLAY_TURN)
    assert g._distill(state, for_decision=True)["prior_turns_in_this_conversation"]


def test_the_client_payload_is_not_burdened_with_it():
    g._record_actions({"thread_id": "t1"}, CLAY_TURN)
    state = {"thread_id": "t1", "query": "q", "evidence": [], "step": 0}
    assert "prior_turns_in_this_conversation" not in g._distill(state)


def test_the_ledger_stays_small_enough_to_inject():
    """It is injected into a routing decision; a big one recreates the failure it prevents."""
    for _ in range(6):
        g._record_actions({"thread_id": "t1"}, CLAY_TURN)
    rows = sm.get_session_actions("t1")
    assert len(json.dumps(rows)) < 4000, len(json.dumps(rows))


def test_long_values_are_truncated():
    rows = g._ledger_rows({"tool_calls": [{"name": "x", "args": {"query": "z" * 500}}]})
    assert len(rows[0]["args"]["query"]) <= g._LEDGER_VALUE_CHARS + 1


def test_only_curated_fields_are_kept():
    """A dumped tool result would be the context problem again, in a new place."""
    rows = g._ledger_rows({"tool_results": [{
        "name": "embed_region", "tool_call_id": "c1", "content": json.dumps({
            "model": "clay", "scale_m": 10,
            "vectors": list(range(1000)), "debug": {"param_absmax": 3.2},
            "stdout": "x" * 5000})}]})
    assert set(rows[0]["facts"]) == {"model", "scale_m"}
    assert "vectors" not in json.dumps(rows[0]) and "stdout" not in json.dumps(rows[0])


def test_the_store_is_bounded_and_keeps_the_newest(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_MEMORY_MAX_ACTIONS", "5")
    sm.append_session_actions("t1", [{"tool": f"t{i}"} for i in range(20)])
    kept = sm.get_session_actions("t1")
    assert len(kept) == 5 and kept[-1]["tool"] == "t19"    # a follow-up is about recent work


def test_no_thread_id_is_a_no_op():
    g._record_actions({}, CLAY_TURN)
    assert sm.get_session_actions(None) == []
    assert g._distill({"query": "q", "evidence": [], "step": 0}, for_decision=True) is not None


def test_clearing_a_session_clears_its_actions():
    g._record_actions({"thread_id": "t1"}, CLAY_TURN)
    sm.clear_session("t1")
    assert sm.get_session_actions("t1") == []


def test_a_broken_ledger_never_breaks_routing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(sm, "get_session_actions", boom)
    monkeypatch.setattr(sm, "append_session_actions", boom)
    state = {"thread_id": "t1", "query": "q", "evidence": [], "step": 0}
    g._record_actions(state, CLAY_TURN)          # must not raise
    assert g._distill(state, for_decision=True)["has_evidence"] is False


# --- the note the ANSWERING model reads -------------------------------------

def test_facts_are_phrased_in_the_words_a_user_would_use():
    """Given `scale_m=10` the model still answered "the available evidence does not specify
    the ground-resolution" — it did not connect the field name to the question. Spelling it
    out is what turned that into "generated at a 10-meter ground resolution"."""
    note = g._prior_actions_note([{"tool": "embed_region", "args": {"model": "gse"},
                                   "facts": {"model": "gse", "scale_m": 10, "year": 2022}}])
    assert "ground resolution" in note and "10 m per pixel" in note
    assert "do not re-derive it" in note          # and says not to go looking for it again


def test_an_unmapped_fact_still_appears():
    note = g._prior_actions_note([{"tool": "x", "facts": {"nodata_fraction": 0.0}}])
    assert "nodata_fraction=0.0" in note


def test_no_rows_means_no_note():
    assert g._prior_actions_note([]) is None


def test_facts_are_found_in_embed_regions_real_nesting():
    """embed_region returns {compute, embedding_package, map_layer, models:[{provenance}]} —
    the facts sit TWO levels down. Reading only the top level found nothing, so the ledger
    recorded a bare `embed_region` row and the follow-up still answered "the evidence does not
    provide specific details"."""
    payload = {
        "compute": "get_embedding", "ok": True,
        "map_layer": {"label": "clay embedding (PCA-RGB)", "url": "/x"},
        "models": [{"model": "clay", "dim": 1024, "grid": [52, 52],
                    "provenance": {"image_size": 256, "patch_size": 8,
                                   "source": "sentinel-2", "normalization": "raw"}}],
    }
    row = g._ledger_rows({"tool_results": [
        {"name": "embed_region", "tool_call_id": "c1", "content": json.dumps(payload)}]})[0]
    assert row["facts"]["model"] == "clay" and row["facts"]["dim"] == 1024
    # An on-the-fly model has no scale_m at all; this is what answers the resolution question.
    assert row["facts"]["image_size"] == 256 and row["facts"]["patch_size"] == 8
    note = g._prior_actions_note([row])
    assert "RESAMPLED" in note


# --- context robustness ------------------------------------------------------

def test_the_rendered_ledger_is_capped_in_characters_not_just_rows():
    """It exists because a turn overflowed the context window; it must not be able to cause
    that itself. 25 rows of long arguments would otherwise reach several thousand tokens."""
    fat = [{"tool": f"t{i}", "args": {"query": "z" * 78, "place": "y" * 78},
            "facts": {"source": "s" * 78}} for i in range(40)]
    kept = g._budgeted(fat)
    assert len(json.dumps(kept)) <= g._LEDGER_MAX_CHARS + 400   # one row may straddle the line
    assert len(kept) < len(fat)
    assert kept[-1]["tool"] == "t39"          # the NEWEST rows survive; oldest drop first


def test_a_small_ledger_is_untouched_by_the_budget():
    rows = [{"tool": "admin_boundary", "facts": {"geoid": "17019"}}]
    assert g._budgeted(rows) == rows


def test_one_huge_row_still_renders():
    """Never return nothing: a single oversized row is better than silence."""
    assert len(g._budgeted([{"tool": "x", "args": {"q": "z" * 5000}}])) == 1


def test_every_peer_and_the_router_see_the_ledger():
    """The search peer starts on a fresh thread each turn, so it was the one structurally
    incapable of knowing the answer was already in hand — it searched SoilGrids for soil clay
    while scale_m sat in the previous turn's result. Pin every exposure point."""
    import inspect

    from agent_runtime.supervisor import graph

    for fn in (graph.default_search_fn, graph.default_analyze_fn, graph.default_code_fn):
        assert "_prior_actions_note(_prior_actions(state))" in inspect.getsource(fn), fn.__name__
    # the router gets the structured rows; synthesis renders the note AND hands the same
    # lines to the grounding auditor as part of the execution record.
    src = inspect.getsource(graph)
    assert "_budgeted(_prior_actions(state))" in src
    syn = inspect.getsource(graph.build_supervisor_graph)
    # Synthesis and the auditor share ONE rendering. Delivery itself is asserted behaviourally
    # by test_the_note_reaches_the_answerer_* — a grep cannot tell "wired" from "wired to a
    # channel that discards it", which is exactly how the chat_history route shipped broken.
    assert "_ledger_text = " in syn
    assert '"prior_actions": _ledger_text' in syn
    assert "do_synthesize(q, evidence, ar, cr, _history, _note)" in syn


# --- the ledger must reach the grounding auditor, not just the answering model -------------
#
# Observed live (Champaign County tracts, unified peer): turn 2 embedded with gse, turn 3 asked
# "what parameters were used?". The answer correctly read 64 dims / 7.645 m per pixel off the
# ledger — and then the grounding caveat appended "may not be fully supported by the retrieved
# evidence (severity: high)". The answerer had the ledger; the auditor did not. The user saw the
# feature accuse itself of hallucinating.

def test_the_auditor_is_given_the_same_earlier_turn_records_as_the_answerer():
    from agent_runtime.evidence_quality import _format_execution_context

    lines = g._ledger_lines(g._ledger_rows(CLAY_TURN))
    assert lines, "ledger produced no lines to hand over"
    rendered = _format_execution_context({"prior_actions": lines})
    assert "earlier turns" in rendered.lower()
    # the values a follow-up would quote must survive into the auditor's record
    assert "1024" in rendered and "clay" in rendered


def test_a_cross_turn_number_is_not_flagged_as_unsupported():
    """The deterministic reconciliation must see ledger values, not only this turn's output."""
    audit = {"severity": "high", "hallucination_detected": True,
             "issues": [{"claim": "the embeddings have 1024 dimensions",
                         "reason": "not supported by the retrieved evidence"}]}
    lines = g._ledger_lines(g._ledger_rows(CLAY_TURN))
    # Without the ledger the issue survives...
    kept = g._reconcile_audit_with_artifacts(
        dict(audit), [], execution_context={"analysis_results": None, "code_result": None})
    assert (kept or {}).get("issues"), "control: should still be flagged with no ledger"
    # ...with it, the number is found in the execution record and the caveat is dropped.
    reconciled = g._reconcile_audit_with_artifacts(
        dict(audit), [], execution_context={"analysis_results": None, "code_result": None,
                                            "prior_actions": lines})
    assert not g._audit_flagged(reconciled), reconciled


def test_a_map_layer_from_an_earlier_turn_still_counts_as_delivered():
    """The map is persistent: a layer added in turn 2 is still on screen in turn 4."""
    lines = g._ledger_lines(g._ledger_rows(CLAY_TURN))
    assert g._map_layer_was_delivered({"prior_actions": lines})
    assert not g._map_layer_was_delivered({"analysis_results": {"note": "nothing mapped"}})


def test_a_genuinely_invented_number_is_still_flagged():
    """The widened record must not become a blanket amnesty."""
    audit = {"severity": "high", "hallucination_detected": True,
             "issues": [{"claim": "the study covered 4096 counties",
                         "reason": "no evidence for this figure"}]}
    lines = g._ledger_lines(g._ledger_rows(CLAY_TURN))
    kept = g._reconcile_audit_with_artifacts(
        dict(audit), [], execution_context={"prior_actions": lines})
    assert g._audit_flagged(kept), "an invented figure must survive reconciliation"


# --- the note must actually ARRIVE at the answering model ---------------------------------
#
# It used to be prepended as chat_history item 0, and _format_chat_history renders `[-8:]` then
# tail-truncates at 4000 chars — both trims cut exactly where it sat. Measured: present at 2-7
# history items, ABSENT at 8+, and absent with 5 large items. The auditor still received it, so
# the two consumers that _prior_actions_note's own docstring says must agree disagreed on every
# conversation longer than seven exchanges. The browser check that passed had 2 history items.
#
# These tests assert DELIVERY, not source text. The previous grep-style assertion could not
# distinguish "wired" from "wired to a channel that discards it".

def _long_history(n, filler=60):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"turn {i} " + "x" * filler} for i in range(n)]


def _run_with_history(history, rows_turn):
    """Run a full supervisor turn on a thread that already has ledger rows, capturing synthesis."""
    captured = {}

    def _synth(q, ev, ar, cr, ch=None, pa=None):
        captured["chat_history"], captured["note"] = ch, pa
        return "answer"

    g._record_actions({"thread_id": "t-deliver"}, rows_turn)
    graph = g.build_supervisor_graph(
        search_fn=lambda q, s: [], analyze_fn=lambda *a: {"summary": "s"},
        synthesize_fn=_synth, decide_fn=lambda *a, **k: ("done", "why"),
        do_rerank=False, do_audit=False)
    graph.invoke({"query": "what resolution was that?", "thread_id": "t-deliver",
                  "chat_history": history, "evidence": [], "actions": []})
    return captured


@pytest.mark.parametrize("n_items", [2, 7, 8, 12, 20])
def test_the_note_reaches_the_answerer_however_long_the_conversation(n_items):
    """The regression that shipped: this fails at n_items >= 8 before the fix."""
    cap = _run_with_history(_long_history(n_items), CLAY_TURN)
    assert cap["note"], f"no ledger note delivered at {n_items} history items"
    assert "clay" in cap["note"], cap["note"]


def test_a_large_note_is_not_truncated_away_by_the_history_budget():
    cap = _run_with_history([{"role": "user", "content": "y" * 900} for _ in range(5)], CLAY_TURN)
    assert cap["note"] and "clay" in cap["note"]


def test_the_note_is_not_smuggled_through_chat_history():
    """It must travel as its own argument, so no history trim can reach it."""
    cap = _run_with_history(_long_history(4), CLAY_TURN)
    hist = cap["chat_history"] or []
    assert not any(g._LEDGER_HEADING in str(i.get("content", "")) for i in hist if isinstance(i, dict))


def test_the_answerer_and_the_auditor_see_the_same_lines():
    """One rendering, two consumers — the property the whole feature rests on."""
    from agent_runtime.evidence_quality import _format_execution_context

    rows = g._ledger_rows(CLAY_TURN) * 12          # past the auditor's old 2200-char cut
    text = "\n".join(g._ledger_lines(rows))
    assert len(text) > 2200, "make the fixture bigger; this must exceed the old cap"
    assert text in g._prior_actions_note(rows)
    assert text in _format_execution_context({"prior_actions": text})


def test_the_synthesizer_prompt_carries_the_note_and_names_the_section():
    calls = []

    class _Rec:
        def invoke(self, prompt):
            calls.append(prompt)
            return "ok"

    note = g._prior_actions_note(g._ledger_rows(CLAY_TURN))
    g.default_synthesize_fn(llm=_Rec())("q", [], None, None, None, note)
    assert g._LEDGER_HEADING in calls[0]
    assert "8. EARLIER-TURN TOOL RECORDS" in calls[0], "rule 8 missing from SYNTHESIS_PROMPT"


def test_a_custom_five_argument_synthesize_fn_still_works():
    """`lambda *a` doubles are the common shape; a 6th POSITIONAL arg keeps them working."""
    graph = g.build_supervisor_graph(
        search_fn=lambda q, s: [], synthesize_fn=lambda *a: "ans",
        decide_fn=lambda *a, **k: ("done", "why"), do_rerank=False, do_audit=False)
    out = graph.invoke({"query": "q", "thread_id": "t-arity", "actions": [],
                        "evidence": [{"doc_id": "d1", "title": "t", "contents": "c"}]})
    assert out["final_answer"] == "ans"
