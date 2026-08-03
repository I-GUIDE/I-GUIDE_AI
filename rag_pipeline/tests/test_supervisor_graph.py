"""Tests for the shared-state supervisor-over-peers graph.

Fully stubbed: scripted decider + injected worker fns + a fake str->str LLM
(routed by prompt for rerank/audit). No network, no keys.
"""

from __future__ import annotations

import json

from agent_runtime.supervisor_graph import (
    build_supervisor_graph,
    is_supervisor_enabled,
    run_supervisor,
)

DOCS = [
    {"doc_id": "a", "title": "A", "contents": "alpha"},
    {"doc_id": "b", "title": "B", "contents": "beta"},
    {"doc_id": "c", "title": "C", "contents": "gamma"},
]


def _fake_llm(prompt: str) -> str:
    low = prompt.lower()
    if "you are auditing" in low:
        return json.dumps({"hallucination_detected": False, "severity": "none", "issues": [], "summary": "grounded"})
    if "relevance judge" in low:
        return json.dumps({"ranking": [
            {"doc_id": "b", "score": 0.9}, {"doc_id": "a", "score": 0.5}, {"doc_id": "c", "score": 0.1},
        ]})
    if "no supporting evidence was found" in low:   # the insufficiency composer prompt
        return ""   # simulate an unhelpful model -> caller uses the safe fallback constant
    return "x"


def _scripted(seq):
    box = {"i": 0}

    def decide(state, distilled):
        i = box["i"]
        box["i"] += 1
        return seq[i] if i < len(seq) else "done"

    return decide


def test_supervisor_loops_search_then_analyze_then_synthesize():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "analyze", "done"]),
        search_fn=lambda q, s: list(DOCS),
        analyze_fn=lambda q, ev, st: {"summary": "ran workflow"},   # workflow output, not prose
        synthesize_fn=lambda q, ev, ar, cr, ch: "the answer",       # answer composed separately
        do_rerank=False,
    )
    assert state["actions"] == ["search", "analyze", "done"]
    assert {d["doc_id"] for d in state["evidence"]} == {"a", "b", "c"}
    assert state["analysis_results"] == {"summary": "ran workflow"}
    assert state["final_answer"] == "the answer"
    assert state["audit"]["summary"] == "grounded"  # audit now in synthesize


def test_evidence_accumulates_and_dedups_across_searches():
    calls = {"n": 0}

    def search_fn(q, s):
        calls["n"] += 1
        return DOCS[:2] if calls["n"] == 1 else DOCS[1:]  # overlap on 'b'

    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "search", "analyze", "done"]),
        search_fn=search_fn, analyze_fn=lambda *a: {"summary": "s"},
        synthesize_fn=lambda *a: "ans", do_rerank=False, do_audit=False,
    )
    assert [d["doc_id"] for d in state["evidence"]] == ["a", "b", "c"]  # 'b' not duplicated


def test_max_steps_guard_terminates():
    state = run_supervisor(
        "q", llm=_fake_llm, max_steps=3,
        decide_fn=lambda s, d: "search",  # would loop forever
        search_fn=lambda q, s: [], analyze_fn=lambda *a: {}, synthesize_fn=lambda *a: "",
        do_rerank=False, do_audit=False,
    )
    assert state["actions"][-1] == "done"
    assert len(state["actions"]) <= 4


def test_unproductive_code_repeat_is_blocked():
    """A decider that keeps choosing 'code' must NOT re-run the code peer once it
    has produced a result — the supervisor short-circuits to synthesize."""
    calls = {"code": 0}

    def code_fn(q, ev, st):
        calls["code"] += 1
        return {"answer": "the heatmap", "code_result": {"png": "heatmap.png"}}

    state = run_supervisor(
        "make a heatmap", llm=_fake_llm,
        decide_fn=lambda s, d: "code",  # always wants code (the looping LLM)
        search_fn=lambda q, s: [], code_fn=code_fn,
        synthesize_fn=lambda *a: "final", do_rerank=False, do_audit=False,
    )
    assert calls["code"] == 1  # ran once, not repeatedly
    assert state["actions"] == ["code", "done"]  # back-to-back repeat -> done
    assert state["final_answer"] == "final"


def test_needs_driven_rerun_is_not_blocked_by_repeat_guard():
    """The repeat guard must not interfere with a legitimate needs-driven re-run."""
    calls = {"code": 0}

    def code_fn(q, ev, st):
        calls["code"] += 1
        if calls["code"] == 1:
            return {"needs": ["search"]}  # request evidence -> supervisor re-runs code after
        return {"answer": "done-code", "code_result": {"ok": True}}

    def decide(state, distilled):
        return "code" if not state.get("actions") else "done"

    state = run_supervisor(
        "write code", llm=_fake_llm, decide_fn=decide,
        search_fn=lambda q, s: list(DOCS), code_fn=code_fn,
        synthesize_fn=lambda *a: "final", do_rerank=False, do_audit=False,
    )
    assert calls["code"] == 2  # needs-driven re-run still happened
    assert state["actions"] == ["code", "search", "code", "done"]


def test_search_not_repeated_when_it_returns_nothing():
    """The supervisor must stop routing to search once it returns no results,
    instead of hammering the search agent."""
    calls = {"search": 0}

    def search_fn(q, s):
        calls["search"] += 1
        return []  # knowledge base has nothing for this query

    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=lambda s, d: "search",   # decider keeps wanting search
        search_fn=search_fn, synthesize_fn=lambda *a: "ans",
        do_rerank=False, do_audit=False,
    )
    assert calls["search"] == 1            # ran once, then stopped (not looped)
    assert state["actions"][-1] == "done"
    # nothing retrieved -> the no-grounding guard returns an honest refusal (the injected
    # synthesize_fn is intentionally bypassed) rather than fabricating an answer.
    assert "couldn't find" in state["final_answer"].lower()


def test_exhausted_search_requests_are_dropped_and_loop_terminates():
    """A peer that keeps requesting search after it's exhausted can't loop forever:
    dead search needs are dropped and per-peer runs are capped."""
    calls = {"search": 0, "analyze": 0}

    def search_fn(q, s):
        calls["search"] += 1
        return []

    def analyze_fn(q, ev, st):
        calls["analyze"] += 1
        return {"summary": "need data", "needs": ["search"]}  # always asks for search

    def decide(s, d):
        return "analyze" if (s.get("actions", []).count("analyze") == 0) else "done"

    state = run_supervisor(
        "q", llm=_fake_llm, decide_fn=decide,
        search_fn=search_fn, analyze_fn=analyze_fn, synthesize_fn=lambda *a: "final",
        do_rerank=False, do_audit=False,
    )
    assert calls["search"] <= 2                 # bounded by AGENT_SUPERVISOR_MAX_SEARCHES (not hammered)
    assert calls["analyze"] <= 3                # per-peer run cap honored
    assert len(state["actions"]) < state["max_steps"] + 1  # terminated before the hard cap
    assert state["final_answer"] == "final"


