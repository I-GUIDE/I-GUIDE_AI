"""Search and retrieval subpackage.

Re-exports the main entry point so callers can do::

    from rag_pipeline.search import run_retrieval

The import is lazy to avoid a circular dependency:
    state.py → search.utils → search/__init__.py → search.core → ... → state.py
"""


def __getattr__(name: str):
    if name == "run_retrieval":
        from .core import run_retrieval
        return run_retrieval
    raise AttributeError(f"module 'rag_pipeline.search' has no attribute {name!r}")
