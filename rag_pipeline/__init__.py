from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from importlib import import_module

__all__ = [
    "rag_tool",
    "rag_tool_json",
    "make_langchain_rag_tool",
]

_EXPORT_MAP = {
    "rag_tool": (".rag_tool", "rag_tool"),
    "rag_tool_json": (".rag_tool", "rag_tool_json"),
    "make_langchain_rag_tool": (".rag_tool", "make_langchain_rag_tool"),
}


def __getattr__(name: str):
    target = _EXPORT_MAP.get(name)
    if target is None:
        raise AttributeError(f"module 'rag_pipeline' has no attribute '{name}'")
    module_name, attr_name = target
    module = import_module(module_name, package=__name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