def test_productive_consecutive_searches_still_allowed():
    """Two searches that each add NEW evidence are not blocked (only empty ones are)."""
    seq = [["x1"], ["x2"]]
    box = {"i": 0}

    def search_fn(q, s):
        d = seq[box["i"]] if box["i"] < len(seq) else []
        box["i"] += 1
        return [{"doc_id": v} for v in d]

    state = run_supervisor(
        "q", llm=_fake_llm, decide_fn=_scripted(["search", "search", "done"]),
        search_fn=search_fn, synthesize_fn=lambda *a: "ok", do_rerank=False, do_audit=False,
    )
    assert {d["doc_id"] for d in state["evidence"]} == {"x1", "x2"}  # both searches ran + added


def test_code_peer_feeds_synthesis():
    state = run_supervisor(
        "write code", llm=_fake_llm,
        decide_fn=_scripted(["code", "done"]),
        search_fn=lambda q, s: [],
        code_fn=lambda q, ev, st: {"answer": "code-answer", "code_result": {"x": 1}},
        synthesize_fn=lambda q, ev, ar, cr, ch: f"final:{(cr or {}).get('answer', '')}",
        do_audit=False,
    )
    assert state["code_result"]["answer"] == "code-answer"
    assert state["final_answer"] == "final:code-answer"  # synthesize incorporates code_result


def test_graph_has_peer_nodes_codefn_3arg():
    # CodeFn now takes (query, evidence, state)
    g = build_supervisor_graph(
        decide_fn=_scripted(["done"]), search_fn=lambda q, s: [],
        analyze_fn=lambda *a: {}, code_fn=lambda q, ev, st: None,
        synthesize_fn=lambda *a: "", llm=_fake_llm,
    )
    assert "code" in set(g.get_graph().nodes)


def test_analyze_request_drives_supervisor_routing():
    """analyze signals it needs evidence -> supervisor runs search, then re-runs analyze."""
    calls = {"analyze": 0}

    def analyze_fn(q, ev, st):
        calls["analyze"] += 1
        if calls["analyze"] == 1:
            return {"summary": "insufficient", "needs": ["search"]}  # request evidence
        return {"summary": "complete"}

    def decide(state, distilled):  # only consulted when the needs queue is empty
        return "analyze" if state.get("actions", []).count("analyze") == 0 else "done"

    state = run_supervisor(
        "q", llm=_fake_llm, decide_fn=decide,
        search_fn=lambda q, s: list(DOCS), analyze_fn=analyze_fn,
        synthesize_fn=lambda *a: "final", do_rerank=False, do_audit=False,
    )
    assert calls["analyze"] == 2  # re-ran after its request was fulfilled
    assert state["actions"] == ["analyze", "search", "analyze", "done"]
    assert {d["doc_id"] for d in state["evidence"]} == {"a", "b", "c"}
    assert state["analysis_results"] == {"summary": "complete"}
    assert state["final_answer"] == "final"


def test_code_request_routes_then_reruns():
    """code signals it needs evidence -> supervisor runs search, then re-runs code."""
    calls = {"code": 0}

    def code_fn(q, ev, st):
        calls["code"] += 1
        if calls["code"] == 1:
            return {"needs": ["search"]}  # code needs evidence first
        return {"answer": "code-done", "code_result": {"ok": True}}

    def decide(state, distilled):
        return "code" if state.get("actions", []).count("code") == 0 else "done"

    state = run_supervisor(
        "write code", llm=_fake_llm, decide_fn=decide,
        search_fn=lambda q, s: list(DOCS), code_fn=code_fn,
        synthesize_fn=lambda q, ev, ar, cr, ch: f"final:{(cr or {}).get('answer', '')}",
        do_rerank=False, do_audit=False,
    )
    assert calls["code"] == 2
    assert state["actions"] == ["code", "search", "code", "done"]
    assert state["code_result"]["answer"] == "code-done"
    assert state["final_answer"] == "final:code-done"


def test_decider_uses_chat_history():
    """The supervisor decider sees the conversation; a follow-up like 'show me the code'
    should NOT trigger a fresh search."""
    from agent_runtime.supervisor_graph import default_decide_fn

    captured = {}

    def rec(prompt):
        captured["prompt"] = prompt
        return '{"next": "done", "reason": "already in conversation"}'

    decide = default_decide_fn(llm=rec)
    nxt = decide(
        {"query": "show me the code", "chat_history": [{"role": "assistant", "content": "PRIOR_FIB_CODE"}]},
        {"has_evidence": False, "actions_taken": []},
    )
    assert "PRIOR_FIB_CODE" in captured["prompt"]
    assert "Conversation so far" in captured["prompt"]
    assert nxt == "done"


def test_synthesize_uses_chat_history():
    from agent_runtime.supervisor_graph import default_synthesize_fn

    captured = {}

    def rec(prompt):
        captured["prompt"] = prompt
        return "final"

    out = default_synthesize_fn(llm=rec)(
        "show me the code", [], None, None, [{"role": "assistant", "content": "PRIOR_CODE_X"}]
    )
    assert out == "final"
    assert "PRIOR_CODE_X" in captured["prompt"]


def test_code_worker_does_not_refeed_chat_history(monkeypatch):
    """P1-3: the code peer does NOT re-feed chat_history into its payload (continuity
    is owned by its checkpointed child thread); mirrors the search peer."""
    import agent_runtime.executor_factory as ef
    import agent_runtime.supervisor_graph as sg

    captured = {}
    monkeypatch.setattr(ef, "build_agent_executor", lambda **k: object())

    def fake_invoke(executor, *, query, chat_history, config):
        captured["chat_history"] = chat_history
        return {"messages": []}

    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", fake_invoke)
    hist = [{"role": "user", "content": "H"}]
    sg.default_code_fn()("q", [], {"chat_history": hist})
    assert captured["chat_history"] is None  # not double-fed


def test_code_fn_result_is_flat(monkeypatch):
    """P1-1: code-peer result is flat (no nested raw response object)."""
    import agent_runtime.executor_factory as ef
    import agent_runtime.supervisor_graph as sg

    monkeypatch.setattr(ef, "build_agent_executor", lambda **k: object())
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})
    out = sg.default_code_fn()("q", [], {"thread_id": None, "chat_history": []})
    assert "answer" in out
    assert "code_result" not in out  # the giant raw response is no longer nested


def test_synthesize_prefers_code_answer_text():
    """P1-1: synthesis feeds the code peer's answer text, not a serialized dump."""
    from agent_runtime.supervisor_graph import default_synthesize_fn

    captured = {}

    def rec(prompt):
        captured["prompt"] = prompt
        return "final"

    code_result = {"answer": "RUNNABLE_CODE_SNIPPET", "tool_results": [{"x": "huge" * 500}]}
    out = default_synthesize_fn(llm=rec)("q", [], None, code_result, None)
    assert out == "final"
    assert "RUNNABLE_CODE_SNIPPET" in captured["prompt"]


def test_merge_dedup_collapses_idless_docs_by_content():
    """P1-4: ID-less documents dedup by content hash (not disjoint positional keys)."""
    from agent_runtime.supervisor_graph import _merge_dedup

    existing = [{"title": "T", "contents": "same body"}]
    new = [
        {"title": "T", "contents": "same body"},   # duplicate of existing (no id)
        {"title": "U", "contents": "different"},    # genuinely new
    ]
    merged = _merge_dedup(existing, new)
    assert len(merged) == 2  # the duplicate id-less doc collapsed


