"""CLI helpers to inspect and replay agent execution traces."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_runtime.graph_runtime import stream_agent_query_events
from agent_runtime.trace_analyzer import (
    format_event,
    format_error_details,
    format_summary,
    get_errors,
    parse_trace_events,
    summarize_trace,
)
from agent_runtime.trace_store import TraceStore


def run_live_query(
    query: str,
    save_path: Optional[str] = None,
    verbose: bool = False,
    **kwargs: Any,
) -> None:
    trace_id = f"trace-{uuid.uuid4()}"
    events: List[Dict[str, Any]] = []
    print(f"Running live query and collecting trace id={trace_id}\n")
    for event in stream_agent_query_events(query, **kwargs):
        events.append(event)
        if verbose:
            print(format_event(event))
    store = TraceStore(max_size=1)
    store.add_trace(trace_id=trace_id, query=query, events=events)
    summary = summarize_trace(events)
    print("\n" + format_summary(summary))
    errors = get_errors(events)
    if errors:
        print("\n" + format_error_details(events))
    if save_path:
        store.save_to_file(save_path)
        print(f"\nSaved trace to: {save_path}")


def replay_trace_file(path: str, verbose: bool = False) -> None:
    raw = Path(path).expanduser().read_text(encoding="utf-8")
    payload = json.loads(raw)
    if payload.get("traces"):
        traces = payload.get("traces")
        if not traces:
            print("No traces found in file.")
            return
        trace = traces[-1]
    else:
        trace = payload
    events = parse_trace_events(trace.get("events") or [])
    print(f"Replaying trace id={trace.get('trace_id') or '<unknown>'}\n")
    if verbose:
        for event in events:
            print(format_event(event))
    summary = summarize_trace(events)
    print("\n" + format_summary(summary))
    errors = get_errors(events)
    if errors:
        print("\n" + format_error_details(events))


def list_trace_file(path: str) -> None:
    raw = Path(path).expanduser().read_text(encoding="utf-8")
    payload = json.loads(raw)
    traces = payload.get("traces") or []
    if not traces:
        print("No traces found in file.")
        return
    print(f"Found {len(traces)} trace(s) in {path}:\n")
    for item in traces:
        print(f"- trace_id={item.get('trace_id')} query={item.get('query')!r} created_at={item.get('created_at')}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and replay agent execution traces.")
    parser.add_argument("--query", help="Run a live query and stream trace events.")
    parser.add_argument("--save", help="Save the collected trace to a JSON file.")
    parser.add_argument("--replay", help="Replay a saved trace JSON file.")
    parser.add_argument("--list", help="List traces in a saved trace JSON file.")
    parser.add_argument("--verbose", action="store_true", help="Show each trace event as it arrives.")
    parser.add_argument("--tool_strategy", default="granular", help="Tool strategy to use for live query execution.")
    parser.add_argument("--return_intermediate_steps", action="store_true", default=True, help="Return intermediate steps when running the live query.")
    parser.add_argument("--no-return_intermediate_steps", dest="return_intermediate_steps", action="store_false", help="Do not request intermediate steps for the live query.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.query:
        run_live_query(
            args.query,
            save_path=args.save,
            verbose=args.verbose,
            tool_strategy=args.tool_strategy,
            return_intermediate_steps=args.return_intermediate_steps,
        )
        return 0

    if args.replay:
        replay_trace_file(args.replay, verbose=args.verbose)
        return 0

    if args.list:
        list_trace_file(args.list)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
