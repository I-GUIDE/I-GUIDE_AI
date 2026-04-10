from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Protocol

from .executor_factory import (
    agent_config,
    build_analysis_agent_executor,
    build_code_agent_executor,
    build_search_agent_executor,
    build_verification_agent_executor,
    child_thread_id,
    collect_tools,
    invoke_agent_with_payload_fallback,
    is_empty_model_response_error,
    rag_tool,
    resolve_thread_id,
)
from .graph_state import AgentArtifacts, AgentPolicy, AgentQueryGraphState, AgentRequest, AgentRuntimeState
from .intent_classifier import build_route_trace
from .runtime_utils import (
    build_search_evidence_payload,
    build_subagent_envelope,
    extract_citations_from_payload,
    extract_final_answer,
    extract_generated_files,
    extract_search_artifacts,
)
from .tool_policy import READ_ONLY_FILE_TOOL_NAMES, WRITE_FILE_TOOL_NAMES, resolve_agent_policy


class SubagentRunner(Protocol):
    def run_search(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
        allowed_tool_names: Optional[List[str]],
    ) -> Dict[str, Any]: ...

    def run_analysis(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]: ...

    def run_code(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
        can_write_files: bool,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]: ...

    def run_verification(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
        target_role: str,
        raw_result: Dict[str, Any],
        route_trace: Dict[str, Any],
    ) -> Dict[str, Any]: ...