def test_initialized_advertises_supervisor_peers(monkeypatch):
    """P1-7: the initialized event advertises the peers that actually run."""
    import agent_runtime.graph_runtime as gr

    monkeypatch.delenv("AGENT_SUPERVISOR", raising=False)

    class _Graph:
        def invoke(self, *a, **k):
            return {
                "final_answer": "x",
                "available_agent_names": ["search", "analyze", "code"],
                "orchestration_result": {"actions": ["done"]},
            }

    monkeypatch.setattr(gr, "build_orchestrator_graph", lambda **k: _Graph())
    events = list(gr.stream_agent_query_events("q", use_supervisor=True))
    init = [e for e in events if (e.get("data") or {}).get("stage") == "initialized"]
    assert init and init[0]["data"]["available_agents"] == ["search", "analyze", "code"]


def test_request_capability_tool_records_needs():
    """The request_capability tool (the model-driven signal) records valid requests."""
    from agent_runtime.supervisor_graph import _make_request_tool

    tool, requests = _make_request_tool()
    tool.invoke({"capability": "search", "reason": "need evidence from KB"})
    tool.invoke({"capability": "bogus"})  # ignored
    assert requests == [{"capability": "search", "reason": "need evidence from KB"}]


def test_rerank_is_bundled_into_search():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "done"]),
        search_fn=lambda q, s: list(DOCS), synthesize_fn=lambda *a: "x",
        do_rerank=True, do_audit=False,
    )
    assert [d["doc_id"] for d in state["evidence"]] == ["b", "a", "c"]  # reranked


def test_distilled_view_is_compact():
    state = run_supervisor(
        "q", llm=_fake_llm,
        decide_fn=_scripted(["search", "analyze", "done"]),
        search_fn=lambda q, s: list(DOCS), analyze_fn=lambda *a: {"summary": "s"},
        synthesize_fn=lambda *a: "ans", do_rerank=False, do_audit=False,
    )
    d = state["distilled"]
    assert d["has_evidence"] and d["document_count"] == 3 and d["answer"] == "ans"
    assert "evidence" not in d and "documents" not in d  # heavy docs not surfaced


def test_graph_has_peer_nodes():
    g = build_supervisor_graph(
        decide_fn=_scripted(["done"]), search_fn=lambda q, s: [],
        analyze_fn=lambda *a: {}, code_fn=lambda q, ev: None,
        synthesize_fn=lambda *a: "", llm=_fake_llm,
    )
    nodes = set(g.get_graph().nodes)
    for n in ("supervisor", "search", "analyze", "code", "synthesize"):
        assert n in nodes


def test_flag_parsing(monkeypatch):
    # Default ON when unset.
    monkeypatch.delenv("AGENT_SUPERVISOR", raising=False)
    assert is_supervisor_enabled() is True
    # Explicit opt-out.
    monkeypatch.setenv("AGENT_SUPERVISOR", "0")
    assert is_supervisor_enabled() is False
    monkeypatch.setenv("AGENT_SUPERVISOR", "false")
    assert is_supervisor_enabled() is False
    # Explicit on.
    monkeypatch.setenv("AGENT_SUPERVISOR", "1")
    assert is_supervisor_enabled() is True


def test_orchestrate_uses_supervisor_by_default(monkeypatch):
    """With AGENT_SUPERVISOR unset, the orchestrate node defaults to the supervisor."""
    import agent_runtime.supervisor_graph as sg
    import agent_runtime.graph_runtime as gr

    monkeypatch.delenv("AGENT_SUPERVISOR", raising=False)
    monkeypatch.setattr(
        sg, "run_supervisor",
        lambda query, **kwargs: {"final_answer": "supervisor answer", "evidence": [], "actions": ["analyze", "done"]},
    )

    result = gr.run_agent_query("find flood datasets for texas and summarize")
    assert result["final_answer"] == "supervisor answer"


# --- P0-2: grounding audit acts on its verdict --------------------------------

def _audit_llm(*, flagged: bool):
    def llm(prompt: str) -> str:
        if "you are auditing" in prompt.lower():
            if flagged:
                return json.dumps({"hallucination_detected": True, "severity": "high",
                                   "issues": [{"claim": "claim X", "reason": "unsupported by the evidence"}],
                                   "summary": "claim X unsupported"})
            return json.dumps({"hallucination_detected": False, "severity": "none", "issues": [], "summary": "grounded"})
        return "x"
    return llm


def test_grounding_audit_appends_caveat_when_flagged():
    # A search populates evidence so the audit actually evaluates (it no-ops on
    # empty evidence).
    state = run_supervisor(
        "q", llm=_audit_llm(flagged=True), decide_fn=_scripted(["search", "done"]),
        search_fn=lambda q, s: list(DOCS), synthesize_fn=lambda *a: "raw answer",
        do_rerank=False, do_audit=True,
    )
    assert "raw answer" in state["final_answer"]
    assert "Grounding check" in state["final_answer"]  # caveat appended -> not cosmetic
    assert state["audit"]["severity"] == "high"


def test_grounding_audit_no_caveat_when_grounded():
    state = run_supervisor(
        "q", llm=_audit_llm(flagged=False), decide_fn=_scripted(["search", "done"]),
        search_fn=lambda q, s: list(DOCS), synthesize_fn=lambda *a: "clean answer",
        do_rerank=False, do_audit=True,
    )
    assert state["final_answer"] == "clean answer"  # unchanged when grounded
    assert "Grounding check" not in state["final_answer"]


def test_audit_surfaced_in_orchestrate_return(monkeypatch):
    """orchestrate_node lifts sup_state['audit'] so it can reach the response."""
    import agent_runtime.supervisor_graph as sg
    import agent_runtime.graph_runtime as gr

    monkeypatch.delenv("AGENT_SUPERVISOR", raising=False)
    monkeypatch.setattr(
        sg, "run_supervisor",
        lambda query, **kwargs: {
            "final_answer": "ans", "evidence": [], "actions": ["code", "done"],
            "audit": {"hallucination_detected": True, "severity": "high", "summary": "bad"},
        },
    )
    result = gr.run_agent_query("plot something")
    assert result["grounding_audit"]["severity"] == "high"


# --- P0-3: route trace is supervisor-aware ------------------------------------

def test_route_trace_supervisor_aware():
    from agent_runtime.runtime_utils import build_orchestration_trace

    sup = {"actions": ["search", "code", "code", "done"], "evidence": [{"doc_id": "a"}],
           "code_result": {"x": 1}, "audit": {"severity": "low"}}
    t = build_orchestration_trace(
        query="q", chat_history=None,
        available_agent_names=["search", "analyze", "code"], orchestration_result=sup,
    )
    assert "search_agent_evidence" in t["called_tools"]  # search ran
    assert "code_agent_answer" in t["called_tools"]       # code ran
    assert t["route"] == "supervisor:search→code"          # distinct peers, in order
    assert t["document_count"] == 1 and t["has_code"] is True
    assert t["supervisor_actions"] == ["search", "code", "code", "done"]


def test_route_trace_legacy_message_shape_still_works():
    from agent_runtime.runtime_utils import build_orchestration_trace
    from types import SimpleNamespace

    legacy = {"messages": [SimpleNamespace(content="hi", type="ai", tool_calls=[])]}
    t = build_orchestration_trace(
        query="q", chat_history=None, available_agent_names=[], orchestration_result=legacy,
    )
    assert t["route"] == "orchestrator_only"  # legacy path unchanged


