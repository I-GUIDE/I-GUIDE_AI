from importlib import import_module

__all__ = [
    "create_output_file",
    "get_file_record",
    "require_file_record",
    "resolve_file_id",
    "save_uploaded_file",
    "storage_root",
    "rag_tool",
    "rag_tool_json",
    "make_langchain_rag_tool",
    "make_langchain_granular_tools",
    "make_langchain_mcp_tools",
    "run_agent_chat",
    "stream_agent_chat_events",
    "build_agent_executor",
    "build_code_agent_executor",
    "run_agent_query",
    "run_code_agent_query",
]

_EXPORT_MAP = {
    "create_output_file": (".file_store", "create_output_file"),
    "get_file_record": (".file_store", "get_file_record"),
    "require_file_record": (".file_store", "require_file_record"),
    "resolve_file_id": (".file_store", "resolve_file_id"),
    "save_uploaded_file": (".file_store", "save_uploaded_file"),
    "storage_root": (".file_store", "storage_root"),
    "rag_tool": (".langchain_tool", "rag_tool"),
    "rag_tool_json": (".langchain_tool", "rag_tool_json"),
    "make_langchain_rag_tool": (".langchain_tool", "make_langchain_rag_tool"),
    "make_langchain_granular_tools": (".langchain_granular_tools", "make_langchain_granular_tools"),
    "make_langchain_mcp_tools": (".langchain_mcp_tools", "make_langchain_mcp_tools"),
    "run_agent_chat": (".agent_chat_service", "run_agent_chat"),
    "stream_agent_chat_events": (".agent_chat_service", "stream_agent_chat_events"),
    "build_agent_executor": (".langchain_agent_executor", "build_agent_executor"),
    "build_code_agent_executor": (".langchain_agent_executor", "build_code_agent_executor"),
    "run_agent_query": (".langchain_agent_executor", "run_agent_query"),
    "run_code_agent_query": (".langchain_agent_executor", "run_code_agent_query"),
}


def __getattr__(name: str):
    target = _EXPORT_MAP.get(name)
    if target is None:
        raise AttributeError(f"module 'agent_runtime' has no attribute '{name}'")
    module_name, attr_name = target
    module = import_module(module_name, package=__name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
