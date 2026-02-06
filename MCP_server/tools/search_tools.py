from typing import List
from server import mcp_tool  # decorator

@mcp_tool
def search_publications(query: str, limit: int = 5) -> List[dict]:
    """
    Search publications by keyword.
    """
    return [{"title": f"Paper about {query}", "year": 2024} for _ in range(limit)]
