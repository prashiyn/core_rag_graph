import argparse
import os
from typing import Tuple

from graph.utils.graph_repository import NetworkXJsonGraphRepository, Neo4jGraphRepository


def _counts(graph) -> Tuple[int, int]:
    if graph is None:
        return (0, 0)
    return (graph.number_of_nodes(), graph.number_of_edges())


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare NetworkX JSON graph parity against Neo4j for collections.")
    parser.add_argument("--collections", nargs="+", required=True, help="Collection IDs to compare")
    parser.add_argument("--base-graph-dir", default="./data/graph")
    args = parser.parse_args()

    nx_repo = NetworkXJsonGraphRepository(base_graph_dir=args.base_graph_dir)
    neo_repo = Neo4jGraphRepository(
        uri=os.getenv("NEO4J_URI", ""),
        user=os.getenv("NEO4J_USER", ""),
        password=os.getenv("NEO4J_PASSWORD", ""),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
        base_graph_dir=args.base_graph_dir,
    )
    try:
        failures = 0
        for collection_id in args.collections:
            nx_graph = nx_repo.load_collection_graph(collection_id)
            neo_graph = neo_repo.load_collection_graph(collection_id)
            nx_counts = _counts(nx_graph)
            neo_counts = _counts(neo_graph)
            if nx_counts != neo_counts:
                failures += 1
                print(f"FAIL {collection_id}: networkx={nx_counts} neo4j={neo_counts}")
            else:
                print(f"OK   {collection_id}: nodes={nx_counts[0]} edges={nx_counts[1]}")
        if failures:
            raise SystemExit(1)
    finally:
        neo_repo.close()


if __name__ == "__main__":
    main()

