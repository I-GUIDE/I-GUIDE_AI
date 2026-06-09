# I-GUIDE Agent — Architecture (as built, Phases 0–3)

A **hybrid LangGraph system**: a thin `StateGraph` triages each request and either
**fast-paths** trivial inputs (one direct LLM call) or hands substantive work to an
**LLM orchestrator** coordinating search / analysis / code sub-agents (every agent is a
`create_agent` graph). Cross-cutting: shared dedup'd evidence, self-healing history,
one unified LLM, and `AGENT_DEV`-gated execution-state streaming.

---

## 1 · Request lifecycle (hybrid graph)

```mermaid
flowchart TD
    classDef entry fill:#dbeafe,stroke:#2563eb,color:#0b2545;
    classDef decision fill:#fef9c3,stroke:#ca8a04,color:#422006;
    classDef agent fill:#ffe4e6,stroke:#e11d48,color:#4c0519;
    classDef out fill:#dcfce7,stroke:#16a34a,color:#052e16;

    U(["👤 User query"]):::entry
    U --> FL["Flask API<br/>/agent/chat · /agent/chat/stream"]:::entry
    FL --> SVC["agent_chat_service<br/>persistent memory · chat history"]:::entry
    SVC --> GR["graph_runtime<br/>run_agent_query · stream_agent_query_events"]:::entry
    GR --> HG

    subgraph HG["🧭 Hybrid Orchestrator Graph — StateGraph(OrchestratorState)"]
        direction TB
        TRI{"triage<br/>is_trivial_query()"}:::decision
        TRI -->|"trivial<br/>greeting / chit-chat"| FAST["fast_answer<br/>1 direct LLM call · no tools"]:::agent
        TRI -->|"substantive"| ORCH["orchestrate<br/>agents-as-tools — see §2"]:::agent
    end

    FAST --> OUT
    ORCH --> OUT
    OUT["📦 final state → response dict<br/>+ terminal SSE 'completed'"]:::out
    OUT -->|"answer + execution-state events"| U
```

---

## 2 · Orchestrate path — agents-as-tools

```mermaid
flowchart TD
    classDef agent fill:#ffe4e6,stroke:#e11d48,color:#4c0519;
    classDef tool fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef quality fill:#fef3c7,stroke:#d97706,color:#451a03;
    classDef store fill:#e2e8f0,stroke:#475569,color:#0f172a;

    ORCH["🧠 OrchestratorAgent<br/>create_agent · LLM-driven"]:::agent

    ORCH -->|tool| MEM["answer_from_memory<br/>if chat history"]:::tool
    ORCH -->|tool| SE1["search_agent_evidence"]:::tool
    ORCH -->|tool| AA["analysis_agent_answer"]:::tool
    ORCH -.->|"if attached files"| FT["file · QGIS tools"]:::tool

    SE1 --> SA["🔎 SearchAgent<br/>create_agent"]:::agent
    AA --> AN["🧩 AnalysisAgent<br/>create_agent"]:::agent

    AN -->|tool| SE2["search_agent_evidence"]:::tool
    AN -->|tool| RR1["rerank_evidence"]:::quality
    AN -->|tool| AUD1["audit_answer_grounding"]:::quality
    AN -->|tool| CA["code_agent_answer"]:::tool
    SE2 --> SA
    CA --> CODE["💻 CodeAgent<br/>create_agent"]:::agent
    CODE -->|tool| SE3["search_agent_evidence"]:::tool
    SE3 --> SA

    DS[("shared search_invocations<br/>dedup by query")]:::store
    SE1 -.-> DS
    SE2 -.-> DS
    SE3 -.-> DS
```

---

## 3 · SearchAgent tools & backends

