# I-GUIDE Multi-Agent System

The agent is a **recursive "agents-as-tools" hierarchy** built on LangChain
`create_agent` / `AgentExecutor`. Each sub-agent is wrapped as a `StructuredTool`,
so a parent delegates by calling a tool that internally spins up a child agent.
SearchAgent is the only leaf that reaches the real RAG search backends.

## Agent hierarchy & control flow

```mermaid
flowchart TD
    classDef entry fill:#e8f0fe,stroke:#4285f4,color:#1a1a1a;
    classDef agent fill:#fce8e6,stroke:#ea4335,color:#1a1a1a;
    classDef tool fill:#e6f4ea,stroke:#34a853,color:#1a1a1a;
    classDef route fill:#fef7e0,stroke:#fbbc04,color:#1a1a1a;
    classDef backend fill:#f3e8fd,stroke:#a142f4,color:#1a1a1a;

    %% ---- Entry points ----
    API["Flask API<br/>/agent/chat · /agent/chat/stream"]:::entry
    CLI["CLI<br/>python -m agent_runtime.graph_runtime"]:::entry
    GR["graph_runtime<br/>run_agent_query / stream_agent_query_events / run_code_agent_query"]:::entry
    API --> GR
    CLI --> GR

    %% ---- Routing ----
    GR --> ROUTE{{"Routing (intent_classifier)<br/>1. forced intent<br/>2. LLM router<br/>3. heuristic fallback"}}:::route
    ROUTE -->|"classify_intent + select_allowed_tools"| ORCH

    %% ---- Orchestrator ----
    ORCH["OrchestratorAgent"]:::agent

    ORCH -->|tool| MEM["answer_from_memory<br/>(single LLM call on chat history)"]:::tool
    ORCH -->|tool| SAE1["search_agent_evidence"]:::tool
    ORCH -->|tool| AAA["analysis_agent_answer"]:::tool
    ORCH -.->|"only if attached-file context"| FILES["file tools + QGIS tools"]:::tool

    %% ---- Sub-agents ----
    SAE1 --> SEARCH["SearchAgent"]:::agent
    AAA --> ANALYSIS["AnalysisAgent"]:::agent

    ANALYSIS -->|tool| SAE2["search_agent_evidence"]:::tool
    ANALYSIS -->|tool| CAA["code_agent_answer"]:::tool
    SAE2 --> SEARCH
    CAA --> CODE["CodeAgent"]:::agent
    CODE -->|tool| SAE3["search_agent_evidence"]:::tool
    SAE3 --> SEARCH

    %% ---- Skills (available to every agent) ----
    SKILLS["Skill tools<br/>list_available_skills · load_skill<br/>(SKILL.md bundles)"]:::tool
    ORCH -.-> SKILLS
    ANALYSIS -.-> SKILLS
    CODE -.-> SKILLS
    SEARCH -.-> SKILLS

    %% ---- Tool layer / backends ----
    SEARCH --> TL["Granular LangChain tools<br/>(tool_policy.collect_tools)"]:::tool
    TL --> KW["keyword_search"]:::backend
    TL --> SEM["semantic_search"]:::backend
    TL --> NEO["neo4j_search / get_by_id / explore"]:::backend
    TL --> SPA["spatial_search"]:::backend
    TL --> OGD["opengeodata_search"]:::backend
    TL --> MCP["MCP tools (optional)<br/>data · spatial · image · search"]:::backend

    KW & SEM & NEO & SPA & OGD --> RP["rag_pipeline/search/*"]:::backend
    MCP --> MCPS["MCP_server (FastMCP, port 8000)"]:::backend
```

## Cross-cutting concerns

```mermaid
flowchart LR
    classDef infra fill:#e8eaed,stroke:#5f6368,color:#1a1a1a;

    A["Every agent invocation<br/>(invoke_agent_with_payload_fallback)"]:::infra
    A --> M["Memory<br/>InMemorySaver checkpointer<br/>hierarchical thread ids<br/>thread::search::1, thread::analysis_tool"]:::infra
    A --> R["Robustness<br/>messages→legacy payload fallback<br/>recursion limit (default 60)<br/>AgentInvocationError + diagnostics"]:::infra
    A --> T["Streaming trace<br/>trace_context → queue.Queue<br/>emit_trace_event → SSE events<br/>status·decision·tool_call·tool_result·final_answer"]:::infra
```

### Notes

- **Same tool, three depths.** `search_agent_evidence` appears under the
  Orchestrator, AnalysisAgent, and CodeAgent — SearchAgent can be reached at up to
  3 nesting levels (Orchestrator → Analysis → Code → Search). This is why the
  recursion-limit + diagnostics machinery exists.
- **Two tool strategies.** `granular` (per-backend tools, shown above) vs
  `full_pipeline` (a single `rag_tool` wrapping the whole RAG pipeline).
- **Two-layer tool selection.** `collect_tools` *instantiates* what's available;
  `select_allowed_tools` *filters* by intent (name-sets in `graph_state.py`).
- **Clean dependency direction.** `agent_runtime` never imports `rag_pipeline`
  directly; backends are reached only through the granular tool wrappers.
```