# --- P0-1: search peer forwards enabled_search_methods ------------------------

def test_search_peer_forwards_enabled_search_methods(monkeypatch):
    import agent_runtime.executor_factory as ef
    from agent_runtime.supervisor_graph import default_search_fn

    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(ef, "build_search_agent_executor", fake_build)
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})

    default_search_fn(enabled_search_methods=["keyword_search"])("q", {"thread_id": None})
    assert captured["enabled_search_methods"] == ["keyword_search"]


# --- P0-4: collect_tools no longer silently defaults to full_pipeline ---------

def test_collect_tools_defaults_to_granular_not_full_pipeline(monkeypatch):
    import agent_runtime.langchain_granular_tools as gt
    import agent_runtime.langchain_quality_tools as qt
    import agent_runtime.skills as sk
    import agent_runtime.langchain_tool as lt
    from agent_runtime.tool_policy import collect_tools

    monkeypatch.setattr(gt, "make_langchain_granular_tools", lambda **k: ["GRANULAR"])
    monkeypatch.setattr(qt, "make_quality_tools", lambda: [])
    monkeypatch.setattr(sk, "make_skill_tools", lambda **k: [])

    def boom():
        raise AssertionError("deprecated full_pipeline rag_tool must not be built by default")

    monkeypatch.setattr(lt, "make_langchain_rag_tool", boom)

    # empty/falsy strategy must resolve to granular, not full_pipeline
    tools = collect_tools(tool_strategy="", include_mcp_tools=False, mcp_modules=None)
    assert tools == ["GRANULAR"]


# --- P2-7: decider uses the fenced-block JSON extractor ----------------------

def test_decider_parses_fenced_json():
    from agent_runtime.supervisor_graph import default_decide_fn

    def llm(prompt):
        return "Here is my choice:\n```json\n{\"next\": \"analyze\", \"reason\": \"run it\"}\n```"

    nxt = default_decide_fn(llm=llm)(
        {"query": "q", "chat_history": []}, {"has_evidence": True, "actions_taken": []}
    )
    assert nxt == "analyze"  # fenced JSON parsed (naive slicing would have failed)


def test_decider_falls_back_when_output_unparseable():
    from agent_runtime.supervisor_graph import default_decide_fn

    def llm(prompt):
        return "I cannot produce JSON right now, sorry."

    nxt = default_decide_fn(llm=llm)(
        {"query": "q", "chat_history": []}, {"has_evidence": False, "actions_taken": []}
    )
    assert nxt == "search"  # deterministic heuristic: no evidence yet -> search


# --- P2-2: analyze peer gets file tools when files are attached ---------------

def test_analyze_peer_gets_file_tools_only_when_files_present(monkeypatch):
    from types import SimpleNamespace

    import agent_runtime.executor_factory as ef
    import agent_runtime.langchain_granular_tools as gt
    import agent_runtime.langchain_file_tools as ft
    import agent_runtime.supervisor_graph as sg

    monkeypatch.delenv("AGENT_CODE_EXEC", raising=False)
    monkeypatch.setattr(gt, "make_langchain_qgis_tools", lambda **k: [])
    monkeypatch.setattr(ft, "make_langchain_file_tools", lambda: [SimpleNamespace(name="read_text_file")])

    captured = {}

    def fake_build(**kwargs):
        captured["tools"] = [getattr(t, "name", "") for t in (kwargs.get("preloaded_tools") or [])]
        return object()

    monkeypatch.setattr(ef, "build_agent_executor", fake_build)
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})

    sg.default_analyze_fn(include_mcp_tools=False, input_file_ids=["file_x"])("q", [], {"thread_id": None})
    assert "read_text_file" in captured["tools"]

    captured.clear()
    sg.default_analyze_fn(include_mcp_tools=False, input_file_ids=None)("q", [], {"thread_id": None})
    assert "read_text_file" not in captured["tools"]


# --- P2-6: the checkpointer is bounded (LRU thread eviction) ------------------

def test_bounded_checkpointer_evicts_least_recently_used_thread():
    from agent_runtime.executor_factory import BoundedInMemorySaver

    saver = BoundedInMemorySaver(max_threads=2)
    for tid in ("t1", "t2"):
        saver.storage[tid]["ns"] = {"cid": tid}
        saver._touch_thread(tid)
    saver.storage["t3"]["ns"] = {"cid": "t3"}
    saver._touch_thread("t3")  # over cap -> evicts t1 (LRU)

    assert "t1" not in saver.storage
    assert "t2" in saver.storage and "t3" in saver.storage


def test_use_supervisor_false_forces_agents_as_tools(monkeypatch):
    """A per-request use_supervisor=False overrides the default and skips the supervisor."""
    from types import SimpleNamespace

    import agent_runtime.legacy.orchestration as lo
    import agent_runtime.supervisor_graph as sg
    import agent_runtime.graph_runtime as gr

    def boom(*a, **k):
        raise AssertionError("run_supervisor should NOT be called when use_supervisor=False")

    monkeypatch.setattr(sg, "run_supervisor", boom)
    monkeypatch.setattr(lo, "collect_orchestration_tools", lambda **k: [])
    monkeypatch.setattr(lo, "build_orchestrator_agent_executor", lambda **k: object())
    monkeypatch.setattr(
        lo, "invoke_agent_with_payload_fallback",
        lambda *a, **k: {"messages": [SimpleNamespace(content="agents-as-tools answer", type="ai", tool_calls=[])]},
    )

    result = gr.run_agent_query("substantive query", use_supervisor=False)
    assert result["final_answer"] == "agents-as-tools answer"


# --- inline image embedding in the final answer ----------------------------

def test_collect_image_artifacts_walks_json_tool_results():
    import json as _json
    from agent_runtime.supervisor_graph import _collect_image_artifacts
    analysis = {"tool_results": [
        {"name": "plot_vector", "content": _json.dumps(
            {"ok": True, "file_id": "f1", "filename": "vector_plot.png",
             "download_url": "/agent/files/f1/download"})},
        {"name": "inspect_vector", "content": _json.dumps({"ok": True, "feature_count": 3})},
    ]}
    code = {"tool_results": [{"name": "execute_code", "content": _json.dumps(
        {"artifacts": [
            {"file_id": "f2", "filename": "result.png", "download_url": "/agent/files/f2/download"},
            {"file_id": "f3", "filename": "out.csv", "download_url": "/agent/files/f3/download"},
        ]})}]}
    imgs = _collect_image_artifacts(analysis, code)
    assert {i["file_id"] for i in imgs} == {"f1", "f2"}  # csv excluded


def test_append_image_embeds_appends_and_dedupes():
    from agent_runtime.supervisor_graph import _append_image_embeds
    imgs = [{"filename": "m.png", "download_url": "/agent/files/f1/download", "file_id": "f1"},
            {"filename": "r.jpg", "download_url": "/agent/files/f2/download", "file_id": "f2"}]
    out = _append_image_embeds("Here you go.", imgs)
    assert "![m.png](/agent/files/f1/download)" in out
    assert "![r.jpg](/agent/files/f2/download)" in out
    # already referenced by exact url (f1) or by file_id as a URL path segment (f2) -> not duplicated
    out2 = _append_image_embeds("![x](/agent/files/f1/download) see http://h/agent/files/f2/download", imgs)
    assert out2.count("/agent/files/f1/download") == 1
    assert out2.count("![r.jpg]") == 0
    # a BARE file_id mention in prose must NOT suppress the embed (image isn't actually shown)
    out3 = _append_image_embeds("the file f2 holds the plot", imgs[1:])
    assert "![r.jpg](/agent/files/f2/download)" in out3
    # no-op safety
    assert _append_image_embeds("x", []) == "x"


