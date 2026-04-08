import sys
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_notebook


def test_create_notebook_workflow_tool_and_execute(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    mcp_root = repo_root / "MCP_server"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))

    uploads_dir = repo_root / "agent_chat_files" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    notebook_path = uploads_dir / "pytest_notebook_workflow.ipynb"
    notebook = new_notebook(
        cells=[
            new_code_cell(
                "def run_workflow(a, b=2):\n"
                "    return {'sum': a + b, 'difference': a - b}\n"
            )
        ]
    )
    notebook_path.write_text(nbformat.writes(notebook), encoding="utf-8")

    from tools.notebook_workflow_tools import create_notebook_workflow_tool
    import tools.generated_notebook_tools as generated

    result = create_notebook_workflow_tool(str(notebook_path), tool_name="pytest_nb_tool", overwrite=True)

    assert result["tool_name"] == "pytest_nb_tool"
    assert result["mode"] == "function"

    fn = getattr(generated, "pytest_nb_tool")
    execution = fn('{"a": 9, "b": 4}')

    assert execution["entrypoint"] == "run_workflow"
    assert execution["result"] == {"sum": 13, "difference": 5}
