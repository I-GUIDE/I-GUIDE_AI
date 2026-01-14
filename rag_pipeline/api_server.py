from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os
import logging
from uuid import uuid4

from .pipeline import run_pipeline

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _format_elements(retrieved_documents):
    """
    Convert internal evidence entries into the simplified element payload expected by the UI.
    """
    elements = []
    for entry in retrieved_documents:
        document = (entry or {}).get("document") or {}
        metadata = (entry or {}).get("metadata") or {}

        elements.append(
            {
                "_id": document.get("doc_id") or metadata.get("hit_id"),
                "_score": entry.get("score"),
                "contributor": document.get("contributor"),
                "contents": document.get("contents"),
                "resource-type": document.get("resource-type") or document.get("element_type"),
                "title": document.get("title"),
                "authors": document.get("authors") or [],
                "tags": document.get("tags") or [],
                "thumbnail-image": document.get("thumbnail-image") or document.get("thumbnail_image"),
            }
        )
    return elements


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "rag-pipeline"}), 200

@app.route('/query', methods=['POST'])
def query():
    """
    Main RAG pipeline endpoint with LLM routing and reranker.
    
    Request body:
    {
        "user_input": "your query",
        "memory_id": "optional-session-id",
        "params": {
            "top_k": 8,
            "max_context_tokens": 6000,
            "enable_llm_reranker": true
        },
        "session_context": {},
        "recent_k": 5
    }
    
    Returns:
    {
        "answer": "final answer text",
        "message_id": "uuid",
        "elements": [...],
        "count": 8,
        "retrievalSteps": [...],
        "reactHistory": [...]
    }
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
            params=data.get('params', {
                "top_k": 8,
                "max_context_tokens": 6000,
                "enable_llm_reranker": True
            }),
            session_context=data.get('session_context', {}),
            recent_k=data.get('recent_k'),
            extra_state=data.get('extra_state', {})
        )
        
        evidence = result.get("evidence", {}) or {}
        retrieved_documents = evidence.get("retrieved_documents", []) or []
        elements = _format_elements(retrieved_documents)
        trace = result.get("trace_observability", {}) or {}
        planner = result.get("planner_reasoning", {}) or {}

        response = {
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
        
        logger.info(f"Query processed successfully. Retrieved {len(elements)} documents.")
        return jsonify(response), 200
        
    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}", exc_info=True)
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
                    params=query_data.get('params', {}),
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
