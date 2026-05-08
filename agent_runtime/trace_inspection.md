# Agent Execution Trace Inspection

This document explains how to inspect and replay LangChain agent execution traces.

## Live tracing with the CLI

Run a live query and save the trace:

```bash
python tools/debug_agent_execution.py --query "What is the crime rate in Chicago?" --save ./trace.json --verbose
```

The CLI will:
- stream agent execution events as they happen
- print a summary after the query completes
- save the full trace to `trace.json`

## Replay a saved trace

```bash
python tools/debug_agent_execution.py --replay ./trace.json --verbose
```

This prints the trace summary and optionally replays every event.

## List traces inside a saved file

```bash
python tools/debug_agent_execution.py --list ./trace.json
```

## Useful APIs

- `agent_runtime.graph_runtime.stream_agent_query_events(...)` — stream structured SSE events while the agent runs
- `agent_runtime.trace_analyzer.parse_trace_events(...)` — normalize a sequence of trace events
- `agent_runtime.trace_analyzer.summarize_trace(...)` — build a concise execution summary
- `agent_runtime.trace_store.TraceStore` — keep recent traces in memory and export them to JSON

## Debugging scenarios

1. **Agent gets stuck or raises an error**
   - use `--verbose` to show live tool calls and errors
   - inspect the saved trace file for `error` and `tool_error` events

2. **Output is incorrect**
   - review `tool_call` and `tool_result` events in the trace
   - compare the `route_trace` route with expected execution flow

3. **Need a clean summary**
   - use `agent_runtime.trace_analyzer.summarize_trace(...)` to see route, tool counts, and final answer presence