# --- hyperlink citations in the synthesized answer (Rule 2) -----------------

def test_element_url_builds_platform_and_external_links(monkeypatch):
    monkeypatch.setenv("FRONTEND_DOMAIN", "https://platform.i-guide.io")
    from agent_runtime.supervisor.evidence_subgraph import _element_url
    # internal knowledge elements -> platform URL (plural; 'code' stays 'code')
    assert _element_url({"element_type": "dataset", "doc_id": "abc"}) == "https://platform.i-guide.io/datasets/abc"
    assert _element_url({"element_type": "code", "doc_id": "c1"}) == "https://platform.i-guide.io/code/c1"
    assert _element_url({"resource-type": "publication", "doc_id": "p1"}).endswith("/publications/p1")
    # OpenGeoData -> its own landing url
    assert _element_url({"element_type": "opengeodata", "url": "https://ext/og"}) == "https://ext/og"
    # no element_type and no url -> no link (synthesizer bolds the title instead)
    assert _element_url({"doc_id": "x"}) == ""
    # FRONTEND_DOMAIN override + trailing-slash handling
    monkeypatch.setenv("FRONTEND_DOMAIN", "https://dev.example/")
    assert _element_url({"element_type": "notebook", "doc_id": "n"}) == "https://dev.example/notebooks/n"


def test_format_documents_emits_url_line(monkeypatch):
    monkeypatch.setenv("FRONTEND_DOMAIN", "https://platform.i-guide.io")
    from agent_runtime.supervisor.evidence_subgraph import _format_documents
    out = _format_documents([{"doc_id": "abc", "title": "Flood DS", "element_type": "dataset", "contents": "d"}])
    assert "url: https://platform.i-guide.io/datasets/abc" in out


def test_opengeodata_hits_carry_landing_url_for_hyperlink():
    """OpenGeoData assets carry links as a {label: url} DICT; _landing_url must extract a landing
    url from it (preferring a non-metadata link) so the doc gets a url the synthesizer can render
    as a hyperlink, mirroring internal KB elements."""
    from agent_runtime.langchain_granular_tools import _landing_url, _normalize_hits
    from agent_runtime.supervisor.evidence_subgraph import _element_url

    links = {"Digital Data": "https://doi.org/10.5066/F7833R62",
             "Original Metadata": "https://data.usgs.gov/meta.xml"}
    assert _landing_url({"links": links}) == "https://doi.org/10.5066/F7833R62"   # non-metadata wins
    # metadata-only -> still returns a link rather than nothing
    assert _landing_url({"links": {"Original Metadata": "https://x/meta.xml"}}) == "https://x/meta.xml"
    # list shape still supported; top-level url/landing_url still preferred
    assert _landing_url({"links": [{"url": "https://l/1"}]}) == "https://l/1"
    assert _landing_url({"url": "https://top"}) == "https://top"

    hit = {"_id": "og-1", "_score": 1.0, "_source": {
        "title": "US Dams", "element_type": "opengeodata", "contents": "inventory", "links": links}}
    doc = _normalize_hits([hit], "opengeodata")[0]
    assert doc["url"] == "https://doi.org/10.5066/F7833R62"
    assert _element_url(doc) == doc["url"]     # surfaced end-to-end for the hyperlink


# --- audit precision: only HIGH severity warns; reconcile is crash-proof ------

def test_medium_severity_does_not_warn_user():
    """Reasonable-elaboration false positives (rated medium by a strict judge) no longer
    surface a caveat — only HIGH, confident factual hallucinations do."""
    from agent_runtime.supervisor.graph import _audit_flagged, _apply_grounding_caveat
    medium = {"hallucination_detected": True, "severity": "medium", "summary": "interpretive over-reach"}
    assert _audit_flagged(medium) is False
    assert _apply_grounding_caveat("the answer", medium) == "the answer"  # unchanged
    high = {"hallucination_detected": True, "severity": "high", "summary": "fabricated statistic"}
    assert _audit_flagged(high) is True
    assert "Grounding check" in _apply_grounding_caveat("the answer", high)


def test_reconcile_audit_tolerates_malformed_issue_strings():
    """A small judge may emit issues as bare strings instead of {claim,reason} dicts;
    reconciliation must degrade gracefully, never crash synthesize."""
    from agent_runtime.supervisor.graph import _reconcile_audit_with_artifacts
    audit = {"hallucination_detected": True, "severity": "high",
             "issues": ["claim X unsupported"], "summary": "x"}
    out = _reconcile_audit_with_artifacts(audit, artifacts=[], execution_context={})
    assert isinstance(out, dict)  # did not raise


# --- conversational / meta requests are answered from history, not refused ----

def test_conversational_request_answered_from_history_not_refused():
    """A meta request ('summarize our discussion') has no retrievable evidence, but the
    conversation IS its grounding — it must be composed from chat_history, not hard-refused
    with the retrieval-failure message."""
    state = run_supervisor(
        "summarize our discussion", llm=_fake_llm,
        decide_fn=lambda s, d: "done",            # straight to synthesize, nothing retrieved
        search_fn=lambda q, s: [],
        synthesize_fn=lambda q, ev, ar, cr, ch: "Here is a summary of our chat.",
        chat_history=[{"role": "user", "content": "what's the risk of aging dams?"}],
        do_rerank=False, do_audit=False,
    )
    assert state["final_answer"] == "Here is a summary of our chat."   # composed from history
    assert "couldn't find" not in state["final_answer"].lower()        # NOT the refusal


def test_cold_query_with_no_evidence_and_no_history_falls_back_to_constant():
    """The honest no-grounding reply still fires for a true retrieval failure (nothing retrieved
    AND no conversation). Here the model returns nothing for the insufficiency prompt, so the
    deterministic NO_GROUNDING_FALLBACK constant is used — and the injected synthesize_fn is
    never reached (no fabrication)."""
    state = run_supervisor(
        "what is the population of atlantis", llm=_fake_llm,
        decide_fn=lambda s, d: "done",
        search_fn=lambda q, s: [],
        synthesize_fn=lambda *a: "should not be used",
        chat_history=[],                          # cold first turn
        do_rerank=False, do_audit=False,
    )
    assert "couldn't find" in state["final_answer"].lower()
    assert "should not be used" not in state["final_answer"]


def test_no_grounding_uses_llm_composed_contextual_reply_when_available():
    """When the LLM is available, the cold no-grounding case is answered with a contextual,
    grounding-safe reply composed by the model — not the canned fallback."""
    def llm(prompt: str) -> str:
        if "no supporting evidence was found" in prompt.lower():
            return "I don't have material on the population of Atlantis. Could you name a real place or dataset?"
        return "x"

    state = run_supervisor(
        "population of atlantis", llm=llm,
        decide_fn=lambda s, d: "done",
        search_fn=lambda q, s: [],
        synthesize_fn=lambda *a: "unused",
        chat_history=[],
        do_rerank=False, do_audit=False,
    )
    assert "atlantis" in state["final_answer"].lower()           # contextual, LLM-composed
    assert "couldn't find" not in state["final_answer"].lower()  # not the canned fallback


