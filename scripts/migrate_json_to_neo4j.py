import argparse
import os
import json
from typing import List, Dict

from graph.config import get_config
from graph.utils.graph_repository import Neo4jGraphRepository


def _discover_collections(base_graph_dir: str) -> List[str]:
    if not os.path.exists(base_graph_dir):
        return []
    collections: List[str] = []
    for name in os.listdir(base_graph_dir):
        if not name.endswith(".json"):
            continue
        if name.endswith("_community_reports.json"):
            continue
        collections.append(name[:-5])
    collections.sort()
    return collections


def _load_relationships(base_graph_dir: str, collection_id: str) -> List[Dict]:
    path = os.path.join(base_graph_dir, f"{collection_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected list of relationships in {path}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate JSON collection graphs to Neo4j.")
    parser.add_argument("--base-graph-dir", default="./data/graph", help="Path containing {collection_id}.json graphs")
    parser.add_argument("--collections", nargs="*", default=None, help="Optional explicit collection IDs")
    parser.add_argument("--drop-target-first", action="store_true", help="Delete target collection graph before import")
    args = parser.parse_args()

    uri = os.getenv("NEO4J_URI", "").strip()
    user = os.getenv("NEO4J_USER", "").strip()
    password = os.getenv("NEO4J_PASSWORD", "").strip()
    database = os.getenv("NEO4J_DATABASE", "neo4j").strip()
    if not uri or not user or not password:
        raise RuntimeError("Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD before running migration")

    repo = Neo4jGraphRepository(uri=uri, user=user, password=password, database=database)
    config = get_config()
    try:
        collections = args.collections if args.collections else _discover_collections(args.base_graph_dir)
        if not collections:
            print("No collections discovered. Nothing to migrate.")
            return

        print(f"Discovered {len(collections)} collections")
        for collection_id in collections:
            relationships = _load_relationships(args.base_graph_dir, collection_id)
            if args.drop_target_first:
                repo.delete_collection(collection_id)
            repo.replace_collection_graph(collection_id, relationships, config)
            graph = repo.load_collection_graph(collection_id)
            node_count = graph.number_of_nodes() if graph is not None else 0
            edge_count = graph.number_of_edges() if graph is not None else 0
            print(f"Migrated collection={collection_id} relationships={len(relationships)} nodes={node_count} edges={edge_count}")
    finally:
        repo.close()


if __name__ == "__main__":
    main()

