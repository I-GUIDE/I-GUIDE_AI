# services — Agent/chat infrastructure shared across api/ and agent_runtime/
#
# This package owns chat session handling, file storage, and the LangChain
# tool wrappers that bridge the underlying capabilities (search, files, MCP)
# into the agent runtime.  It depends on ``rag_pipeline`` for search and
# memory.
