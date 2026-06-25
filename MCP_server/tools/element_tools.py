"""MCP tool: resolve + fetch a community knowledge-element's SOURCE FILE by element_id.

Makes the find->run linkage real: an agent that discovered an element (notebook/dataset/
code) can fetch its actual source via the public I-GUIDE element API and get a usable
``file_id`` for downstream tools/code. Publications expose no source file (their text is
already in the search index as ``pdf_chunks``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

# Repo root on path so we can reuse the shared resolver + file store.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import mcp_tool


@mcp_tool(category="data_loading")
def fetch_element_source(element_id: str) -> Dict[str, Any]:
    """Fetch the SOURCE FILE of an I-GUIDE knowledge element by its element_id.

    Resolves the element via the public platform element API and downloads its source
    (notebook/code -> the .ipynb / repo file; dataset -> the data file), registers it in
    the agent file store, and returns a ``file_id`` usable by other tools or generated
    code. Publications return no file (use their indexed text instead).

    Args:
        element_id: The platform element UUID (the cited knowledge element).

    Returns:
        dict with: element_id, resource_type, title, source_url, found, file_id,
        download_url, filename, note.
    """
    from agent_runtime.element_resolver import download_element_source
    from agent_runtime.file_store import create_output_file_from_path

    info = download_element_source(element_id)
    out: Dict[str, Any] = {k: info.get(k) for k in
                           ("element_id", "resource_type", "title", "source_url", "note")}
    path = info.get("path")
    out["found"] = bool(path)
    if path:
        rec = create_output_file_from_path(path, filename=Path(path).name)
        out["file_id"] = rec["file_id"]
        out["download_url"] = rec["download_url"]
        out["filename"] = rec["filename"]
    return out
