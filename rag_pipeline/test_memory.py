from __future__ import annotations

import argparse
import sys

try:
    from agent_runtime.langchain_agent_executor import run_agent_query
except Exception:
    from langchain_agent_executor import run_agent_query


def main() -> int:
    parser = argparse.ArgumentParser(description="One-process memory test for LangGraph thread state.")
    parser.add_argument(
        "--thread-id",
        default="test-memory-thread",
        help="Thread id reused across both turns in the same Python process.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose agent output.",
    )
    args = parser.parse_args()

    print(f"Running one-session memory test with thread_id={args.thread_id!r}")

    first_query = "What is the capital of France?"
    second_query = "What is its population?"

    print("\nTurn 1:")
    print(f"Query: {first_query}")
    result1 = run_agent_query(
        first_query,
        thread_id=args.thread_id,
        verbose=args.verbose,
    )
    answer1 = result1.get("final_answer", "No answer")
    print(f"Thread: {result1.get('thread_id')}")
    print(f"Response: {answer1}")

    print("\nTurn 2:")
    print(f"Query: {second_query}")
    result2 = run_agent_query(
        second_query,
        thread_id=args.thread_id,
        verbose=args.verbose,
    )
    answer2 = result2.get("final_answer", "No answer")
    print(f"Thread: {result2.get('thread_id')}")
    print(f"Response: {answer2}")

    if result1.get("thread_id") != args.thread_id or result2.get("thread_id") != args.thread_id:
        print("\nFAIL: thread_id was not preserved across both turns.")
        return 1

    print("\nThread id was preserved across both turns.")
    print("If the second answer correctly resolves 'its' to France, memory worked within this session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
