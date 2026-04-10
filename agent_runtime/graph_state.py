from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict


AgentIntent = Literal["general_discovery", "analysis_task", "code_task", "hybrid"]
AgentRole = Literal["search", "analysis", "code", "verification"]
RouteType = Literal["search", "analysis", "code"]


@dataclass
class AgentRequest:
    query: str
    chat_history: Optional[List[Any]] = None
    llm: Optional[Any] = None
    verbose: bool = False
    return_intermediate_steps: bool = True
    tool_strategy: str = "granular"
    include_mcp_tools: bool = False
    mcp_modules: Optional[List[str]] = None
    enabled_search_methods: Optional[List[str]] = None
    smart_tool_routing: bool = True
    forced_intent: Optional[str] = None
    thread_id: Optional[str] = None
    checkpointer: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRuntimeState:
    all_tools: List[Any] = field(default_factory=list)
    effective_thread_id: Optional[str] = None
    search_thread_id: Optional[str] = None
    analysis_thread_id: Optional[str] = None
    code_thread_id: Optional[str] = None
    verification_thread_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentPolicy:
    intent: AgentIntent = "general_discovery"
    reason: str = ""
    route_type: RouteType = "search"
    role: AgentRole = "search"
    allowed_tool_names: List[str] = field(default_factory=list)
    can_write_files: bool = False
    can_use_mcp: bool = False
    use_verification: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentArtifacts:
    search_artifacts: Dict[str, Any] = field(default_factory=dict)
    subagent_invocations: List[Dict[str, Any]] = field(default_factory=list)
    generated_files: List[Dict[str, Any]] = field(default_factory=list)
    route_trace: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentResponse:
    final_answer: Optional[str] = None
    citations: List[Dict[str, Any]] = field(default_factory=list)
    thread_id: Optional[str] = None
    agent_role: AgentRole = "search"
    intent: AgentIntent = "general_discovery"
    route_trace: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SubagentResultEnvelope(TypedDict, total=False):
    role: AgentRole
    summary: str
    citations: List[Dict[str, Any]]
    tool_calls: List[Dict[str, Any]]
    tool_results: List[Dict[str, Any]]
    artifacts: Dict[str, Any]
    raw_result: Dict[str, Any]


class AgentQueryGraphState(TypedDict, total=False):
    request: AgentRequest
    runtime: AgentRuntimeState
    policy: AgentPolicy
    artifacts: AgentArtifacts
    response_model: AgentResponse
    response: Dict[str, Any]
    search_result: Dict[str, Any]
    analysis_result: Dict[str, Any]
    code_result: Dict[str, Any]
    verification_result: Dict[str, Any]
    search_envelope: SubagentResultEnvelope
    analysis_envelope: SubagentResultEnvelope
    code_envelope: SubagentResultEnvelope
    error: Optional[str]