```mermaid
flowchart LR
    classDef agent fill:#ffe4e6,stroke:#e11d48,color:#4c0519;
    classDef tool fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef quality fill:#fef3c7,stroke:#d97706,color:#451a03;
    classDef backend fill:#f3e8ff,stroke:#9333ea,color:#3b0764;

    SA["🔎 SearchAgent"]:::agent
    SA --> KW["keyword_search"]:::tool
    SA --> SEM["semantic_search"]:::tool
    SA --> NEO["neo4j_search"]:::tool
    SA --> SP["spatial_search"]:::tool
    SA --> OGD["opengeodata_search"]:::tool
    SA --> RR["rerank_evidence"]:::quality
    SA --> AUD["audit_answer_grounding"]:::quality
    SA --> SK["skills · load_skill"]:::tool

    KW --> OS[("OpenSearch BM25")]:::backend
    SEM --> EMB["embedding server :5001"]:::backend
    EMB --> OSV[("OpenSearch kNN")]:::backend
    NEO --> T2C["Text2Cypher · agent LLM<br/>→ keyword fallback"]:::backend
    T2C --> N4[("Neo4j")]:::backend
    SP --> GEO[("geocode + geo_shape")]:::backend
    OGD --> STAC[("STAC / OGC / CKAN / CMR")]:::backend
```

---

## 4 · Cross-cutting runtime (every agent)

```mermaid
flowchart TB
    classDef infra fill:#e2e8f0,stroke:#475569,color:#0f172a;
    classDef llm fill:#cffafe,stroke:#0891b2,color:#083344;

    AG["Every agent = create_agent<br/>compiled LangGraph ReAct graph"]:::infra
    AG --> MW["wrap_model_call middleware<br/>history self-healing<br/>drops dangling tool_calls"]:::infra
    AG --> CP["InMemorySaver checkpointer<br/>hierarchical thread ids<br/>(…::orchestrator, …::analysis_search)"]:::infra
    AG --> RB["Robustness<br/>tools return errors (no raise)<br/>recursion limit 60 + diagnostics<br/>no retry on tool-ordering 400"]:::infra
    AG --> LLM["build_default_llm · ChatOpenAI<br/>VLLM_* → OPENAI_*<br/>(also used by Text2Cypher)"]:::llm
```

---

## 5 · Streaming tiers (`AGENT_DEV`)

```mermaid
flowchart LR
    classDef infra fill:#e2e8f0,stroke:#475569,color:#0f172a;
    classDef status fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef detail fill:#fde68a,stroke:#d97706,color:#451a03;

    EV["emit_trace_event / callbacks<br/>trace_context → queue.Queue"]:::infra --> GATE{"AGENT_DEV?<br/>per-request flag or env"}:::infra
    GATE -->|"always"| ST["STATUS tier<br/>node_started / node_completed<br/>final_answer · completed · error"]:::status
    GATE -->|"on"| DT["DETAIL tier<br/>tool_call · tool_result<br/>llm_interaction · route_trace"]:::detail
    ST --> SSE["SSE → Flask relay → dashboard<br/>(node chip · trace log)"]:::infra
    DT --> SSE
```

---

## 6 · End-to-end sequence (substantive query)

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant G as Hybrid Graph
    participant O as OrchestratorAgent
    participant S as SearchAgent
    participant A as AnalysisAgent
    U->>G: query
    G->>G: triage → "substantive"
    G->>O: orchestrate (node_started)
    O->>S: search_agent_evidence(query)
    Note over S: keyword tool-filter (no LLM router)<br/>dedup cache on repeat
    S-->>O: evidence
    O->>A: analysis_agent_answer(query, evidence)
    opt quality
        A->>A: rerank_evidence / audit_answer_grounding
    end
    A-->>O: grounded answer
    O-->>G: final answer (node_completed)
    G-->>U: response + STATUS/DETAIL SSE
```

---

### Legend
🧠 / 🔎 / 🧩 / 💻 agents (`create_agent`) · 🟩 tools · 🟨 quality (rerank/audit) ·
🟪 search backends · ⬜ runtime infra. Trivial queries skip §2–§3 entirely via the
fast path. `full_pipeline` / `rag_tool` remain only as a deprecated path (not shown).
