# api — Flask REST API layer
#
# Thin routing layer over rag_pipeline and agent_runtime.  Contains
# no business logic; every route is a short handler that parses the
# request, dispatches to the relevant service, and formats the response.
