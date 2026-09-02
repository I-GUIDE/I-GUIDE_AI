"""A capability question is answered by reasoning over live introspection, not a fixed prompt.

The old route was a one-shot composer that received the inventory and NO query, under an
instruction to "cover everything in the inventory". So every capability question got the same
grouped catalogue of every area, written by a model that had never seen what was asked: "what
can you do with satellite imagery?" returned five generic headings and never mentioned
embeddings, though the inventory carried eight tools for exactly that. From the user's side
that is indistinguishable from a hardcoded answer.

It also truncated: json.dumps(inventory, indent=1)[:12000] against a 27,397-char blob dropped
56% of the surface — always the registries added last, and sliced mid-object, so the model
received malformed JSON.
"""
from __future__ import annotations

import json

import pytest

from agent_runtime.capabilities import describe_capabilities, make_capability_tools


@pytest.fixture()
def introspect():
    tools = make_capability_tools(include_mcp_tools=False)
    assert tools, "the introspection tool must exist"
    return tools[0]


# --- introspection is live, filterable, and cannot mislead --------------------------------

def test_the_agent_can_look_up_its_own_surface(introspect):
    out = json.loads(introspect.invoke({"topic": ""}))
    assert out["total_available"] > 40
    # Descriptions are capped, and the cap lands on the LAST registries — the same cut that
    # hid the embedding tools before. The name index must still carry them, and the response
    # must say descriptions were omitted rather than implying 40 is the whole surface.
    assert "embed_zones" in out["all_tool_names"]
    if out["descriptions_shown"] < out["total_available"]:
        assert "hint" in out and "of" in out["hint"]


def test_a_topic_narrows_the_result(introspect):
    out = json.loads(introspect.invoke({"topic": "cluster"}))
    assert 0 < out["matched"] < out["total_available"]
    assert any("cluster" in t["name"] or "cluster" in (t["description"] or "").lower()
               for t in out["tools"])


def test_a_weak_topic_match_cannot_read_as_the_whole_answer(introspect):
    """The vocabulary gap is real: the embedding tools say "remote-sensing foundation model",
    so "satellite imagery" matches only a couple of them by substring. Returning every tool
    NAME regardless is what stops the agent answering from a third of the surface."""
    out = json.loads(introspect.invoke({"topic": "satellite imagery"}))
    names = set(out["all_tool_names"])
    for want in ("embed_region", "embed_zones", "segment_region", "fit_zone_model",
                 "predict_for_region", "embedding_change"):
        assert want in names, f"{want} must be discoverable even on a weak match"
    assert "hint" in out, "a filtered result must say the filter is only a starting point"


def test_an_unsupported_topic_says_so_rather_than_guessing(introspect):
    out = json.loads(introspect.invoke({"topic": "quantum computing"}))
    assert out["matched"] == 0
    assert out["all_tool_names"], "and still shows what IS available"


def test_the_payload_stays_small_enough_to_reason_over(introspect):
    """The point of a filterable tool: no 12,000-char truncation of a 27,000-char blob."""
    assert len(introspect.invoke({"topic": "satellite imagery"})) < 8000


# --- the loop itself ----------------------------------------------------------------------

class _ScriptedAgent:
    """Stands in for the ReAct executor: records the question, returns a canned answer."""

    def __init__(self, answer="Satellite imagery: embeddings for a rectangle or per polygon."):
        self.answer = answer
        self.seen = []


def test_the_question_reaches_the_agent(monkeypatch):
    captured = {}

    import agent_runtime.executor_factory as ef

    def _fake_build(**kwargs):
        captured["prompt"] = kwargs.get("system_prompt_override")
        captured["tools"] = [str(getattr(t, "name", "")) for t in (kwargs.get("preloaded_tools") or [])]
        return object()

    def _fake_invoke(executor, *, query, chat_history=None, config=None):
        captured["query"] = query
        return {"messages": []}

    monkeypatch.setattr(ef, "build_agent_executor", _fake_build)
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback", _fake_invoke)
    monkeypatch.setattr(ef, "build_default_llm", lambda: object())

    describe_capabilities(query="what can you do with satellite imagery?",
                          include_mcp_tools=False)

    assert captured["query"] == "what can you do with satellite imagery?"
    assert "list_my_capabilities" in captured["tools"], "the agent must be able to introspect"
    assert "INTROSPECT" in (captured["prompt"] or "")
    assert "ANSWER THE QUESTION ASKED" in (captured["prompt"] or "")


def test_the_real_model_listers_are_available_to_it(monkeypatch):
    """"which models can you use?" should be answerable with the actual names."""
    captured = {}
    import agent_runtime.executor_factory as ef
    monkeypatch.setattr(ef, "build_agent_executor",
                        lambda **kw: captured.setdefault(
                            "tools", [str(getattr(t, "name", "")) for t in kw.get("preloaded_tools") or []]) and object() or object())
    monkeypatch.setattr(ef, "invoke_agent_with_payload_fallback",
                        lambda *a, **k: {"messages": []})
    monkeypatch.setattr(ef, "build_default_llm", lambda: object())

    describe_capabilities(query="which embedding models do you have?", include_mcp_tools=False)
    assert "list_embedding_models" in captured["tools"]


def test_it_falls_back_to_a_mechanical_listing_when_no_model_answers(monkeypatch):
    import agent_runtime.executor_factory as ef

    def _boom(**kwargs):
        raise RuntimeError("no llm")

    monkeypatch.setattr(ef, "build_agent_executor", _boom)
    monkeypatch.setattr(ef, "build_default_llm", lambda: object())

    out = describe_capabilities(query="what can you do?", include_mcp_tools=False)
    assert "Available capabilities:" in out
    assert "embed_zones" in out, "the fallback is still derived from the live registries"


def test_the_route_passes_the_users_question(monkeypatch):
    """The node used to call describe_capabilities without a query at all."""
    import inspect

    from agent_runtime import orchestrator_graph

    src = inspect.getsource(orchestrator_graph)
    assert 'query=state.get("query")' in src
