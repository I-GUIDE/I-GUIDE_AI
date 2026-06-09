"""The `execute_code` tool — run/debug code in the sandbox (see code_execution.py)."""

from __future__ import annotations

import json
from typing import Any, List, Optional


def make_code_execution_tools(executor: Optional[Any] = None) -> List[Any]:
    """Build the `execute_code` StructuredTool (container-per-run sandbox)."""
    from langchain_core.tools import StructuredTool

    from agent_runtime.code_execution import DEFAULT_TIMEOUT, get_code_executor

    def execute_code(code: str, language: str = "python", timeout_seconds: int = DEFAULT_TIMEOUT) -> str:
        ex = executor or get_code_executor()
        result = ex.execute(code, language=language, timeout=timeout_seconds)
        return json.dumps(result.to_dict(), ensure_ascii=True, default=str)

    tool = StructuredTool.from_function(
        func=execute_code,
        name="execute_code",
        description=(
            "Execute code in an isolated, sandboxed container and return JSON with "
            "exit_code, stdout, stderr, timed_out, and any output file artifacts. "
            "Use this to RUN and DEBUG code: run it, read stdout/stderr, fix errors, and "
            "re-run until it works. No network access; only files written to the working "
            "directory are returned as artifacts."
        ),
    )
    return [tool]


__all__ = ["make_code_execution_tools"]
