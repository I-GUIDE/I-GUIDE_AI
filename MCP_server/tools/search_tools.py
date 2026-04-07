from typing import List
from server import mcp_tool  # decorator
from duckduckgo_search import DDGS

# @mcp_tool
# def search_publications(query: str, limit: int = 5) -> List[dict]:
#     """Search publications by keyword.
    
#     Args:
#         query: The search keyword or phrase
#         limit: Maximum number of results to return (default: 5)
    
#     Returns:
#         List of publication dictionaries with title and year
#     """
#     return [{"title": f"Paper about {query}", "year": 2024} for _ in range(limit)]

@mcp_tool
def search_external_resources(topic: str, resource_type: str) -> list:
    """
    Searches for geospatial resources like datasets, notebooks, or publications on a given topic.
    
    Args:
        topic: The geospatial topic to search for.
        resource_type: The type of resource to find ('datasets', 'notebooks', or 'publications').
        
    Returns:
        A list of search results with titles, links, and snippets.
    """
    query_map = {
        'datasets': f'geospatial open data {topic}',
        'notebooks': f'jupyter notebook {topic} github',
        'publications': f'research paper {topic} pdf'
    }
    query = query_map.get(resource_type, f'geospatial {topic}')
    
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))
    return results
