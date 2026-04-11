#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.graph_nodes import collect_orchestration_tools as _collect_orchestration_tools


def _build_spec() -> dict:
    tools = _collect_orchestration_tools(
        chat_history=[{"role": "user", "content": "example memory"}],
        llm=None,
        verbose=False,
        return_intermediate_steps=True,
        tool_strategy="granular",
        include_mcp_tools=False,
        mcp_modules=None,
        enabled_search_methods=None,
        smart_tool_routing=True,
        forced_intent=None,
        thread_id="inspect-thread",
        checkpointer=None,
    )
    tool_names = [getattr(tool, "name", "") for tool in tools if getattr(tool, "name", "")]
    return {
        "mode": "orchestration",
        "entrypoint": "run_agent_query / stream_agent_query_events",
        "flow": [
            "load chat_history before orchestration",
            "orchestrator_agent decides minimal sufficient path",
            "answer_from_memory when chat history is enough",
            "search_agent_evidence when retrieval is needed",
            "analysis_agent_answer when synthesis is needed",
            "search_agent_evidence -> analysis_agent_answer chaining when both are needed",
        ],
        "available_tools_with_chat_history": tool_names,
    }


def _ascii(spec: dict) -> str:
    lines = [
        "orchestrator_agent",
        "  -> answer_from_memory (if chat_history available and sufficient)",
        "  -> search_agent_evidence (if retrieval needed)",
        "  -> analysis_agent_answer (if synthesis needed)",
        "  -> search_agent_evidence -> analysis_agent_answer (if both needed)",
        "",
        f"available_tools_with_chat_history: {', '.join(spec.get('available_tools_with_chat_history', []))}",
    ]
    return "\n".join(lines)


def _mermaid(_: dict) -> str:
    return "\n".join(
        [
            "flowchart TD",
            "    A[chat_history loaded] --> B[orchestrator_agent]",
            "    B --> C[answer_from_memory]",
            "    B --> D[search_agent_evidence]",
            "    B --> E[analysis_agent_answer]",
            "    D --> E",
            "    C --> F[final answer]",
            "    D --> F",
            "    E --> F",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the active orchestration-based agent flow.")
    parser.add_argument(
        "--format",
        choices=["ascii", "json", "mermaid"],
        default="ascii",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path for json or mermaid.",
    )
    args = parser.parse_args()

    spec = _build_spec()
    if args.format == "ascii":
        print(_ascii(spec))
        return

    if args.format == "json":
        content = json.dumps(spec, indent=2)
    else:
        content = _mermaid(spec)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        print(output_path)
        return

    print(content)


if __name__ == "__main__":
    main()
