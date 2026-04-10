from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.graph_nodes import DefaultSubagentRunner
from agent_runtime.graph_runtime import AGENT_QUERY_GRAPH, run_agent_query, stream_agent_query_events
from agent_runtime.graph_state import AgentRequest, AgentRuntimeState


@dataclass
class DummyTool:
    name: str


def test_agent_query_graph_contains_new_nodes():
    mermaid = AGENT_QUERY_GRAPH.get_graph().draw_mermaid()
    assert "initialize_request" in mermaid
    assert "classify_intent" in mermaid
    assert "resolve_policy" in mermaid
    assert "run_search_agent" in mermaid
    assert "run_analysis_agent" in mermaid
    assert "run_code_agent" in mermaid
    assert "run_verification_agent" in mermaid
    assert "finalize_response" in mermaid


def test_run_agent_query_returns_langgraph_first_contract(monkeypatch):
    monkeypatch.setattr(
        "agent_runtime.graph_nodes.collect_tools",
        lambda **kwargs: [DummyTool("keyword_search"), DummyTool("read_text_file"), DummyTool("write_text_file")],
    )
    monkeypatch.setattr("agent_runtime.graph_nodes.build_search_agent_executor", lambda **kwargs: object())
    monkeypatch.setattr(
        "agent_runtime.graph_nodes.invoke_agent_with_payload_fallback",
        lambda executor, **kwargs: {"final_answer": "search answer", "messages": []},
    )

    result = run_agent_query("Find hydrology datasets", thread_id="thread-search", checkpointer=None)

    assert result["thread_id"] == "thread-search"
    assert result["agent_role"] == "search"
    assert result["intent"] == "general_discovery"
    assert result["final_answer"] == "search answer"
    assert isinstance(result["route_trace"], dict)
    assert isinstance(result["artifacts"], dict)
    assert "search_result" in result


def test_run_code_agent_query_adds_verification(monkeypatch):
    monkeypatch.setattr(
        "agent_runtime.graph_nodes.collect_tools",
        lambda **kwargs: [DummyTool("keyword_search"), DummyTool("read_text_file"), DummyTool("write_output_file")],
    )

    class FakeExecutor:
        def __init__(self, phase: str):
            self.phase = phase

    monkeypatch.setattr("agent_runtime.graph_nodes.build_code_agent_executor", lambda **kwargs: FakeExecutor("code"))
    monkeypatch.setattr("agent_runtime.graph_nodes.build_verification_agent_executor", lambda **kwargs: FakeExecutor("verify"))

    def fake_invoke(executor, **kwargs):
        if executor.phase == "code":
            return {"final_answer": "def answer():\n    return 42", "messages": []}
        return {
            "final_answer": '{"status":"PASS","summary":"Looks internally consistent.","issues":[]}',
            "messages": [],
        }

    monkeypatch.setattr("agent_runtime.graph_nodes.invoke_agent_with_payload_fallback", fake_invoke)

    result = run_agent_query(
        "Write Python code to summarize the attached CSV",
        thread_id="thread-code",
        checkpointer=None,
    )

    assert result["agent_role"] == "code"
    assert result["intent"] == "code_task"
    assert result["verification_result"]["status"] == "PASS"
    assert result["final_answer"].startswith("def answer")


def test_stream_agent_query_events_use_stable_envelope(monkeypatch):
    monkeypatch.setattr(
        "agent_runtime.graph_nodes.collect_tools",
        lambda **kwargs: [DummyTool("keyword_search"), DummyTool("read_text_file")],
    )
    monkeypatch.setattr("agent_runtime.graph_nodes.build_search_agent_executor", lambda **kwargs: object())
    monkeypatch.setattr(
        "agent_runtime.graph_nodes.invoke_agent_with_payload_fallback",
        lambda executor, **kwargs: {"final_answer": "streamed answer", "messages": []},
    )

    events = list(
        stream_agent_query_events(
            "Find climate publications",
            thread_id="thread-stream",
            checkpointer=None,
        )
    )

    assert events
    assert any(event["event"] == "route_trace" for event in events)
    assert any(event["event"] == "subagent_completed" for event in events)
    assert events[-1]["event"] == "completed"
    for event in events:
        assert "thread_id" in event
        assert "node" in event
        assert "timestamp" in event
        assert "payload" in event


def test_full_pipeline_search_falls_back_to_legacy_rag_tool(monkeypatch):
    runner = DefaultSubagentRunner()
    request = AgentRequest(query="Fallback query", tool_strategy="full_pipeline", thread_id="thread-fallback", checkpointer=None)
    runtime = AgentRuntimeState(all_tools=[DummyTool("rag_tool")], search_thread_id="thread-fallback::search")

    monkeypatch.setattr("agent_runtime.graph_nodes.build_search_agent_executor", lambda **kwargs: object())

    class EmptyModelError(RuntimeError):
        pass

    def fake_invoke(executor, **kwargs):
        raise EmptyModelError("NoneType has no attribute model_dump")

    monkeypatch.setattr("agent_runtime.graph_nodes.invoke_agent_with_payload_fallback", fake_invoke)
    monkeypatch.setattr("agent_runtime.graph_nodes.is_empty_model_response_error", lambda exc: True)
    monkeypatch.setattr("agent_runtime.graph_nodes.rag_tool", lambda query: {"answer": "legacy fallback"})

    result = runner.run_search(request=request, runtime=runtime, allowed_tool_names=["rag_tool"])

    assert result["fallback"] == "direct_rag_tool"
    assert result["result"]["answer"] == "legacy fallback"