# --- related-knowledge-element: deterministic two-bucket lookup ----------------

def test_detect_related_elements_request():
    from agent_runtime.supervisor.graph import _detect_related_elements_request
    uuid = "86df1948-9726-4d64-901c-66fcfdbca433"
    assert _detect_related_elements_request(f"related knowledge elements of {uuid}") == uuid
    assert _detect_related_elements_request(f"what is connected to {uuid}?") == uuid
    assert _detect_related_elements_request("datasets related to floods") is None     # no UUID
    assert _detect_related_elements_request(f"describe element {uuid}") is None        # no related intent


def test_related_elements_evidence_splits_curated_and_content(monkeypatch):
    import agent_runtime.element_resolver as er
    import rag_pipeline.search.agents as agents
    import rag_pipeline.search.semantic as semantic
    from agent_runtime.supervisor.graph import _related_elements_evidence
    SEED = "86df1948-9726-4d64-901c-66fcfdbca433"
    monkeypatch.setattr(agents, "explore_neo4j_related_nodes", lambda eid, depth=2, limit=50: {
        "seed": {"doc_id": SEED, "title": "Stale Graph Title"},
        "documents": [{"doc_id": "rel1", "title": "Dam Failure Study",
                       "element_type": "publication", "contents": "..."}],
    })
    # The platform API is authoritative for the element's identity.
    monkeypatch.setattr(er, "resolve_element", lambda eid: {
        "title": "National Inventory of Dams", "resource_type": "dataset",
        "abstract": "all known dams", "related": []})

    def hit(i, did):
        return {"_id": did, "_score": 0.5, "_source": {"title": f"Sim {i}",
                "element_type": "dataset", "contents": "..."}}
    # semantic returns the seed (must be excluded) + two genuinely-similar elements
    monkeypatch.setattr(semantic, "semantic_search",
                        lambda q, size=12: [hit(0, SEED), hit(1, "sim1"), hit(2, "sim2")])

    docs = _related_elements_evidence(SEED, content_k=5)
    curated = [d["doc_id"] for d in docs if d["provenance"] == "curated"]
    content = [d["doc_id"] for d in docs if d["provenance"] == "content"]
    seed = [d for d in docs if d["provenance"] == "seed"]
    assert curated == ["rel1"]
    assert content == ["sim1", "sim2"]          # seed excluded, curated not duplicated
    assert SEED not in content
    # seed doc included FIRST, named from the PLATFORM (stale graph title overridden)
    assert seed and docs[0]["provenance"] == "seed"
    assert seed[0]["title"] == "National Inventory of Dams"


def test_related_elements_curated_falls_back_to_platform_api(monkeypatch):
    """When the graph yields nothing (no edges / stale node / auth failure), the curated bucket
    must come from the platform API's contributor-specified related-elements — the live failure:
    the platform had 2 related elements but the answer said 'none specified'."""
    import agent_runtime.element_resolver as er
    import rag_pipeline.search.agents as agents
    import rag_pipeline.search.semantic as semantic
    from agent_runtime.supervisor.graph import _related_elements_evidence
    SEED = "5e9c7566-1be5-49ea-aaec-fa304f401dd2"
    monkeypatch.setattr(agents, "explore_neo4j_related_nodes", lambda eid, depth=2, limit=50: {})
    monkeypatch.setattr(er, "resolve_element", lambda eid: {
        "title": "Dataset for SPASTC", "resource_type": "dataset", "abstract": "spatial partitioning",
        "related": [
            {"element_id": "d65a13bd", "title": "SPASTC paper", "resource_type": "publication"},
            {"element_id": "b1fa548b", "title": "SPASTC notebook", "resource_type": "notebook"},
        ]})
    monkeypatch.setattr(semantic, "semantic_search", lambda q, size=12: [])

    docs = _related_elements_evidence(SEED)
    seed = [d for d in docs if d["provenance"] == "seed"]
    curated = [d for d in docs if d["provenance"] == "curated"]
    assert seed[0]["title"] == "Dataset for SPASTC"
    assert [d["doc_id"] for d in curated] == ["d65a13bd", "b1fa548b"]   # from the platform API
    assert all(d["source"] == "platform_api" for d in curated)
    assert curated[0]["element_type"] == "publication"                  # -> /publications/... url


def test_related_provenance_docs_not_reranked_or_truncated():
    """Two-bucket related-element evidence must survive search_node intact — no rerank
    interleaving and no top_k truncation that would drop a bucket."""
    docs = [{"doc_id": f"d{i}", "title": f"T{i}",
             "provenance": "curated" if i < 4 else "content"} for i in range(7)]
    state = run_supervisor(
        "related elements of 86df1948-9726-4d64-901c-66fcfdbca433", llm=_fake_llm,
        decide_fn=_scripted(["search", "done"]),
        search_fn=lambda q, s: list(docs),
        synthesize_fn=lambda *a: "ok", do_rerank=True, do_audit=False,
    )
    assert len(state["evidence"]) == 7   # not truncated to top_k=5; both buckets intact
    assert all(d.get("provenance") in ("curated", "content") for d in state["evidence"])


# --- explain/describe <UUID>: deterministic by-id lookup ----------------------

def test_detect_element_lookup_request():
    from agent_runtime.supervisor.graph import _detect_element_lookup_request
    uuid = "86df1948-9726-4d64-901c-66fcfdbca433"
    assert _detect_element_lookup_request(uuid) == uuid                          # bare id
    assert _detect_element_lookup_request(f"Explain {uuid}") == uuid
    assert _detect_element_lookup_request(f"describe {uuid} please") == uuid
    assert _detect_element_lookup_request(f"what is {uuid}?") == uuid
    assert _detect_element_lookup_request(f"related elements of {uuid}") is None  # related handler owns this
    assert _detect_element_lookup_request("explain dam failures") is None         # no UUID -> normal search


def test_element_lookup_evidence_graph_then_api(monkeypatch):
    import rag_pipeline.search.agents as agents
    import agent_runtime.element_resolver as er
    from agent_runtime.supervisor.graph import _element_lookup_evidence
    UUID = "86df1948-9726-4d64-901c-66fcfdbca433"

    # graph node present -> used (rich contents)
    monkeypatch.setattr(agents, "get_neo4j_element_by_id_results",
        lambda eid: [{"_id": eid, "_score": 1.0, "_source": {
            "title": "National Inventory of Dams", "element_type": "dataset",
            "contents": "All known dams in the U.S."}}])
    docs = _element_lookup_evidence(UUID)
    assert len(docs) == 1 and docs[0]["title"] == "National Inventory of Dams"
    assert "dams" in docs[0]["contents"].lower()

    # graph empty -> backend API fallback (works regardless of Neo4j auth/presence)
    monkeypatch.setattr(agents, "get_neo4j_element_by_id_results", lambda eid: [])
    monkeypatch.setattr(er, "resolve_element", lambda eid: {
        "title": "NID", "resource_type": "dataset", "abstract": "dam inventory", "authors": [], "tags": []})
    docs2 = _element_lookup_evidence(UUID)
    assert len(docs2) == 1 and docs2[0]["title"] == "NID" and docs2[0]["contents"] == "dam inventory"
    assert docs2[0]["source"] == "backend_api"


