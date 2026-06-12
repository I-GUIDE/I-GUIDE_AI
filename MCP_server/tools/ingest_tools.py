"""MCP tool: ingest a GitHub repo (code + notebook extractors).

Agent-callable entry point for the GitHub ingest path. The batch equivalent is
``python -m extractors.cli <url>``. Dataset/publication ingestion is delivered via
the webhook, not here.
"""

import sys
from pathlib import Path
from typing import Any, Dict

from server import mcp_tool

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@mcp_tool(
    category="generation",
    description=(
        "Ingest a GitHub repository: extract notebook code blocks (searchable), "
        "runnable functions/workflows (MCP manifests), and the overall pipeline (a SKILL). "
        "targets is a comma-separated subset of opensearch,mcp,skill. Returns a manifest summary."
    ),
)
def ingest_github_repo(
    url: str,
    targets: str = "opensearch,mcp,skill",
    ref: str = "",
    reingest: bool = False,
) -> Dict[str, Any]:
    from extractors.ingest import ingest_from_github

    target_list = [t.strip() for t in (targets or "").split(",") if t.strip()]
    manifest = ingest_from_github(url, ref=ref, targets=target_list, reingest=reingest)
    return manifest.to_dict()
