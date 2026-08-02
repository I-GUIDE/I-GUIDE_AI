import json

import pytest

from rag_pipeline.search import agents
from rag_pipeline.search.neo4j_graph_tools import (
    build_element_by_id_query,
    build_explore_related_nodes_query,
    detect_pattern,
)


def test_element_by_id_query_is_public_only_and_rejects_empty_id():
    cypher, params = build_element_by_id_query("nb1")

    assert "MATCH (n {id: $element_id})" in cypher
    # tolerant filter: platform stores visibility as 'public' (string) or legacy 10 (numeric)
    assert "toString(n.visibility) IN $public_visibilities" in cypher
    assert "OPTIONAL MATCH (c)-[:CONTRIBUTED]-(n)" in cypher
    assert params == {"element_id": "nb1", "public_visibilities": ["public", "10"]}

    with pytest.raises(ValueError):
        build_element_by_id_query("")


def test_related_nodes_query_clamps_depth_and_limit():
    cypher, params = build_explore_related_nodes_query("nb1", depth=99, limit=999)

    assert "MATCH (seed {id: $element_id})" in cypher
    assert "[:RELATED*1..3]" in cypher
    assert "toString(path_node.visibility) IN $public_visibilities" in cypher
    assert params == {"element_id": "nb1", "public_visibilities": ["public", "10"], "limit": 100}

    with pytest.raises(ValueError):
        build_explore_related_nodes_query("   ")


def test_id_pattern_detection_routes_to_deterministic_tools():
    assert detect_pattern("element id nb1") == ("element_by_id", {"element_id": "nb1"})
    assert detect_pattern("knowledge element d95f1b41-e068") == (
        "element_by_id",
        {"element_id": "d95f1b41-e068"},
    )
    assert detect_pattern("related nodes for id nb1") == (
        "explore_related_by_id",
        {"element_id": "nb1"},
    )
    assert detect_pattern("knowledge element based on id") is None


def test_get_element_by_id_formats_canonical_id(monkeypatch):
    def fake_run(cypher, params):
        return [
            {
                "node": {
                    "id": "nb1",
                    "title": "Notebook One",
                    "contents": "Notebook contents",
                    "visibility": "public",
                    "resource-type": "notebook",
                    "tags": ["demo"],
                },
                "score": 1.0,
            }
        ]

    monkeypatch.setattr(agents, "_neo4j_run", fake_run)

    hits = agents.get_neo4j_element_by_id_results("nb1")

    assert len(hits) == 1
    assert hits[0]["_id"] == "nb1"
    assert hits[0]["_source"]["doc_id"] == "nb1"
    assert hits[0]["_source"]["title"] == "Notebook One"
    assert hits[0]["_source"]["resource-type"] == "notebook"


def test_explore_related_nodes_returns_hybrid_payload(monkeypatch):
    def fake_run(cypher, params):
        return [
            {
                "seed": {
                    "id": "nb1",
                    "title": "Notebook One",
                    "contents": "Seed contents",
                    "visibility": "public",
                    "resource-type": "notebook",
                },
                "nodes": [
                    {
                        "id": "ds1",
                        "title": "Dataset One",
                        "contents": "Related contents",
                        "visibility": "public",
                        "resource-type": "dataset",
                    }
                ],
                "edges": [
                    {"src": "nb1", "dst": "ds1", "type": "RELATED"},
                    {"src": "nb1", "dst": "ds1", "type": "RELATED"},
                ],
            }
        ]

    monkeypatch.setattr(agents, "_neo4j_run", fake_run)

    payload = agents.explore_neo4j_related_nodes("nb1", depth=1, limit=5)

    assert payload["seed"]["doc_id"] == "nb1"
    assert payload["documents"][0]["doc_id"] == "ds1"
    assert payload["edges"] == [{"src": "nb1", "dst": "ds1", "type": "RELATED"}]
    assert payload["citation_ids"] == ["nb1", "ds1"]


def test_explore_related_nodes_missing_seed_returns_empty(monkeypatch):
    monkeypatch.setattr(agents, "_neo4j_run", lambda cypher, params: [])

    payload = agents.explore_neo4j_related_nodes("missing")

    assert payload == {
        "source": "neo4j",
        "count": 0,
        "seed": None,
        "documents": [],
        "edges": [],
        "citation_ids": [],
    }


def test_agent_id_route_does_not_fall_through_to_text2cypher(monkeypatch):
    expected_hit = {"_id": "nb1", "_score": 1.0, "_source": {"doc_id": "nb1"}}

    monkeypatch.setattr(agents, "get_neo4j_element_by_id_results", lambda element_id: [expected_hit])
    monkeypatch.setattr(
        agents,
        "_run_text2cypher",
        lambda *args, **kwargs: pytest.fail("ID lookup should not use Text2Cypher"),
    )

    assert agents.get_neo4j_agent_results("element id nb1") == [expected_hit]


def test_neo4j_search_enables_id_companion_tools():
    pytest.importorskip("langchain_core.tools")

    from rag_pipeline.langchain_granular_tools import (
        make_langchain_granular_tools,
        neo4j_explore_related_nodes_tool,
    )

    tools = make_langchain_granular_tools(
        enabled_search_methods=["neo4j_search"],
        include_file_tools=False,
    )
    names = {tool.name for tool in tools}

    assert {"neo4j_search", "neo4j_get_element_by_id", "neo4j_explore_related_nodes"} <= names

    payload = json.loads(neo4j_explore_related_nodes_tool(" ", depth=10, limit=500))
    assert payload["documents"] == []
