from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

from ..utils import get_logger, getenv

log = get_logger("neo4j._client")


@lru_cache(maxsize=1)
def _components() -> Optional[Dict[str, Any]]:
    try:
        from neo4j import GraphDatabase
        from neo4j.graph import Node as Neo4jNode
    except Exception as exc:
        log.debug("Neo4j package unavailable: %s", exc)
        return None
    return {"GraphDatabase": GraphDatabase, "Node": Neo4jNode}


@lru_cache(maxsize=1)
def get_driver() -> Optional[Any]:
    c = _components()
    if not c:
        return None
    uri = (
        getenv("NEO4J_CONNECTION_STRING", required=False)
        or getenv("NEO4J_URI", required=False)
        or getenv("NEO4J_CONNECTION_STRING")
    )
    user = (
        getenv("NEO4J_USER", required=False)
        or getenv("NEO4J_USERNAME", required=False)
        or getenv("NEO4J_USER")
    )
    password = getenv("NEO4J_PASSWORD")
    try:
        return c["GraphDatabase"].driver(uri, auth=(user, password), max_connection_lifetime=300)
    except Exception as exc:
        log.error("Failed to create Neo4j driver: %s", exc)
        return None


def get_db() -> Optional[str]:
    value = getenv("NEO4J_DB", required=False, default="").strip()
    return value or None


def run_query(cypher: str, params: Dict[str, Any], driver: Optional[Any] = None) -> List[Dict[str, Any]]:
    db_driver = driver or get_driver()
    if db_driver is None:
        return []
    database = get_db()
    session_factory = db_driver.session
    with (session_factory(database=database) if database else session_factory()) as session:
        return list(session.run(cypher, **params))


def get_node_class() -> Optional[type]:
    c = _components()
    return c["Node"] if c else None
