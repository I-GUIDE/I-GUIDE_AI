import os
import logging
import json
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_file, stream_with_context
from flask_cors import CORS
from flasgger import Swagger

from rag_pipeline.agent_file_store import require_file_record, resolve_file_id, save_uploaded_file
from rag_pipeline.agent_chat_service import run_agent_chat, stream_agent_chat_events
from rag_pipeline.pipeline import run_pipeline

app = Flask(__name__)

# Load environment variables
load_dotenv()

CORS(app)

swagger = Swagger(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API key auth
# ---------------------------------------------------------------------------

def _coalesce(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _get_agent_chat_api_key() -> str:
    return str(os.getenv("AGENT_CHAT_API_KEY") or "").strip()


def _extract_presented_api_key() -> str:
    header_key = str(request.headers.get("X-API-KEY") or "").strip()
    if header_key:
        return header_key
    auth = str(request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _require_agent_chat_api_key() -> None:
    expected = _get_agent_chat_api_key()
    if not expected:
        return  # auth disabled when key is not configured
    presented = _extract_presented_api_key()
    if not presented or presented != expected:
        raise PermissionError("Forbidden: invalid API key.")


# ---------------------------------------------------------------------------
# Request / response normalization
# ---------------------------------------------------------------------------

def _normalize_agent_chat_request(data: dict) -> dict:
    """Normalize camelCase frontend fields to internal snake_case, accepting both."""
    user_query = _coalesce(data.get("userQuery"), data.get("user_input"))
    memory_id = _coalesce(data.get("memoryId"), data.get("memory_id"))
    thread_id = _coalesce(data.get("threadId"), data.get("thread_id"))
    conversation_name = _coalesce(data.get("conversationName"), data.get("conversation_name"))
    recent_k = _coalesce(data.get("recentK"), data.get("recent_k"))
    tool_strategy = _coalesce(data.get("toolStrategy"), data.get("tool_strategy"), "granular")
    include_mcp_tools = bool(_coalesce(data.get("includeMcpTools"), data.get("include_mcp_tools"), False))
    mcp_modules = _coalesce(data.get("mcpModules"), data.get("mcp_modules"))
    enabled_search_methods = _coalesce(data.get("enabledSearchMethods"), data.get("enabled_search_methods"))
    use_persistent_memory = bool(_coalesce(data.get("usePersistentMemory"), data.get("use_persistent_memory"), True))
    smart_tool_routing = bool(_coalesce(data.get("smartToolRouting"), data.get("smart_tool_routing"), True))
    forced_intent = _coalesce(data.get("forcedIntent"), data.get("forced_intent"))
    file_paths = _coalesce(data.get("filePaths"), data.get("file_paths"))
    file_ids = _coalesce(data.get("fileIds"), data.get("file_ids"))
    skill_roots = _coalesce(data.get("skillPaths"), data.get("skill_paths"), data.get("skillRoots"), data.get("skill_roots"))
    verbose = bool(_coalesce(data.get("verbose"), False))

    return {
        "user_query": str(user_query).strip() if user_query is not None else "",
        "memory_id": str(memory_id).strip() if memory_id is not None else None,
        "thread_id": str(thread_id).strip() if thread_id is not None else None,
        "conversation_name": str(conversation_name).strip() if conversation_name is not None else None,
        "recent_k": recent_k,
        "tool_strategy": str(tool_strategy).strip(),
        "include_mcp_tools": include_mcp_tools,
        "mcp_modules": mcp_modules,
        "enabled_search_methods": enabled_search_methods,
        "use_persistent_memory": use_persistent_memory,
        "smart_tool_routing": smart_tool_routing,
        "forced_intent": forced_intent,
        "file_paths": file_paths,
        "file_ids": file_ids,
        "skill_roots": skill_roots,
        "verbose": verbose,
    }


def _format_agent_chat_result(payload: dict) -> dict:
    """Align output shape to the legacy Node /llm/search result object."""
    answer = payload.get("answer") or ""
    elements = payload.get("elements") or []
    retrieval_steps = payload.get("retrievalSteps") or payload.get("retrieval_steps") or []
    react_history = payload.get("reactHistory") or payload.get("react_history") or []

    formatted = {
        "answer": answer,
        "message_id": payload.get("message_id"),
        "elements": elements,
        "count": int(payload.get("count") or (len(elements) if isinstance(elements, list) else 0)),
        "retrievalSteps": retrieval_steps,
        "reactHistory": react_history,
        "memoryId": payload.get("memoryId") or payload.get("memory_id"),
        "threadId": payload.get("threadId") or payload.get("thread_id"),
        "routeTrace": payload.get("routeTrace") or payload.get("route_trace") or {},
        "artifacts": payload.get("artifacts") or {},
        "agent_result": payload.get("agent_result") or {},
        "fileIds": payload.get("fileIds") or payload.get("file_ids") or [],
        "filePaths": payload.get("filePaths") or payload.get("file_paths") or [],
        "skillPaths": payload.get("skillPaths") or payload.get("skill_roots") or [],
        "availableSkills": payload.get("availableSkills") or payload.get("available_skills") or [],
    }
    warning = payload.get("warning")
    if warning:
        formatted["warning"] = warning
    return formatted


def _humanize_stage(value: str) -> str:
    text = str(value or "").strip().replace("_", " ")
    return text[:1].upper() + text[1:] if text else "Status"


def _category_for_agent_role(role: object) -> str:
    role_text = str(role or "").strip().lower()
    if role_text in {"search", "search_agent"} or "search" in role_text:
        return "search"
    if role_text in {"analysis", "analysis_agent", "code", "code_agent", "verification"}:
        return "analysis"
    return "analysis"


def _short_text(value: object, limit: int = 900) -> str:
    text = value if isinstance(value, str) else str(value or "")
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit]}..."


def _agent_trace_event(payload: dict) -> dict:
    """Convert low-level LLM/tool payloads into browser-friendly trace events."""
    kind = str(payload.get("kind") or payload.get("type") or "").strip()
    if kind == "llm_tool_decision":
        calls = payload.get("tool_calls") or []
        if not calls and payload.get("name"):
            calls = [{"name": payload.get("name"), "args": payload.get("args") or {}}]
        return {
            "type": "tool_call",
            "agent": payload.get("agent") or "",
            "label": "LLM tool decision",
            "message": "; ".join(
                f"{call.get('name', 'unknown_tool')}({json.dumps(call.get('args') or {}, ensure_ascii=True, default=str)})"
                for call in calls
                if isinstance(call, dict)
            ),
            "tool_calls": calls,
            "detail": payload,
        }
    if kind == "tool_result":
        tool_name = payload.get("tool_name") or payload.get("name") or ""
        content = payload.get("content")
        parsed = payload.get("parsed")
        return {
            "type": "tool_result",
            "agent": payload.get("agent") or "",
            "label": f"Tool result {tool_name}".strip(),
            "message": _short_text(parsed if parsed is not None else content),
            "tool_name": tool_name,
            "detail": payload,
        }
    if kind == "llm_message":
        return {
            "type": "llm_message",
            "agent": payload.get("agent") or "",
            "label": "LLM message",
            "message": _short_text(payload.get("content") or ""),
            "detail": payload,
        }
    if kind == "llm_start":
        return {
            "type": "llm_start",
            "agent": payload.get("agent") or "",
            "label": payload.get("label") or "LLM request",
            "message": _short_text(payload.get("message") or "LLM request started"),
            "detail": payload,
        }
    if kind == "llm_error":
        return {
            "type": "llm_error",
            "agent": payload.get("agent") or "",
            "label": payload.get("label") or "LLM error",
            "message": _short_text(payload.get("message") or payload),
            "detail": payload,
        }
    if kind in {"mcp_call_start", "mcp_call_end", "mcp_call_error"}:
        tool_name = payload.get("tool_name") or payload.get("mcp_tool_name") or "mcp tool"
        labels = {
            "mcp_call_start": "MCP call started",
            "mcp_call_end": "MCP call completed",
            "mcp_call_error": "MCP call failed",
        }
        return {
            "type": kind,
            "agent": payload.get("agent") or "",
            "label": payload.get("label") or labels[kind],
            "message": _short_text(payload.get("message") or tool_name),
            "tool_name": tool_name,
            "detail": payload,
        }
    if kind == "tool_error":
        tool_name = payload.get("tool_name") or payload.get("name") or ""
        return {
            "type": "tool_error",
            "agent": payload.get("agent") or "",
            "label": f"Tool error {tool_name}".strip(),
            "message": _short_text(payload.get("message") or payload),
            "tool_name": tool_name,
            "detail": payload,
        }
    if kind == "agent_route_decision":
        route_trace = payload.get("route_trace") or {}
        route = route_trace.get("route") or payload.get("route") or "unknown"
        intent = route_trace.get("intent") or payload.get("intent") or "unknown"
        allowed = payload.get("allowed_tools") or route_trace.get("allowed_tools") or []
        extra = f"; tools={', '.join(map(str, allowed[:6]))}" if isinstance(allowed, list) and allowed else ""
        return {
            "type": "route_decision",
            "agent": payload.get("agent") or "",
            "label": "Route decision",
            "message": f"route={route}; intent={intent}{extra}",
            "detail": payload,
        }
    return {
        "type": kind or "message",
        "agent": payload.get("agent") or "",
        "label": kind or "Trace",
        "message": _short_text(payload.get("content") or payload.get("message") or payload),
        "detail": payload,
    }


def _diagnostic_agent_trace_events(diagnostics: object) -> list[dict]:
    """Expand recursion diagnostics into readable SSE trace events."""
    if not isinstance(diagnostics, dict):
        return []
    events: list[dict] = []
    call_counts = diagnostics.get("tool_call_counts")
    if isinstance(call_counts, dict) and call_counts:
        events.append(
            {
                "type": "diagnostic_summary",
                "agent": "agent",
                "label": "Recursion diagnostic",
                "message": "Tool calls observed: "
                + ", ".join(f"{name} x{count}" for name, count in sorted(call_counts.items())),
                "detail": {
                    "thread_id": diagnostics.get("thread_id"),
                    "recursion_limit": diagnostics.get("recursion_limit"),
                    "tool_call_counts": call_counts,
                },
            }
        )

    for idx, item in enumerate(diagnostics.get("recent_messages") or [], start=1):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        content = _short_text(item.get("content") or "")
        tool_calls = item.get("tool_calls")
        name = str(item.get("name") or "").strip()
        if role in {"human", "humanmessage", "user"}:
            events.append(
                {
                    "type": "user_message",
                    "agent": "",
                    "label": "User",
                    "message": content,
                    "sequence": idx,
                    "detail": item,
                }
            )
        elif isinstance(tool_calls, list) and tool_calls:
            events.append(
                {
                    "type": "tool_call",
                    "agent": "llm",
                    "label": "LLM tool decision",
                    "message": "; ".join(
                        f"{call.get('name', 'unknown_tool')}({json.dumps(call.get('args') or {}, ensure_ascii=True, default=str)})"
                        for call in tool_calls
                        if isinstance(call, dict)
                    ),
                    "sequence": idx,
                    "tool_calls": tool_calls,
                    "detail": item,
                }
            )
        elif role in {"tool", "toolmessage"} or name:
            events.append(
                {
                    "type": "tool_result",
                    "agent": "tool",
                    "label": f"Tool result {name}".strip(),
                    "message": content,
                    "sequence": idx,
                    "tool_name": name,
                    "detail": item,
                }
            )
        elif role in {"ai", "aimessage", "assistant"}:
            events.append(
                {
                    "type": "llm_message",
                    "agent": "llm",
                    "label": "LLM message",
                    "message": content or "(empty message)",
                    "sequence": idx,
                    "detail": item,
                }
            )
    return events


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _format_elements(retrieved_documents):
    """Convert internal evidence entries into the simplified element payload expected by the UI."""
    elements = []
    opengeodata_count = 0
    for entry in retrieved_documents:
        document = (entry or {}).get("document") or {}
        metadata = (entry or {}).get("metadata") or {}
        source = entry.get("source", "unknown")
        resource_type = document.get("resource-type") or document.get("element_type")

        if source == "opengeodata" or resource_type == "opengeodata":
            opengeodata_count += 1

        elements.append(
            {
                "_id": document.get("doc_id") or metadata.get("hit_id"),
                "_score": entry.get("score"),
                "contributor": document.get("contributor"),
                "contents": document.get("contents"),
                "resource-type": resource_type,
                "title": document.get("title"),
                "authors": document.get("authors") or [],
                "tags": document.get("tags") or [],
                "thumbnail-image": document.get("thumbnail-image") or document.get("thumbnail_image"),
                "source": source,
            }
        )
    if opengeodata_count > 0:
        logger.info(f"Formatted {opengeodata_count} opengeodata results in {len(elements)} total elements")
    return elements


def _pipeline_params(data):
    return data.get('params', {
        "top_k": 8,
        "max_context_tokens": 6000,
        "enable_llm_reranker": True
    })


def _pipeline_response(result):
    evidence = result.get("evidence", {}) or {}
    retrieved_documents = evidence.get("retrieved_documents", []) or []
    elements = _format_elements(retrieved_documents)
    trace = result.get("trace_observability", {}) or {}
    planner = result.get("planner_reasoning", {}) or {}

    return {
        "answer": (
            (result.get("answer") or {}).get("final_composed_answer")
            or "No answer"
        ),
        "message_id": str(uuid4()),
        "elements": elements,
        "count": len(elements),
        "retrievalSteps": trace.get("retrieval_routing_decisions") or [],
        "reactHistory": planner.get("react_history") or trace.get("react_history") or [],
    }


def _parse_mcp_modules(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise ValueError("mcp_modules must be a list of strings or a comma-separated string")


def _parse_enabled_search_methods(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise ValueError("enabled_search_methods must be a list of strings or a comma-separated string")


def _sse_event(name, data):
    payload = json.dumps(data or {}, ensure_ascii=True, default=str)
    return f"event: {name}\ndata: {payload}\n\n"


def _normalize_uploaded_files():
    files = []
    files.extend(request.files.getlist("files"))
    single = request.files.get("file")
    if single is not None:
        files.append(single)
    return [item for item in files if getattr(item, "filename", None)]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint.

    ---
    tags:
      - Health
    produces:
      - application/json
    responses:
      200:
        description: Service is healthy.
        schema:
          type: object
          properties:
            status:
              type: string
              example: healthy
            service:
              type: string
              example: rag-pipeline
    """
    return jsonify({"status": "healthy", "service": "rag-pipeline"}), 200


@app.route('/agent/dashboard', methods=['GET'])
def agent_dashboard():
    """Serve the local streaming agent dashboard."""
    dashboard_path = Path(__file__).resolve().parent.parent / "examples" / "agent_chat_stream_demo.html"
    return send_file(dashboard_path)


@app.route('/agent/files/upload', methods=['POST'])
def upload_agent_files():
    """
    Upload one or more files for the agent to inspect.

    The returned `file_id` values can be passed to `/agent/chat` or
    `/agent/chat/stream` via the JSON field `fileIds`.

    ---
    tags:
      - Agent Files
    consumes:
      - multipart/form-data
    produces:
      - application/json
    parameters:
      - in: formData
        name: file
        type: file
        required: false
        description: Single file upload.
      - in: formData
        name: files
        type: file
        required: false
        description: Multi-file upload (repeat this field per file).
    responses:
      200:
        description: Upload succeeded.
        schema:
          type: object
          properties:
            files:
              type: array
              items:
                type: object
                properties:
                  file_id:
                    type: string
                    example: file_0123456789ab
                  filename:
                    type: string
                    example: data.csv
                  kind:
                    type: string
                    example: upload
                  size_bytes:
                    type: integer
                    example: 1234
                  download_url:
                    type: string
                    example: /agent/files/file_0123456789ab/download
            count:
              type: integer
              example: 2
      400:
        description: No files provided or invalid request.
      500:
        description: Internal server error.
    """
    try:
        files = _normalize_uploaded_files()
        if not files:
            return jsonify({"error": "No files uploaded. Use form field `file` or `files`."}), 400

        uploaded = [save_uploaded_file(file_storage) for file_storage in files]
        return jsonify({"files": uploaded, "count": len(uploaded)}), 200
    except ValueError as e:
        logger.error(f"Agent file upload validation error: {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error uploading agent files: {str(e)}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/agent/files/<file_id>/download', methods=['GET'])
def download_agent_file(file_id):
    """
    Download an uploaded (or generated) file by file_id.

    ---
    tags:
      - Agent Files
    produces:
      - application/octet-stream
    parameters:
      - in: path
        name: file_id
        type: string
        required: true
        description: File identifier returned by `/agent/files/upload` or `write_output_file`.
    responses:
      200:
        description: File bytes.
      404:
        description: Unknown file_id.
      500:
        description: Internal server error.
    """
    try:
        record = require_file_record(file_id)
        path = resolve_file_id(file_id)
        mimetype = None
        suffix = Path(record.get("filename", "")).suffix.lower()
        if suffix == ".csv":
            mimetype = "text/csv"
        elif suffix == ".json":
            mimetype = "application/json"
        elif suffix == ".png":
            mimetype = "image/png"
        elif suffix in {".txt", ".md", ".py"}:
            mimetype = "text/plain"
        return send_file(path, as_attachment=True, download_name=record.get("filename") or path.name, mimetype=mimetype)
    except ValueError as e:
        logger.error(f"Agent file download validation error: {str(e)}")
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error downloading agent file {file_id}: {str(e)}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/query', methods=['POST'])
def query():
    """
Main RAG pipeline endpoint with LLM routing and optional reranking.

This endpoint accepts a natural language query and executes the full
retrieval-augmented generation (RAG) pipeline, including:
- retriever selection / routing
- optional LLM-based reranking
- context assembly
- answer synthesis
- trace and provenance collection

---
tags:
  - RAG
  - Query
consumes:
  - application/json
produces:
  - application/json

parameters:
  - in: body
    name: body
    required: true
    schema:
      type: object
      required:
        - user_input
      properties:
        user_input:
          type: string
          description: Natural language query from the user.
          example: "What is telecoupling in sustainability science?"
        memory_id:
          type: string
          nullable: true
          description: Optional session or memory identifier for conversational context.
          example: "session-abc123"
        params:
          type: object
          description: Parameters controlling retrieval and generation behavior.
          properties:
            top_k:
              type: integer
              default: 8
              description: Number of documents to retrieve.
            max_context_tokens:
              type: integer
              default: 6000
              description: Maximum token budget for assembled context.
            enable_llm_reranker:
              type: boolean
              default: true
              description: Whether to apply an LLM-based reranker to retrieved documents.
        session_context:
          type: object
          description: Arbitrary session-level context passed to the pipeline.
          example: {}
        recent_k:
          type: integer
          description: Number of recent conversational turns to include.
          example: 5
        extra_state:
          type: object
          description: Optional additional state injected into the pipeline.
          example: {}

responses:
  200:
    description: Query processed successfully.
    schema:
      type: object
      properties:
        answer:
          type: string
          description: Final composed answer generated by the RAG pipeline.
        message_id:
          type: string
          description: Unique identifier for this query-response pair.
        elements:
          type: array
          description: Retrieved and formatted evidence elements.
          items:
            type: object
        count:
          type: integer
          description: Number of retrieved evidence elements.
        retrievalSteps:
          type: array
          description: Retrieval routing and decision trace.
          items:
            type: object
        reactHistory:
          type: array
          description: ReAct-style reasoning or planner trace, if available.
          items:
            type: object

  400:
    description: Invalid request (e.g., missing user_input).
    schema:
      type: object
      properties:
        error:
          type: string

  500:
    description: Internal server error during pipeline execution.
    schema:
      type: object
      properties:
        error:
          type: string
"""
    try:
        data = request.get_json() or {}
        user_input = data.get('user_input')

        if not user_input:
            return jsonify({"error": "user_input is required"}), 400

        logger.info(f"Processing query: {user_input[:100]}...")

        result = run_pipeline(
            user_input=user_input,
            memory_id=data.get('memory_id'),
            params=_pipeline_params(data),
            session_context=data.get('session_context', {}),
            recent_k=data.get('recent_k'),
            extra_state=data.get('extra_state', {})
        )
        response = _pipeline_response(result)

        logger.info(f"Query processed successfully. Retrieved {response['count']} documents.")
        return jsonify(response), 200

    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/agent/chat', methods=['POST'])
def agent_chat():
    """
    Chat endpoint backed by the LangChain agent with optional persistent memory.

    Accepts both camelCase (frontend) and snake_case field names.

    ---
    tags:
      - Agent Chat
    consumes:
      - application/json
    produces:
      - application/json
    parameters:
      - in: header
        name: X-API-KEY
        type: string
        required: true
        description: API key for agent chat endpoints (env var AGENT_CHAT_API_KEY).
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - userQuery
          properties:
            userQuery:
              type: string
              example: What datasets are available for Chicago crime?
            memoryId:
              type: string
              nullable: true
              example: conversation-1
            threadId:
              type: string
              nullable: true
              example: chat-thread-1
            conversationName:
              type: string
              nullable: true
              example: Chicago agent chat
            recentK:
              type: integer
              nullable: true
              example: 8
            toolStrategy:
              type: string
              example: granular
            includeMcpTools:
              type: boolean
              example: false
            mcpModules:
              type: array
              items:
                type: string
              nullable: true
              example: ["search_tools", "data_tools"]
            enabledSearchMethods:
              type: array
              items:
                type: string
              nullable: true
              example: ["keyword_search", "semantic_search"]
            usePersistentMemory:
              type: boolean
              example: true
            smartToolRouting:
              type: boolean
              example: true
            forcedIntent:
              type: string
              nullable: true
              example: null
            filePaths:
              type: array
              items:
                type: string
              nullable: true
              example: ["./data/crime.csv"]
            fileIds:
              type: array
              items:
                type: string
              nullable: true
              example: ["file_0123456789ab"]
            skillPaths:
              type: array
              items:
                type: string
              nullable: true
              example: ["./skills"]
            verbose:
              type: boolean
              example: false
    responses:
      200:
        description: Agent chat response payload.
      400:
        description: Validation error (e.g., missing userQuery).
      403:
        description: Forbidden — invalid or missing API key.
      500:
        description: Internal server error.
    """
    try:
        try:
            _require_agent_chat_api_key()
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RuntimeError as exc:
            logger.error("Agent chat API key misconfigured: %s", exc)
            return jsonify({"error": "Server misconfiguration: API key not set"}), 500

        data = request.get_json() or {}
        normalized = _normalize_agent_chat_request(data)
        user_query = normalized["user_query"]
        if not user_query:
            return jsonify({"error": "Missing userQuery in request body."}), 400

        logger.info(f"Processing agent chat: {user_query[:100]}...")
        raw = run_agent_chat(
            user_input=user_query,
            thread_id=normalized.get("thread_id"),
            memory_id=normalized.get("memory_id"),
            conversation_name=normalized.get("conversation_name"),
            recent_k=normalized.get("recent_k"),
            tool_strategy=normalized.get("tool_strategy", "granular"),
            include_mcp_tools=bool(normalized.get("include_mcp_tools", False)),
            mcp_modules=_parse_mcp_modules(normalized.get("mcp_modules")),
            enabled_search_methods=_parse_enabled_search_methods(normalized.get("enabled_search_methods")),
            use_persistent_memory=bool(normalized.get("use_persistent_memory", True)),
            smart_tool_routing=bool(normalized.get("smart_tool_routing", True)),
            forced_intent=normalized.get("forced_intent"),
            file_paths=normalized.get("file_paths"),
            file_ids=normalized.get("file_ids"),
            skill_roots=normalized.get("skill_roots"),
            verbose=bool(normalized.get("verbose", False)),
        )
        return jsonify(_format_agent_chat_result(raw)), 200
    except ValueError as e:
        logger.error(f"Agent chat validation error: {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error processing agent chat: {str(e)}", exc_info=True)
        payload = {"error": f"Internal server error: {str(e)}"}
        diagnostics = getattr(e, "diagnostics", None)
        if diagnostics:
            payload["diagnostics"] = diagnostics
            diagnostic_text = diagnostics.get("readable_trace") if isinstance(diagnostics, dict) else None
            if diagnostic_text:
                payload["diagnosticText"] = diagnostic_text
        return jsonify(payload), 500


@app.route('/agent/chat/stream', methods=['POST'])
def agent_chat_stream():
    """
    Stream agent chat events using Server-Sent Events (SSE).

    The response is `text/event-stream` with blocks of the form:
    `event: <name>\\ndata: <json>\\n\\n`

    Node-compatible events: `status`, `result`, `error`.
    Additional categorized events: `routing`, `search`, `analysis`, `agent_trace`, `answer`, `file`.
    `agent_trace` includes live LLM messages, LLM tool decisions, and MCP call lifecycle events.

    ---
    tags:
      - Agent Chat
    consumes:
      - application/json
    produces:
      - text/event-stream
    parameters:
      - in: header
        name: X-API-KEY
        type: string
        required: true
        description: API key for agent chat endpoints (env var AGENT_CHAT_API_KEY).
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - userQuery
          properties:
            userQuery:
              type: string
              example: Inspect the attached CSV and summarize the main columns.
            memoryId:
              type: string
              nullable: true
              example: demo-session-1
            threadId:
              type: string
              nullable: true
              example: demo-thread-1
            conversationName:
              type: string
              nullable: true
            recentK:
              type: integer
              nullable: true
              example: 8
            toolStrategy:
              type: string
              example: granular
            includeMcpTools:
              type: boolean
              example: false
            mcpModules:
              type: array
              items:
                type: string
              nullable: true
            enabledSearchMethods:
              type: array
              items:
                type: string
              nullable: true
            usePersistentMemory:
              type: boolean
              example: true
            smartToolRouting:
              type: boolean
              example: true
            forcedIntent:
              type: string
              nullable: true
            filePaths:
              type: array
              items:
                type: string
              nullable: true
            fileIds:
              type: array
              items:
                type: string
              nullable: true
            skillPaths:
              type: array
              items:
                type: string
              nullable: true
            verbose:
              type: boolean
              example: false
    responses:
      200:
        description: SSE stream of status / result (or error) events.
      400:
        description: Validation error (returned as an SSE error event).
      403:
        description: Forbidden — invalid or missing API key.
      500:
        description: Internal server error.
    """
    try:
        try:
            _require_agent_chat_api_key()
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RuntimeError as exc:
            logger.error("Agent chat stream API key misconfigured: %s", exc)
            return jsonify({"error": "Server misconfiguration: API key not set"}), 500

        data = request.get_json() or {}
        normalized = _normalize_agent_chat_request(data)
        user_query = normalized["user_query"]
        if not user_query:
            def _single_error():
                yield _sse_event("error", {"error": "Missing userQuery in request body."})
            return Response(
                stream_with_context(_single_error()),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
            )

        logger.info(f"Streaming agent chat: {user_query[:100]}...")

        @stream_with_context
        def generate():
            error_emitted = False
            try:
                for item in stream_agent_chat_events(
                    user_input=user_query,
                    thread_id=normalized.get("thread_id"),
                    memory_id=normalized.get("memory_id"),
                    conversation_name=normalized.get("conversation_name"),
                    recent_k=normalized.get("recent_k"),
                    tool_strategy=normalized.get("tool_strategy", "granular"),
                    include_mcp_tools=bool(normalized.get("include_mcp_tools", False)),
                    mcp_modules=_parse_mcp_modules(normalized.get("mcp_modules")),
                    enabled_search_methods=_parse_enabled_search_methods(normalized.get("enabled_search_methods")),
                    use_persistent_memory=bool(normalized.get("use_persistent_memory", True)),
                    smart_tool_routing=bool(normalized.get("smart_tool_routing", True)),
                    forced_intent=normalized.get("forced_intent"),
                    file_paths=normalized.get("file_paths"),
                    file_ids=normalized.get("file_ids"),
                    skill_roots=normalized.get("skill_roots"),
                    verbose=bool(normalized.get("verbose", False)),
                ):
                    event_name = str(item.get("event") or "message")
                    payload = item.get("data") or {}
                    agent_role = item.get("agent_role") or payload.get("role")
                    node_name = item.get("node")

                    # Categorized events for richer frontend consumption
                    if event_name == "route_trace":
                        yield _sse_event("routing", {"type": "route_trace", "detail": payload})

                    if event_name == "status":
                        stage = payload.get("stage")
                        if stage in {"initialized", "intent_classified", "policy_resolved"}:
                            yield _sse_event("routing", {"type": stage, "detail": payload})

                    if event_name in {"subagent_started", "subagent_completed"}:
                        role = payload.get("role") or agent_role
                        category = _category_for_agent_role(role)
                        yield _sse_event(
                            category,
                            {"type": event_name, "agent": str(role or ""), "node": node_name, "detail": payload},
                        )

                    if event_name in {"tool_call", "tool_result", "tool_error"}:
                        category = _category_for_agent_role(agent_role)
                        display_agent_role = str(agent_role or payload.get("agent") or "")
                        yield _sse_event(
                            category,
                            {"type": event_name, "agent": display_agent_role, "node": node_name, "detail": payload},
                        )
                        trace_agent_role = display_agent_role
                        trace_kind = {
                            "tool_call": "llm_tool_decision",
                            "tool_result": "tool_result",
                            "tool_error": "tool_error",
                        }[event_name]
                        yield _sse_event(
                            "agent_trace",
                            _agent_trace_event(
                                {
                                    **payload,
                                    "kind": trace_kind,
                                    "agent": trace_agent_role,
                                }
                            ),
                        )

                    if event_name in {
                        "llm_interaction",
                        "llm_start",
                        "llm_error",
                        "decision",
                        "mcp_call_start",
                        "mcp_call_end",
                        "mcp_call_error",
                    }:
                        yield _sse_event("agent_trace", _agent_trace_event(payload))

                    if event_name == "artifact":
                        yield _sse_event("file", {"type": "artifact", "agent": str(agent_role or ""), "detail": payload})

                    if event_name == "completed":
                        yield _sse_event(
                            "answer",
                            {"type": "completed", "final_answer": payload.get("final_answer"), "detail": payload},
                        )

                    if event_name == "response":
                        yield _sse_event("answer", {"type": "result", "answer": payload.get("answer"), "detail": payload})

                    # Node-compatible events: status / result / error
                    if event_name == "status":
                        stage = payload.get("status") or payload.get("stage")
                        if stage:
                            yield _sse_event("status", {"status": _humanize_stage(stage)})
                        continue

                    if event_name == "memory_loaded":
                        yield _sse_event("status", {"status": "Augmenting question"})
                        continue

                    if event_name == "memory_saved":
                        yield _sse_event("status", {"status": "Updating memory..."})
                        continue

                    if event_name == "warning":
                        yield _sse_event("status", {"status": str(payload.get("message") or "Warning")})
                        continue

                    if event_name in {"subagent_started", "subagent_completed"}:
                        role = payload.get("role") or "agent"
                        msg = f"{'Running' if event_name == 'subagent_started' else 'Completed'} {role}"
                        yield _sse_event("status", {"status": msg})
                        continue

                    if event_name == "verification_result":
                        yield _sse_event("status", {"status": "Verification"})
                        continue

                    if event_name == "response":
                        yield _sse_event("result", _format_agent_chat_result(payload))
                        continue

                    if event_name == "error":
                        message = payload.get("error") or payload.get("message") or "Unknown error"
                        diagnostic_text = payload.get("diagnosticText")
                        error_text = str(message)
                        if diagnostic_text:
                            error_text = f"{error_text}\n\n{diagnostic_text}"
                        error_payload = {"error": error_text}
                        if diagnostic_text:
                            error_payload["diagnosticText"] = diagnostic_text
                        if payload.get("diagnostics"):
                            error_payload["diagnostics"] = payload.get("diagnostics")
                            for trace_event in _diagnostic_agent_trace_events(payload.get("diagnostics")):
                                yield _sse_event("agent_trace", trace_event)
                        error_emitted = True
                        yield _sse_event("error", error_payload)
                        continue

            except ValueError as e:
                logger.error(f"Agent chat stream validation error: {str(e)}")
                yield _sse_event("error", {"error": str(e)})
            except Exception as e:
                logger.error(f"Error streaming agent chat: {str(e)}", exc_info=True)
                if error_emitted:
                    return
                error_payload = {"error": str(e)}
                diagnostics = getattr(e, "diagnostics", None)
                if diagnostics:
                    error_payload["diagnostics"] = diagnostics
                    diagnostic_text = diagnostics.get("readable_trace") if isinstance(diagnostics, dict) else None
                    if diagnostic_text:
                        error_payload["diagnosticText"] = diagnostic_text
                        error_payload["error"] = f"{error_payload['error']}\n\n{diagnostic_text}"
                    for trace_event in _diagnostic_agent_trace_events(diagnostics):
                        yield _sse_event("agent_trace", trace_event)
                yield _sse_event("error", error_payload)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    except ValueError as e:
        logger.error(f"Agent chat stream validation error: {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error processing agent chat stream: {str(e)}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/query/batch', methods=['POST'])
def batch_query():
    """
    Batch query endpoint for processing multiple queries in one call.

    ---
    tags:
      - RAG
      - Query
    consumes:
      - application/json
    produces:
      - application/json
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - queries
          properties:
            queries:
              type: array
              items:
                type: object
                properties:
                  user_input:
                    type: string
                    example: What datasets are available for Chicago crime?
                  memory_id:
                    type: string
                    nullable: true
                  params:
                    type: object
                    nullable: true
                  session_context:
                    type: object
                    nullable: true
    responses:
      200:
        description: Batch query results.
        schema:
          type: object
          properties:
            results:
              type: array
              items:
                type: object
                properties:
                  success:
                    type: boolean
                  answer:
                    type: object
                  evidence:
                    type: object
                  error:
                    type: string
      400:
        description: Validation error.
      500:
        description: Internal server error.
    """
    try:
        data = request.get_json() or {}
        queries = data.get('queries', [])

        if not queries or not isinstance(queries, list):
            return jsonify({"error": "queries must be a non-empty list"}), 400

        results = []
        for query_data in queries:
            try:
                result = run_pipeline(
                    user_input=query_data.get('user_input'),
                    memory_id=query_data.get('memory_id'),
                    params=_pipeline_params(query_data),
                    session_context=query_data.get('session_context', {})
                )
                results.append({
                    "success": True,
                    "answer": result.get("answer", {}),
                    "evidence": result.get("evidence", {})
                })
            except Exception as e:
                results.append({
                    "success": False,
                    "error": str(e)
                })

        return jsonify({"results": results}), 200

    except Exception as e:
        logger.error(f"Error processing batch query: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5002))
    debug = os.getenv('DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