def test_default_search_fn_short_circuits_id_lookup(monkeypatch):
    """An 'explain <UUID>' query must be served by the deterministic by-id fetch, NOT by
    building the LLM SearchAgent (whose tool-choice was the original failure)."""
    import rag_pipeline.search.agents as agents
    import agent_runtime.executor_factory as ef
    from agent_runtime.supervisor.graph import default_search_fn
    UUID = "86df1948-9726-4d64-901c-66fcfdbca433"
    monkeypatch.setattr(agents, "get_neo4j_element_by_id_results",
        lambda eid: [{"_id": eid, "_source": {"title": "NID", "element_type": "dataset", "contents": "x"}}])

    def boom(*a, **k):
        raise AssertionError("must NOT build the LLM SearchAgent for an id-lookup query")
    monkeypatch.setattr(ef, "build_search_agent_executor", boom)

    docs = default_search_fn()(f"Explain {UUID}", {"thread_id": "t"})
    assert len(docs) == 1 and docs[0]["title"] == "NID"   # served deterministically, no LLM


# --- id recall from conversation memory (follow-up without an explicit id) ----

def test_recall_recent_element_id_from_history():
    from agent_runtime.supervisor.graph import _recall_recent_element_id
    uuid = "86df1948-9726-4d64-901c-66fcfdbca433"
    other = "11111111-2222-3333-4444-555555555555"
    hist = [  # {userQuery, answer} session-memory shape; newest last
        {"userQuery": f"Explain {other}", "answer": "older element"},
        {"userQuery": f"Explain {uuid}", "answer": "The National Inventory of Dams ..."},
    ]
    assert _recall_recent_element_id(hist) == uuid                       # most-recent wins
    assert _recall_recent_element_id([{"role": "user", "content": f"explain {uuid}"}]) == uuid
    assert _recall_recent_element_id([("user", f"explain {uuid}")]) == uuid
    assert _recall_recent_element_id([]) is None
    assert _recall_recent_element_id([{"userQuery": "no id here", "answer": "none"}]) is None


def test_followup_regexes_match_subjectless_not_topical():
    from agent_runtime.supervisor.graph import _RELATED_FOLLOWUP_RE, _EXPLAIN_FOLLOWUP_RE
    assert _RELATED_FOLLOWUP_RE.match("What are the related elements")
    assert _RELATED_FOLLOWUP_RE.match("related elements")
    assert _RELATED_FOLLOWUP_RE.match("show me related")
    assert _RELATED_FOLLOWUP_RE.match("related to it")
    assert not _RELATED_FOLLOWUP_RE.match("datasets related to floods")   # carries its own subject
    assert _EXPLAIN_FOLLOWUP_RE.match("explain it")
    assert _EXPLAIN_FOLLOWUP_RE.match("describe this")
    assert _EXPLAIN_FOLLOWUP_RE.match("tell me more about it")
    assert not _EXPLAIN_FOLLOWUP_RE.match("explain dam failures")         # topic, not the element


def test_default_search_fn_recalls_id_for_subjectless_followup(monkeypatch):
    """'What are the related elements' (no id) must recall the element under discussion from
    chat_history and run the deterministic related lookup — not the LLM SearchAgent."""
    import agent_runtime.element_resolver as er
    import rag_pipeline.search.agents as agents
    import rag_pipeline.search.semantic as semantic
    import agent_runtime.executor_factory as ef
    from agent_runtime.supervisor.graph import default_search_fn
    UUID = "86df1948-9726-4d64-901c-66fcfdbca433"
    seen = {}

    def explore(eid, depth=2, limit=50):
        seen["eid"] = eid
        return {"seed": {"doc_id": eid, "title": "National Inventory of Dams"},
                "documents": [{"doc_id": "rel1", "title": "X", "element_type": "publication", "contents": "y"}]}
    monkeypatch.setattr(agents, "explore_neo4j_related_nodes", explore)
    monkeypatch.setattr(semantic, "semantic_search", lambda q, size=12: [])
    monkeypatch.setattr(er, "resolve_element", lambda eid: {"title": "NID", "resource_type": "dataset", "related": []})

    def boom(*a, **k):
        raise AssertionError("must NOT build the LLM SearchAgent when the id is recallable")
    monkeypatch.setattr(ef, "build_search_agent_executor", boom)

    state = {"thread_id": "t", "chat_history": [{"userQuery": f"Explain {UUID}", "answer": "NID ..."}]}
    docs = default_search_fn()("What are the related elements", state)
    assert seen.get("eid") == UUID                                   # recalled the id from memory
    assert any(d.get("provenance") == "curated" for d in docs)


# --- review fixes: role-aware recall + extended follow-up coverage ------------

def test_recall_prefers_user_subject_over_assistant_citation():
    """Regression (adversarial review): after a 'related elements of X' answer whose links cite
    OTHER elements' UUIDs, a follow-up must recall the USER's element X — not a cited one."""
    from agent_runtime.supervisor.graph import _recall_recent_element_id
    X = "33333333-3333-3333-3333-333333333333"
    Y = "44444444-4444-4444-4444-444444444444"
    hist = [
        {"role": "user", "content": f"related elements of {X}"},
        {"role": "assistant",
         "content": f"You may also find [Flood Data](https://platform.i-guide.io/datasets/{Y}) relevant."},
    ]
    assert _recall_recent_element_id(hist) == X          # user's subject, NOT the cited Y
    # {userQuery, answer} turn shape: only the query side counts as user text
    hist2 = [{"userQuery": f"explain {X}", "answer": f"see https://platform.i-guide.io/datasets/{Y}"}]
    assert _recall_recent_element_id(hist2) == X
    # fallback: when the user never typed a UUID, use the assistant's mention
    hist3 = [{"role": "user", "content": "find dam datasets"},
             {"role": "assistant", "content": f"https://platform.i-guide.io/datasets/{Y}"}]
    assert _recall_recent_element_id(hist3) == Y


def test_followup_regexes_extended_coverage():
    from agent_runtime.supervisor.graph import _RELATED_FOLLOWUP_RE, _EXPLAIN_FOLLOWUP_RE
    assert _RELATED_FOLLOWUP_RE.match("What is it related to?")     # finding: copula + pronoun
    assert _RELATED_FOLLOWUP_RE.match("what's this related to")
    assert _EXPLAIN_FOLLOWUP_RE.match("describe this element")      # finding: determiner + noun
    assert _EXPLAIN_FOLLOWUP_RE.match("information about this")     # finding: bare 'information'
    assert _EXPLAIN_FOLLOWUP_RE.match("info on it")
    # negatives still hold (topical queries carry their own subject -> normal search)
    assert not _RELATED_FOLLOWUP_RE.match("datasets related to floods")
    assert not _EXPLAIN_FOLLOWUP_RE.match("explain dam failures")
    assert not _EXPLAIN_FOLLOWUP_RE.match("information about floods")


# --- popularity queries: deterministic click_count ranking ---------------------

