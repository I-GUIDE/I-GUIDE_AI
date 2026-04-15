import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from server import mcp_tool


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from notebook_workflow_builder import build_notebook_workflow_artifacts, list_manifest_paths
from tools.generated_notebook_tools import register_generated_tool_from_manifest


@mcp_tool(category="generation")
def create_notebook_workflow_tool(notebook_ref: str, tool_name: str = "", overwrite: bool = False) -> Dict[str, Any]:
    """
    Convert an uploaded Jupyter notebook into a callable MCP tool.

    Args:
        notebook_ref: Uploaded file_id or local path to a .ipynb file.
        tool_name: Optional explicit MCP tool name to generate.
        overwrite: Replace an existing generated tool with the same name.

    Returns:
        dict: Metadata about the generated tool and how to call it.
    """
    manifest = build_notebook_workflow_artifacts(
        notebook_ref,
        tool_name=(tool_name or None),
        overwrite=overwrite,
    )
    register_generated_tool_from_manifest(manifest["manifest_path"], register_with_server=True)
    return {
        "status": "ok",
        "workflow_id": manifest["workflow_id"],
        "tool_name": manifest["tool_name"],
        "mode": manifest["mode"],
        "entrypoint": manifest["entrypoint"],
        "entrypoint_parameters": manifest["entrypoint_parameters"],
        "script_output_candidates": manifest["script_output_candidates"],
        "top_level_functions": manifest["top_level_functions"],
        "manifest_path": manifest["manifest_path"],
        "source_path": manifest["source_path"],
        "notebook_path": manifest["notebook_path"],
        "notebook_file_id": manifest["notebook_file_id"],
        "next_step": (
            f"Call the generated MCP tool as mcp_{manifest['tool_name']} with parameters_json='{{...}}'. "
            "If the current agent run started before this tool was created, start a new agent run to refresh the tool list."
        ),
    }


@mcp_tool(category="retrieval_internal")
def list_generated_notebook_workflow_tools() -> List[Dict[str, Any]]:
    """
    List notebook-derived MCP tools currently materialized on disk.
    """
    tools: List[Dict[str, Any]] = []
    for manifest_path in list_manifest_paths():
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except Exception:
            continue
        tools.append(
            {
                "workflow_id": manifest.get("workflow_id"),
                "tool_name": manifest.get("tool_name"),
                "mode": manifest.get("mode"),
                "entrypoint": manifest.get("entrypoint"),
                "notebook_path": manifest.get("notebook_path"),
                "notebook_file_id": manifest.get("notebook_file_id"),
                "manifest_path": str(manifest_path),
                "source_path": manifest.get("source_path"),
            }
        )
    return tools
