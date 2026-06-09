# I-GUIDE Agent — Architecture (as built)

A **hybrid LangGraph system**. A thin graph triages each request: trivial inputs are
**fast-pathed** (one direct LLM call); substantive inputs go to the **supervisor-over-peers**
graph (default), where **search / analyze / code** are same-level peers that share one typed
state, an LLM **supervisor** loops over them, peers can **request** capabilities they need, and
a dedicated **synthesize** step composes the final answer. A legacy **agents-as-tools** path
remains behind `AGENT_SUPERVISOR=0`.

---

## 1 · Request lifecycle & dispatch

```mermaid
flowchart TD
    classDef entry fill:#dbeafe,stroke:#2563eb,color:#0b2545;
    classDef decision fill:#fef9c3,stroke:#ca8a04,color:#422006;
    classDef agent fill:#ffe4e6,stroke:#e11d48,color:#4c0519;
    classDef gph fill:#ede9fe,stroke:#7c3aed,color:#2e1065;
    classDef out fill:#dcfce7,stroke:#16a34a,color:#052e16;

    U(["👤 User query"]):::entry
    U --> FL["Flask API<br/>/agent/chat · /agent/chat/stream"]:::entry
    FL --> SVC["agent_chat_service<br/>memory · chat history"]:::entry
    SVC --> GR["graph_runtime<br/>run_agent_query · stream_agent_query_events"]:::entry
    GR --> HG

    subgraph HG["🧭 Hybrid graph"]
        direction TB
        TRI{"triage<br/>is_trivial_query()"}:::decision
        TRI -->|trivial| FAST["fast_answer<br/>1 direct LLM call"]:::agent
        TRI -->|substantive| ORCH{"orchestrate"}:::decision
    end

    ORCH -->|default| SUP["Supervisor-over-peers graph — §2<br/>AGENT_SUPERVISOR not 0"]:::gph
    ORCH -->|legacy| AAT["Agents-as-tools — §7<br/>AGENT_SUPERVISOR=0"]:::gph

    FAST --> OUT["📦 response dict + terminal SSE 'completed'"]:::out
    SUP --> OUT
    AAT --> OUT
    OUT --> U
```

---

## 2 · Supervisor-over-peers (default)

```mermaid
flowchart TD
    classDef sup fill:#ede9fe,stroke:#7c3aed,color:#2e1065;
    classDef agent fill:#ffe4e6,stroke:#e11d48,color:#4c0519;
    classDef store fill:#e2e8f0,stroke:#475569,color:#0f172a;
    classDef done fill:#dcfce7,stroke:#16a34a,color:#052e16;

    START((start)) --> SUP
    SUP{"🧭 supervisor<br/>1. drain needs queue (FIFO)<br/>2. else LLM decide"}:::sup
    SUP -->|search| SE["🔎 search<br/>retrieve internal KB + rerank"]:::agent
    SUP -->|analyze| AN["🧩 analyze<br/>run GIS/stat workflow"]:::agent
    SUP -->|code| CO["💻 code<br/>runnable code + deps"]:::agent
    SUP -->|done| SY["✍️ synthesize<br/>compose answer + grounding audit"]:::done
    SE --> SUP
    AN --> SUP
    CO --> SUP
    SY --> EN((end))

    ST[("shared state<br/>evidence · analysis_results · code_result · needs")]:::store
    SE -.->|writes evidence| ST
    AN -.->|writes analysis_results| ST
    CO -.->|writes code_result| ST
    ST -.->|distilled view| SUP
    ST -.->|reads all| SY

    AN -.->|request_capability| RQ{{"needs → queue"}}:::sup
    CO -.->|request_capability| RQ
    RQ -.-> SUP
```

---

## 3 · Needs / request loop (a peer asks, the supervisor arranges)

```mermaid
sequenceDiagram
    autonumber
    participant S as supervisor
    participant C as code peer
    participant SE as search peer
    participant SY as synthesize
    S->>C: route to code (decider)
    C-->>S: request_capability("search","need datasets")
    Note over S: needs queue (FIFO): [search, code]
    S->>SE: fulfill request → search + rerank
    SE-->>S: evidence written to shared state
    S->>C: re-run code (now grounded)
    C-->>S: code_result
    S->>SY: done → compose answer + audit
    SY-->>S: final_answer
```

---