def test_detect_popularity_request():
    from agent_runtime.supervisor.graph import _detect_popularity_request
    assert _detect_popularity_request("What are the most popular knowledge elements")
    assert _detect_popularity_request("trending datasets")
    assert _detect_popularity_request("most viewed notebooks")
    assert not _detect_popularity_request("datasets about dam failures")
    assert not _detect_popularity_request("explain popular culture in geography")  # bare "popular" must not hijack


def test_default_search_fn_short_circuits_popularity(monkeypatch):
    """'most popular ...' must be served by the graph's click_count ranking, not the LLM agent."""
    import rag_pipeline.search.agents as agents
    import agent_runtime.executor_factory as ef
    from agent_runtime.supervisor.graph import default_search_fn

    monkeypatch.setattr(agents, "get_neo4j_agent_results", lambda q, limit=10: [
        {"_id": "e1", "_score": 42.0, "_source": {"title": "Hot Dataset", "element_type": "dataset", "contents": "x"}},
        {"_id": "e2", "_score": 7.0, "_source": {"title": "Warm Notebook", "element_type": "notebook", "contents": "y"}},
    ])

    def boom(*a, **k):
        raise AssertionError("must NOT build the LLM SearchAgent for a popularity query")
    monkeypatch.setattr(ef, "build_search_agent_executor", boom)

    docs = default_search_fn()("What are the most popular knowledge elements", {"thread_id": "t"})
    assert [d["doc_id"] for d in docs] == ["e1", "e2"]
    assert docs[0]["click_count"] == 42                      # real usage counts carried
    assert "[popularity: 42 clicks]" in docs[0]["contents"]  # visible to the synthesizer


def test_popularity_falls_back_to_search_agent_when_graph_empty(monkeypatch):
    """Graph unreachable/empty -> fall through to the normal search agent (not a dead end)."""
    import rag_pipeline.search.agents as agents
    import agent_runtime.executor_factory as ef
    from agent_runtime.supervisor.graph import default_search_fn

    monkeypatch.setattr(agents, "get_neo4j_agent_results", lambda q, limit=10: [])
    import rag_pipeline.search.keyword as kw
    import rag_pipeline.search.semantic as sem
    monkeypatch.setattr(kw, "get_keyword_search_results", lambda q, size=8: [])
    monkeypatch.setattr(sem, "semantic_search", lambda q, size=8: [])
    called = {}

    def fake_build(**kwargs):
        called["built"] = True
        return object()
    monkeypatch.setattr(ef, "build_search_agent_executor", fake_build)
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})

    default_search_fn()("most popular datasets", {"thread_id": "t"})
    assert called.get("built") is True


# --- search completeness: multi-method sweep + top_k ---------------------------

def _kw_hit(did, title):
    return {"_id": did, "_score": 1.0, "_source": {"title": title, "element_type": "dataset", "contents": "x"}}


def test_direct_search_sweep_runs_both_core_methods_and_respects_allowlist(monkeypatch):
    import rag_pipeline.search.keyword as kw
    import rag_pipeline.search.semantic as sem
    from agent_runtime.supervisor.graph import _direct_search_sweep
    monkeypatch.setattr(kw, "get_keyword_search_results", lambda q, size=8: [_kw_hit("k1", "KW Hit")])
    monkeypatch.setattr(sem, "semantic_search", lambda q, size=8: [_kw_hit("s1", "Sem Hit")])

    docs = _direct_search_sweep("floods", None)
    assert {d["doc_id"] for d in docs} == {"k1", "s1"}          # BOTH methods contributed
    docs2 = _direct_search_sweep("floods", ["semantic_search"])
    assert {d["doc_id"] for d in docs2} == {"s1"}               # allowlist respected

    def boom(q, size=8):
        raise RuntimeError("opensearch down")
    monkeypatch.setattr(kw, "get_keyword_search_results", boom)
    docs3 = _direct_search_sweep("floods", None)
    assert {d["doc_id"] for d in docs3} == {"s1"}               # one method failing never raises


def test_search_fn_unions_sweep_with_llm_harvest(monkeypatch):
    """Even when the LLM SearchAgent calls a single tool (or none), the search turn returns
    multi-method coverage: the deterministic keyword+semantic sweep is unioned in."""
    import agent_runtime.executor_factory as ef
    import rag_pipeline.search.keyword as kw
    import rag_pipeline.search.semantic as sem
    from agent_runtime.supervisor.graph import default_search_fn

    monkeypatch.setattr(ef, "build_search_agent_executor", lambda **k: object())
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})
    monkeypatch.setattr(kw, "get_keyword_search_results", lambda q, size=8: [_kw_hit("k1", "KW")])
    monkeypatch.setattr(sem, "semantic_search", lambda q, size=8: [_kw_hit("k1", "KW"), _kw_hit("s1", "Sem")])

    docs = default_search_fn()("datasets about floods", {"thread_id": "t"})
    assert [d["doc_id"] for d in docs] == ["k1", "s1"]          # merged + deduped on k1


def test_default_top_k_env_tunable(monkeypatch):
    from agent_runtime.supervisor.graph import _default_top_k
    monkeypatch.delenv("AGENT_SUPERVISOR_TOP_K", raising=False)
    assert _default_top_k() == 8                                 # raised from the historical 5
    monkeypatch.setenv("AGENT_SUPERVISOR_TOP_K", "12")
    assert _default_top_k() == 12
    monkeypatch.setenv("AGENT_SUPERVISOR_TOP_K", "bogus")
    assert _default_top_k() == 8


# --- QGIS availability + analyze-before-code routing --------------------------

def test_code_peer_has_qgis_tools(monkeypatch):
    """The code peer must expose the QGIS tools: they run in the AGENT env, while the code
    sandbox image has no `qgis` package — previously the peer could only try an `import qgis`
    inside execute_code, which always fails (the reported failure)."""
    from types import SimpleNamespace
    import agent_runtime.executor_factory as ef
    import agent_runtime.langchain_granular_tools as gt
    import agent_runtime.supervisor_graph as sg

    monkeypatch.setattr(gt, "make_langchain_qgis_tools",
                        lambda **k: [SimpleNamespace(name="qgis_metric_buffer"),
                                     SimpleNamespace(name="pyqgis_render_map")])
    captured = {}

    def fake_build(**kwargs):
        captured["tools"] = [getattr(t, "name", "") for t in (kwargs.get("preloaded_tools") or [])]
        return object()
    monkeypatch.setattr(ef, "build_agent_executor", fake_build)
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", lambda *a, **k: {"messages": []})

    sg.default_code_fn()("buffer these points with qgis", [], {"thread_id": None})
    assert "qgis_metric_buffer" in captured["tools"]
    assert "pyqgis_render_map" in captured["tools"]


def test_decider_prompt_prefers_analyze_before_code():
    """The supervisor must try the tool-owning analyze peer before writing fresh code."""
    captured = {}

    def llm(prompt):
        captured["prompt"] = prompt
        return json.dumps({"next": "analyze", "reason": "existing tools cover it"})

    from agent_runtime.supervisor.graph import default_decide_fn
    nxt = default_decide_fn(llm=llm)({"query": "buffer these cities"}, {"has_evidence": True})
    assert nxt == "analyze"
    p = captured["prompt"]
    assert "ANALYZE BEFORE CODE" in p
    assert "existing purpose-built tools" in p.lower()
    assert "only when analyze has already run" in p.lower()
