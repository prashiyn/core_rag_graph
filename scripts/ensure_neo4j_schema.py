import os

from neo4j import GraphDatabase


def main() -> None:
    uri = os.getenv("NEO4J_URI", "").strip()
    user = os.getenv("NEO4J_USER", "").strip()
    password = os.getenv("NEO4J_PASSWORD", "").strip()
    database = os.getenv("NEO4J_DATABASE", "neo4j").strip()

    if not uri or not user or not password:
        raise RuntimeError("NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD are required")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            session.run(
                """
                CREATE CONSTRAINT graph_node_identity IF NOT EXISTS
                FOR (n:GraphNode)
                REQUIRE (n.collection_id, n.name, n.schema_type, n.label) IS UNIQUE
                """
            )
            session.run(
                """
                CREATE INDEX graph_node_collection IF NOT EXISTS
                FOR (n:GraphNode) ON (n.collection_id)
                """
            )
        print("Neo4j schema ensured")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
