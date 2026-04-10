import os
import logging
import json
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_file, stream_with_context
from flask_cors import CORS
from flasgger import Swagger

from agent_runtime.agent_chat_service import run_agent_chat, stream_agent_chat_events
from agent_runtime.file_store import require_file_record, resolve_file_id, save_uploaded_file
from .pipeline import run_pipeline

app = Flask(__name__)

# Load environment variables
load_dotenv()

CORS(app)

swagger = Swagger(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _format_elements(retrieved_documents):
    """
    Convert internal evidence entries into the simplified element payload expected by the UI.
    """
    elements = []
    opengeodata_count = 0
    for entry in retrieved_documents:
        document = (entry or {}).get("document") or {}
        metadata = (entry or {}).get("metadata") or {}
        source = entry.get("source", "unknown")
        resource_type = document.get("resource-type") or document.get("element_type")
        
        # Track opengeodata results
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
                "source": source,  # Include source field to identify opengeodata results
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


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "rag-pipeline"}), 200


@app.route('/agent/files/upload', methods=['POST'])
def upload_agent_files():
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
    try:
        record = require_file_record(file_id)
        path = resolve_file_id(file_id)
        mimetype = None
        suffix = Path(record.get("filename", "")).suffix.lower()
        if suffix == ".csv":
            mimetype = "text/csv"
        elif suffix == ".json":
            mimetype = "application/json"
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
        
        # Run the full pipeline with LLM routing and reranker
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

Request body:
{
    "user_input": "What datasets are available for Chicago crime?",
    "thread_id": "chat-thread-1",
    "memory_id": "conversation-1",
    "conversation_name": "Chicago agent chat",
    "recent_k": 8,
    "tool_strategy": "granular",
    "include_mcp_tools": false,
    "mcp_modules": ["search_tools", "data_tools"],
    "enabled_search_methods": ["keyword_search", "semantic_search"],
    "use_persistent_memory": true,
    "smart_tool_routing": true,
    "forced_intent": null,
    "file_paths": ["./data/crime.csv"],
    "verbose": false
}
"""
    try:
        data = request.get_json() or {}
        user_input = data.get('user_input')
        if not user_input:
            return jsonify({"error": "user_input is required"}), 400

        logger.info(f"Processing agent chat: {user_input[:100]}...")
        response = run_agent_chat(
            user_input=user_input,
            thread_id=data.get('thread_id'),
            memory_id=data.get('memory_id'),
            conversation_name=data.get('conversation_name'),
            recent_k=data.get('recent_k'),
            tool_strategy=data.get('tool_strategy', 'granular'),
            include_mcp_tools=bool(data.get('include_mcp_tools', False)),
            mcp_modules=_parse_mcp_modules(data.get('mcp_modules')),
            enabled_search_methods=_parse_enabled_search_methods(data.get('enabled_search_methods')),
            use_persistent_memory=bool(data.get('use_persistent_memory', True)),
            smart_tool_routing=bool(data.get('smart_tool_routing', True)),
            forced_intent=data.get('forced_intent'),
            file_paths=data.get('file_paths'),
            file_ids=data.get('file_ids'),
            verbose=bool(data.get('verbose', False)),
        )
        return jsonify(response), 200
    except ValueError as e:
        logger.error(f"Agent chat validation error: {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error processing agent chat: {str(e)}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/agent/chat/stream', methods=['POST'])
def agent_chat_stream():
    try:
        data = request.get_json() or {}
        user_input = data.get('user_input')
        if not user_input:
            return jsonify({"error": "user_input is required"}), 400

        logger.info(f"Streaming agent chat: {user_input[:100]}...")

        @stream_with_context
        def generate():
            try:
                for item in stream_agent_chat_events(
                    user_input=user_input,
                    thread_id=data.get('thread_id'),
                    memory_id=data.get('memory_id'),
                    conversation_name=data.get('conversation_name'),
                    recent_k=data.get('recent_k'),
                    tool_strategy=data.get('tool_strategy', 'granular'),
                    include_mcp_tools=bool(data.get('include_mcp_tools', False)),
                    mcp_modules=_parse_mcp_modules(data.get('mcp_modules')),
                    enabled_search_methods=_parse_enabled_search_methods(data.get('enabled_search_methods')),
                    use_persistent_memory=bool(data.get('use_persistent_memory', True)),
                    smart_tool_routing=bool(data.get('smart_tool_routing', True)),
                    forced_intent=data.get('forced_intent'),
                    file_paths=data.get('file_paths'),
                    file_ids=data.get('file_ids'),
                    verbose=bool(data.get('verbose', False)),
                ):
                    event_name = str(item.get("event") or "message")
                    yield _sse_event(event_name, item.get("data") or {})
            except ValueError as e:
                logger.error(f"Agent chat stream validation error: {str(e)}")
                yield _sse_event("error", {"message": str(e), "type": "validation_error"})
            except Exception as e:
                logger.error(f"Error streaming agent chat: {str(e)}", exc_info=True)
                yield _sse_event("error", {"message": str(e), "type": "internal_error"})

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
    Batch query endpoint for processing multiple queries.
    
    Request body:
    {
        "queries": [
            {"user_input": "query 1", "memory_id": "session-1"},
            {"user_input": "query 2", "memory_id": "session-2"}
        ]
    }
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
