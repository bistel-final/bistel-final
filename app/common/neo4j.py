from functools import lru_cache

from neo4j import Driver, GraphDatabase

from app.common.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER


@lru_cache(maxsize=1)
def get_neo4j_driver() -> Driver:
    return GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
    )


def close_neo4j_driver() -> None:
    if get_neo4j_driver.cache_info().currsize:
        get_neo4j_driver().close()
        get_neo4j_driver.cache_clear()
