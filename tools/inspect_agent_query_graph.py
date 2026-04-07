#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rag_pipeline.langchain_agent_executor import AGENT_QUERY_GRAPH


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or render the AGENT_QUERY_GRAPH structure.")
    parser.add_argument(
        "--format",
        choices=["ascii", "mermaid", "png"],
        default="ascii",
        help="Output format. ascii prints to stdout, mermaid prints or writes Mermaid, png writes a PNG file.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Required for png. Optional for mermaid.",
    )
    args = parser.parse_args()

    graph = AGENT_QUERY_GRAPH.get_graph()

    if args.format == "ascii":
        try:
            print(graph.draw_ascii())
        except ImportError as exc:
            print(f"ASCII rendering unavailable: {exc}")
            print("Try: python tools/inspect_agent_query_graph.py --format mermaid")
        return

    if args.format == "mermaid":
        mermaid = graph.draw_mermaid()
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(mermaid, encoding="utf-8")
            print(output_path)
        else:
            print(mermaid)
        return

    if args.format == "png":
        if not args.output:
            raise SystemExit("--output is required when --format png")
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        png_bytes = graph.draw_png()
        output_path.write_bytes(png_bytes)
        print(output_path)
        return


if __name__ == "__main__":
    main()