## 4 · Single-responsibility capabilities

```mermaid
flowchart LR
    classDef agent fill:#ffe4e6,stroke:#e11d48,color:#4c0519;
    classDef op fill:#fef3c7,stroke:#d97706,color:#451a03;
    classDef backend fill:#f3e8ff,stroke:#9333ea,color:#3b0764;

    SE["🔎 search<br/>retrieve evidence"]:::agent --> B[("internal KB<br/>keyword · semantic · neo4j<br/>spatial · opengeodata")]:::backend
    SE --> RR["rerank (bundled)"]:::op
    AN["🧩 analyze<br/>RUN workflow → analysis_results"]:::agent --> G[("QGIS/PyQGIS<br/>+ spatial-analysis MCP")]:::backend
    CO["💻 code<br/>→ code_result"]:::agent
    SY["✍️ synthesize<br/>compose grounded answer<br/>(ANALYSIS_AGENT_PROMPT format)"]:::agent --> AU["grounding audit (bundled)"]:::op

    AN -.->|request_capability| SE
    CO -.->|request_capability| SE
    CO -.->|request_capability| AN
```

---

## 5 · Cross-cutting runtime (every agent)

```mermaid
flowchart TB
    classDef infra fill:#e2e8f0,stroke:#475569,color:#0f172a;
    classDef llm fill:#cffafe,stroke:#0891b2,color:#083344;

    AG["Every agent = create_agent<br/>compiled LangGraph ReAct graph"]:::infra
    AG --> MW["wrap_model_call middleware<br/>history self-healing<br/>drops dangling tool_calls"]:::infra
    AG --> CP["InMemorySaver checkpointer<br/>hierarchical thread ids"]:::infra
    AG --> RB["Robustness<br/>tools return errors (no raise)<br/>recursion limit 60 + diagnostics<br/>no retry on tool-ordering 400"]:::infra
    AG --> LLM["build_default_llm · ChatOpenAI<br/>VLLM_* → OPENAI_*<br/>(also Text2Cypher)"]:::llm
```

---

## 6 · Streaming tiers (`AGENT_DEV`)

```mermaid
flowchart LR
    classDef infra fill:#e2e8f0,stroke:#475569,color:#0f172a;
    classDef status fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef detail fill:#fde68a,stroke:#d97706,color:#451a03;

    EV["node events + callbacks<br/>(supervisor · search · analyze · code · synthesize)"]:::infra --> GATE{"AGENT_DEV?<br/>per-request or env"}:::infra
    GATE -->|"always"| ST["STATUS tier<br/>node_started/completed<br/>final_answer · completed · error"]:::status
    GATE -->|"on"| DT["DETAIL tier<br/>tool_call · tool_result<br/>llm_interaction · route_trace"]:::detail
    ST --> SSE["SSE → Flask 'node' relay → dashboard<br/>(node chip · trace log)"]:::infra
    DT --> SSE
```

---

## 7 · Legacy agents-as-tools (`AGENT_SUPERVISOR=0`)

```mermaid
flowchart TD
    classDef agent fill:#ffe4e6,stroke:#e11d48,color:#4c0519;
    classDef tool fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef quality fill:#fef3c7,stroke:#d97706,color:#451a03;

    ORCH["🧠 OrchestratorAgent (LLM)"]:::agent
    ORCH -->|tool| MEM["answer_from_memory"]:::tool
    ORCH -->|tool| SE["search_agent_evidence"]:::tool
    ORCH -->|tool| AA["analysis_agent_answer"]:::tool
    SE --> SA["🔎 SearchAgent"]:::agent
    AA --> AN["🧩 AnalysisAgent"]:::agent
    AN -->|tool| SE2["search_agent_evidence ⚠️ nested"]:::tool
    AN -->|tool| RR["rerank · audit"]:::quality
    AN -->|tool| CA["code_agent_answer ⚠️ nested"]:::tool
    CA --> CODE["💻 CodeAgent"]:::agent
    SE2 --> SA
```

---

### Legend
🟪 supervisor/dispatch · 🔴 agents (`create_agent`) · 🟩 tools / done · 🟡 operators (rerank/audit) ·
🟣 backends · ⬜ runtime infra · 🟦 entry. In supervisor mode, peers stay same-level and coordinate
through shared state + the needs queue; rerank is bundled into search, grounding audit into synthesize.
