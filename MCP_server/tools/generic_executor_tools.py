"""MCP tools: the ONE generic executor for promoted, runnable units.

Replaces per-manifest tool registration (which bloated the agent tool list). Two
stable tools resolve a unit by id from the manifest registry and dispatch through
the existing ``generated_notebook_tools._run_generated_manifest``:

  - run_notebook_workflow(workflow_id, parameters_json)
  - run_code_element(element_id, parameters_json)

SAFETY: these execute ingested repo code via exec(). Execution must run inside the
project ``sandbox/`` and require explicit per-id user opt-in. The guard is wired but
the sandbox dispatch + registry lookup are stubbed in this scaffolding branch.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from server import mcp_tool

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.generated_notebook_tools import _run_generated_manifest  # shared executor body (reused verbatim)


def _lookup_manifest(workflow_id: str) -> Dict[str, Any]:
    """Resolve a workflow manifest by id from the agent workflow store
    (written by ``extractors.emitters.mcp_emitter``)."""
    from extractors.emitters.mcp_emitter import workflow_manifests_dir

    path = workflow_manifests_dir() / f"{workflow_id}.json"
    if not path.exists():
        raise ValueError(f"unknown workflow id: {workflow_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_execution_optin(workflow_id: str) -> None:
    """Gate: ingested code only runs with explicit opt-in (and, in production, inside
    the project sandbox). Default OFF — promotion to a manifest is not auto-execution."""
    if os.getenv("AGENT_ALLOW_WORKFLOW_EXEC", "0").strip().lower() not in ("1", "true", "yes"):
        raise PermissionError(
            f"execution of workflow '{workflow_id}' is disabled. Set AGENT_ALLOW_WORKFLOW_EXEC=1 "
            "(and run in the project sandbox) to allow running ingested code."
        )


@mcp_tool(
    category="computation",
    description=(
        "Run a promoted notebook workflow by workflow_id. parameters_json is a JSON object "
        "string. Executes ingested code in a sandbox; requires explicit opt-in."
    ),
)
def run_notebook_workflow(workflow_id: str, parameters_json: str = "{}") -> Dict[str, Any]:
    _require_execution_optin(workflow_id)
    manifest = _lookup_manifest(workflow_id)
    return _run_generated_manifest(manifest, parameters_json=parameters_json)


@mcp_tool(
    category="computation",
    description=(
        "Run a promoted code element (entry point) by element_id. parameters_json is a JSON "
        "object string. Executes ingested code in a sandbox; requires explicit opt-in."
    ),
)
def run_code_element(element_id: str, parameters_json: str = "{}") -> Dict[str, Any]:
    _require_execution_optin(element_id)
    manifest = _lookup_manifest(element_id)
    return _run_generated_manifest(manifest, parameters_json=parameters_json)
