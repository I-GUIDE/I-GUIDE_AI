from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os
import logging

from .pipeline import run_pipeline

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        "answer": {
            "final_composed_answer": "...",
            "citations": [...],
            "confidence_score": 0.95
        },
        "evidence": {
            "retrieved_documents": [...],
            "sources": {...}
        },
        "query_information": {...},
        "trace_observability": {...}
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
        
        # Extract and format the response
        response = {
            "answer": result.get("answer", {}),
            "evidence": {
                "retrieved_documents": result.get("evidence", {}).get("retrieved_documents", []),
                "sources": result.get("evidence", {}).get("sources", {})
            },
            "query_information": result.get("query_information", {}),
            "trace_observability": result.get("trace_observability", {})
        }
        
        logger.info(f"Query processed successfully. Retrieved {len(response['evidence']['retrieved_documents'])} documents.")
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
