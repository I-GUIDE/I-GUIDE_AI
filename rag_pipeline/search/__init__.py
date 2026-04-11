"""Search and retrieval subpackage.

Re-exports the main entry point so callers can do::

    from rag_pipeline.search import run_retrieval
"""

from .core import run_retrieval  # noqa: F401