class DefaultSubagentRunner:
    def _make_search_agent_evidence_tool(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
        search_invocations: List[Dict[str, Any]],
    ) -> Any:
        try:
            from langchain_core.tools import StructuredTool
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "LangChain is not installed. Add `langchain-core` (or langchain) to dependencies."
            ) from exc

        def search_agent_evidence(query: str) -> str:
            nested_request = AgentRequest(**request.to_dict())
            nested_request.query = query
            all_tools = list(runtime.all_tools or [])
            available_names = [getattr(tool, "name", "") for tool in all_tools if getattr(tool, "name", "")]
            allowed_tool_names = available_names
            route_trace = {}
            if nested_request.smart_tool_routing:
                provisional_trace = build_route_trace(
                    query,
                    available_tool_names=available_names,
                    allowed_tool_names=available_names,
                    forced_intent=nested_request.forced_intent,
                )
                nested_policy = resolve_agent_policy(
                    provisional_trace,
                    available_tool_names=available_names,
                    include_mcp_tools=nested_request.include_mcp_tools,
                )
                allowed_tool_names = nested_policy.allowed_tool_names
                route_trace = build_route_trace(
                    query,
                    available_tool_names=available_names,
                    allowed_tool_names=allowed_tool_names,
                    forced_intent=nested_request.forced_intent,
                )

            search_response = self.run_search(
                request=nested_request,
                runtime=AgentRuntimeState(
                    all_tools=all_tools,
                    effective_thread_id=runtime.analysis_thread_id,
                    search_thread_id=child_thread_id(runtime.analysis_thread_id, f"search_tool_{len(search_invocations) + 1}"),
                    analysis_thread_id=runtime.analysis_thread_id,
                    code_thread_id=runtime.code_thread_id,
                    verification_thread_id=runtime.verification_thread_id,
                ),
                allowed_tool_names=allowed_tool_names,
            )
            evidence = build_search_evidence_payload(query, search_response, route_trace)
            search_invocations.append(
                {
                    "query": query,
                    "route_trace": route_trace,
                    "search_result": search_response,
                    "evidence": evidence,
                }
            )
            return json.dumps(evidence, ensure_ascii=True, default=str)

        return StructuredTool.from_function(
            func=search_agent_evidence,
            name="search_agent_evidence",
            description=(
                "Call SearchAgent to retrieve domain-specific evidence and references. "
                "Use before generating claims or code that relies on external facts."
            ),
        )

    def run_search(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
        allowed_tool_names: Optional[List[str]],
    ) -> Dict[str, Any]:
        search_executor = build_search_agent_executor(
            llm=request.llm,
            verbose=request.verbose,
            return_intermediate_steps=request.return_intermediate_steps,
            tool_strategy=request.tool_strategy,
            include_mcp_tools=request.include_mcp_tools,
            mcp_modules=request.mcp_modules,
            allowed_tool_names=allowed_tool_names,
            preloaded_tools=runtime.all_tools,
            checkpointer=request.checkpointer,
        )
        try:
            return invoke_agent_with_payload_fallback(
                search_executor,
                query=request.query,
                chat_history=request.chat_history,
                config=agent_config(runtime.search_thread_id),
            )
        except Exception as exc:
            if request.tool_strategy == "full_pipeline" and is_empty_model_response_error(exc):
                direct = rag_tool(query=request.query)
                return {"fallback": "direct_rag_tool", "reason": str(exc), "result": direct}
            raise

    def run_analysis(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        search_invocations: List[Dict[str, Any]] = []
        search_tool = self._make_search_agent_evidence_tool(
            request=request,
            runtime=runtime,
            search_invocations=search_invocations,
        )
        analysis_executor = build_analysis_agent_executor(
            llm=request.llm,
            verbose=request.verbose,
            return_intermediate_steps=request.return_intermediate_steps,
            tools=[search_tool],
            checkpointer=request.checkpointer,
        )
        analysis_result = invoke_agent_with_payload_fallback(
            analysis_executor,
            query=request.query,
            chat_history=request.chat_history,
            config=agent_config(runtime.analysis_thread_id),
        )
        return analysis_result, search_invocations

    def run_code(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
        can_write_files: bool,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        search_invocations: List[Dict[str, Any]] = []
        search_tool = self._make_search_agent_evidence_tool(
            request=request,
            runtime=runtime,
            search_invocations=search_invocations,
        )
        file_tool_names = READ_ONLY_FILE_TOOL_NAMES | (WRITE_FILE_TOOL_NAMES if can_write_files else set())
        file_tools = [
            tool for tool in runtime.all_tools
            if getattr(tool, "name", "") in file_tool_names
        ]
        code_executor = build_code_agent_executor(
            llm=request.llm,
            verbose=request.verbose,
            return_intermediate_steps=request.return_intermediate_steps,
            tools=[search_tool, *file_tools],
            checkpointer=request.checkpointer,
        )
        code_result = invoke_agent_with_payload_fallback(
            code_executor,
            query=request.query,
            chat_history=request.chat_history,
            config=agent_config(runtime.code_thread_id),
        )
        generated_files = extract_generated_files(code_result)
        return code_result, search_invocations, generated_files

    def run_verification(
        self,
        *,
        request: AgentRequest,
        runtime: AgentRuntimeState,
        target_role: str,
        raw_result: Dict[str, Any],
        route_trace: Dict[str, Any],
    ) -> Dict[str, Any]:
        final_answer = extract_final_answer(raw_result) or ""
        if not final_answer.strip():
            return {
                "status": "PARTIAL",
                "summary": "No final answer was available for verification.",
                "issues": ["missing_final_answer"],
            }

        verification_executor = build_verification_agent_executor(
            llm=request.llm,
            verbose=request.verbose,
            return_intermediate_steps=request.return_intermediate_steps,
            checkpointer=request.checkpointer,
        )
        verification_query = (
            "Verify the following agent output.\n"
            f"Target role: {target_role}\n"
            f"Route trace: {json.dumps(route_trace, ensure_ascii=True)}\n"
            "Return strict JSON with keys status, summary, and issues.\n\n"
            f"Output:\n{final_answer}"
        )
        try:
            verification_result = invoke_agent_with_payload_fallback(
                verification_executor,
                query=verification_query,
                chat_history=None,
                config=agent_config(runtime.verification_thread_id),
            )
            summary = extract_final_answer(verification_result) or ""
            if isinstance(summary, str):
                try:
                    start = summary.find("{")
                    end = summary.rfind("}") + 1
                    if start >= 0 and end > start:
                        parsed = json.loads(summary[start:end])
                        if isinstance(parsed, dict):
                            return parsed
                except Exception:
                    pass
            return {
                "status": "PASS" if summary else "PARTIAL",
                "summary": summary or "Verification completed without structured output.",
                "issues": [],
                "raw_result": verification_result,
            }
        except Exception as exc:
            return {
                "status": "PARTIAL",
                "summary": f"Verification fallback used: {exc}",
                "issues": ["verification_runtime_error"],
            }


def initialize_request_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    request = state["request"]
    all_tools = collect_tools(
        tool_strategy=request.tool_strategy,
        include_mcp_tools=request.include_mcp_tools,
        mcp_modules=request.mcp_modules,
        enabled_search_methods=request.enabled_search_methods,
    )
    effective_thread_id = resolve_thread_id(request.thread_id, request.checkpointer)
    return {
        "runtime": AgentRuntimeState(
            all_tools=all_tools,
            effective_thread_id=effective_thread_id,
            search_thread_id=child_thread_id(effective_thread_id, "search"),
            analysis_thread_id=child_thread_id(effective_thread_id, "analysis"),
            code_thread_id=child_thread_id(effective_thread_id, "code"),
            verification_thread_id=child_thread_id(effective_thread_id, "verification"),
        ),
        "artifacts": AgentArtifacts(),
        "response_model": state.get("response_model") or None,
    }


def classify_intent_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    request = state["request"]
    runtime = state["runtime"]
    available_names = [getattr(tool, "name", "") for tool in runtime.all_tools if getattr(tool, "name", "")]
    route_trace = build_route_trace(
        request.query,
        available_tool_names=available_names,
        allowed_tool_names=available_names,
        forced_intent=request.forced_intent,
    )
    artifacts = state.get("artifacts") or AgentArtifacts()
    artifacts.route_trace = route_trace
    return {"artifacts": artifacts}


def resolve_policy_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    request = state["request"]
    runtime = state["runtime"]
    artifacts = state["artifacts"]
    available_names = [getattr(tool, "name", "") for tool in runtime.all_tools if getattr(tool, "name", "")]
    policy = resolve_agent_policy(
        artifacts.route_trace,
        available_tool_names=available_names,
        include_mcp_tools=request.include_mcp_tools,
    )
    artifacts.route_trace = build_route_trace(
        request.query,
        available_tool_names=available_names,
        allowed_tool_names=policy.allowed_tool_names,
        forced_intent=request.forced_intent,
    )
    return {"policy": policy, "artifacts": artifacts}


def run_search_agent_node(state: AgentQueryGraphState, runner: SubagentRunner) -> AgentQueryGraphState:
    request = state["request"]
    runtime = state["runtime"]
    policy = state["policy"]
    raw_result = runner.run_search(
        request=request,
        runtime=runtime,
        allowed_tool_names=policy.allowed_tool_names,
    )
    artifacts = state["artifacts"]
    search_artifacts = extract_search_artifacts(raw_result)
    artifacts.search_artifacts = search_artifacts
    search_envelope = build_subagent_envelope(
        role="search",
        raw_result=raw_result,
        route_trace=artifacts.route_trace,
        extra_artifacts={"search_artifacts": search_artifacts},
    )
    return {
        "search_result": raw_result,
        "search_envelope": search_envelope,
        "artifacts": artifacts,
    }


def run_analysis_agent_node(state: AgentQueryGraphState, runner: SubagentRunner) -> AgentQueryGraphState:
    request = state["request"]
    runtime = state["runtime"]
    artifacts = state["artifacts"]
    raw_result, search_invocations = runner.run_analysis(request=request, runtime=runtime)
    artifacts.subagent_invocations = search_invocations
    analysis_envelope = build_subagent_envelope(
        role="analysis",
        raw_result=raw_result,
        route_trace=artifacts.route_trace,
        extra_artifacts={"subagent_invocations": search_invocations},
    )
    return {
        "analysis_result": raw_result,
        "analysis_envelope": analysis_envelope,
        "artifacts": artifacts,
    }


def run_code_agent_node(state: AgentQueryGraphState, runner: SubagentRunner) -> AgentQueryGraphState:
    request = state["request"]
    runtime = state["runtime"]
    policy = state["policy"]
    artifacts = state["artifacts"]
    raw_result, search_invocations, generated_files = runner.run_code(
        request=request,
        runtime=runtime,
        can_write_files=policy.can_write_files,
    )
    artifacts.subagent_invocations = search_invocations
    artifacts.generated_files = generated_files
    code_envelope = build_subagent_envelope(
        role="code",
        raw_result=raw_result,
        route_trace=artifacts.route_trace,
        extra_artifacts={
            "subagent_invocations": search_invocations,
            "generated_files": generated_files,
        },
    )
    return {
        "code_result": raw_result,
        "code_envelope": code_envelope,
        "artifacts": artifacts,
    }


def run_verification_agent_node(state: AgentQueryGraphState, runner: SubagentRunner) -> AgentQueryGraphState:
    request = state["request"]
    runtime = state["runtime"]
    artifacts = state["artifacts"]
    verification_result = runner.run_verification(
        request=request,
        runtime=runtime,
        target_role=str(state["policy"].role),
        raw_result=state.get("code_result") or {},
        route_trace=artifacts.route_trace,
    )
    artifacts.verification = verification_result
    return {"verification_result": verification_result, "artifacts": artifacts}


def finalize_response_node(state: AgentQueryGraphState) -> AgentQueryGraphState:
    request = state["request"]
    runtime = state["runtime"]
    policy = state["policy"]
    artifacts = state["artifacts"]

    raw_result: Dict[str, Any] = (
        state.get("code_result")
        or state.get("analysis_result")
        or state.get("search_result")
        or {}
    )
    response = {
        "thread_id": runtime.effective_thread_id or request.thread_id,
        "agent_role": policy.role,
        "intent": policy.intent,
        "route_trace": artifacts.route_trace,
        "artifacts": artifacts.to_dict(),
        "final_answer": extract_final_answer(raw_result),
        "citations": extract_citations_from_payload(raw_result),
    }

    if state.get("search_result") is not None:
        response["search_result"] = state.get("search_result")
    if state.get("analysis_result") is not None:
        response["analysis_result"] = state.get("analysis_result")
    if state.get("code_result") is not None:
        response["code_result"] = state.get("code_result")
    if state.get("verification_result") is not None:
        response["verification_result"] = state.get("verification_result")
    if artifacts.subagent_invocations:
        response["analysis_agent_search_invocations"] = list(artifacts.subagent_invocations)
        response["code_agent_search_invocations"] = list(artifacts.subagent_invocations)

    return {"response": response}
