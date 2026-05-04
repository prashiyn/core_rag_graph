import json
import os
from pathlib import Path
from typing import Any, List, Dict, Protocol

import networkx as nx
from neo4j import GraphDatabase
from neo4j import Driver

from graph.utils import graph_processor
from graph.utils.resolution import LLMEntityResolver
from graph.utils.collection_id_scope import (
    resolve_community_reports_json_path,
    resolve_graph_json_path,
    to_external_collection_id,
)


class GraphRepository(Protocol):
    def merge_relationships(
        self,
        collection_id: str,
        file_name: str,
        relationships: List[Dict],
        config,
    ) -> nx.MultiDiGraph:
        ...

    def delete_file(self, collection_id: str, file_name: str) -> nx.MultiDiGraph | None:
        ...

    def delete_collection(self, collection_id: str) -> None:
        ...

    def load_collection_graph(self, collection_id: str) -> nx.MultiDiGraph | None:
        ...

    def save_community_reports(self, collection_id: str, reports: List[Dict]) -> None:
        ...

    def load_community_reports(self, collection_id: str) -> List[Dict] | None:
        ...

    def replace_collection_graph(
        self,
        collection_id: str,
        relationships: List[Dict],
        config,
    ) -> nx.MultiDiGraph:
        ...

    def close(self) -> None:
        ...


class NetworkXJsonGraphRepository:
    """File-backed graph repository using NetworkX and JSON artifacts."""

    def __init__(self, base_graph_dir: str = "./data/graph"):
        self.base_graph_dir = base_graph_dir

    def _graph_path(self, collection_id: str) -> str:
        return f"{self.base_graph_dir}/{collection_id}.json"

    def _community_reports_path(self, collection_id: str) -> str:
        return f"{self.base_graph_dir}/{collection_id}_community_reports.json"

    def merge_relationships(
        self,
        collection_id: str,
        file_name: str,
        relationships: List[Dict],
        config,
    ) -> nx.MultiDiGraph:
        return graph_processor.update_graph(collection_id, file_name, relationships, config)

    def delete_file(self, collection_id: str, file_name: str) -> nx.MultiDiGraph | None:
        return graph_processor.delete_file(collection_id, file_name)

    def delete_collection(self, collection_id: str) -> None:
        graph_processor.delete_collection(collection_id)
        primary = self._community_reports_path(collection_id)
        legacy = f"{self.base_graph_dir}/{to_external_collection_id(collection_id)}_community_reports.json"
        for community_path in {primary, legacy}:
            if os.path.exists(community_path):
                os.remove(community_path)

    def load_collection_graph(self, collection_id: str) -> nx.MultiDiGraph | None:
        path = resolve_graph_json_path(self.base_graph_dir, collection_id)
        if path is None:
            return None
        return graph_processor.load_graph_from_json(path)

    def save_community_reports(self, collection_id: str, reports: List[Dict]) -> None:
        out_path = self._community_reports_path(collection_id)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)

    def load_community_reports(self, collection_id: str) -> List[Dict] | None:
        path = resolve_community_reports_json_path(self.base_graph_dir, collection_id)
        if path is None:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def replace_collection_graph(
        self,
        collection_id: str,
        relationships: List[Dict],
        config,
    ) -> nx.MultiDiGraph:
        graph_processor.delete_collection(collection_id)
        if not relationships:
            return nx.MultiDiGraph()
        return graph_processor.update_graph(collection_id, "__migration__.json", relationships, config)

    def close(self) -> None:
        return None


class Neo4jGraphRepository:
    """Neo4j-backed graph repository using official neo4j driver."""

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
        base_graph_dir: str = "./data/graph",
    ):
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database
        self.base_graph_dir = base_graph_dir
        self._ensure_constraints()

    def close(self) -> None:
        self._driver.close()

    def _community_reports_path(self, collection_id: str) -> str:
        return f"{self.base_graph_dir}/{collection_id}_community_reports.json"

    def _ensure_constraints(self) -> None:
        with self._driver.session(database=self._database) as session:
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

    @staticmethod
    def _safe_schema_type(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    def _upsert_relationship(self, tx, collection_id: str, rel: Dict) -> None:
        start = rel["start_node"]
        end = rel["end_node"]
        relation = str(rel["relation"])
        start_props = dict(start["properties"])
        end_props = dict(end["properties"])
        start_props["schema_type"] = self._safe_schema_type(start_props.get("schema_type"))
        end_props["schema_type"] = self._safe_schema_type(end_props.get("schema_type"))
        tx.run(
            """
            MERGE (s:GraphNode {
                collection_id: $collection_id,
                name: $start_name,
                schema_type: $start_schema_type,
                label: $start_label
            })
            SET s.properties = $start_props,
                s.level = $start_level,
                s.description = coalesce($start_description, "")
            MERGE (e:GraphNode {
                collection_id: $collection_id,
                name: $end_name,
                schema_type: $end_schema_type,
                label: $end_label
            })
            SET e.properties = $end_props,
                e.level = $end_level,
                e.description = coalesce($end_description, "")
            MERGE (s)-[r:GRAPH_REL {collection_id: $collection_id, relation: $relation}]->(e)
            """,
            collection_id=collection_id,
            start_name=start_props.get("name", ""),
            start_schema_type=start_props.get("schema_type", ""),
            start_label=start.get("label", ""),
            start_props=start_props,
            start_level=self._level_for_label(start.get("label", "")),
            start_description=start.get("description", ""),
            end_name=end_props.get("name", ""),
            end_schema_type=end_props.get("schema_type", ""),
            end_label=end.get("label", ""),
            end_props=end_props,
            end_level=self._level_for_label(end.get("label", "")),
            end_description=end.get("description", ""),
            relation=relation,
        )

    @staticmethod
    def _level_for_label(label: str) -> int:
        if label == "attribute":
            return 1
        if label == "entity":
            return 2
        if label == "keyword":
            return 3
        if label == "community":
            return 4
        return 2

    def _load_relationships(self, collection_id: str) -> List[Dict]:
        with self._driver.session(database=self._database) as session:
            records = session.run(
                """
                MATCH (s:GraphNode {collection_id: $collection_id})
                      -[r:GRAPH_REL {collection_id: $collection_id}]->
                      (e:GraphNode {collection_id: $collection_id})
                RETURN s, r, e
                """,
                collection_id=collection_id,
            )
            relationships: List[Dict] = []
            for rec in records:
                s = rec["s"]
                e = rec["e"]
                r = rec["r"]
                relationships.append(
                    {
                        "start_node": {
                            "label": s.get("label", ""),
                            "properties": s.get("properties", {}),
                            "description": s.get("description", ""),
                        },
                        "relation": r.get("relation", ""),
                        "end_node": {
                            "label": e.get("label", ""),
                            "properties": e.get("properties", {}),
                            "description": e.get("description", ""),
                        },
                    }
                )
            return relationships

    def _sync_pagerank(self, collection_id: str) -> None:
        graph = self.load_collection_graph(collection_id)
        if graph is None or graph.number_of_nodes() == 0:
            return
        pr = nx.pagerank(graph)
        with self._driver.session(database=self._database) as session:
            for node_name, pagerank in pr.items():
                node_data = graph.nodes[node_name]
                schema_type = self._safe_schema_type(node_data.get("properties", {}).get("schema_type", ""))
                label = node_data.get("label", "")
                session.run(
                    """
                    MATCH (n:GraphNode {
                        collection_id: $collection_id,
                        name: $name,
                        schema_type: $schema_type,
                        label: $label
                    })
                    SET n.pagerank = $pagerank
                    """,
                    collection_id=collection_id,
                    name=node_name,
                    schema_type=schema_type,
                    label=label,
                    pagerank=float(pagerank),
                )

    def merge_relationships(
        self,
        collection_id: str,
        file_name: str,
        relationships: List[Dict],
        config,
    ) -> nx.MultiDiGraph:
        old_graph = self.load_collection_graph(collection_id)
        subgraph = graph_processor.generate_subgraph(relationships)

        if old_graph is not None and old_graph.number_of_nodes() > 0:
            resolver = LLMEntityResolver(config)
            name_mapping = resolver.resolve_by_name_and_llm(old_graph, subgraph)
            if name_mapping:
                for relationship in relationships:
                    start_name = relationship["start_node"]["properties"].get("name")
                    end_name = relationship["end_node"]["properties"].get("name")
                    if start_name in name_mapping:
                        relationship["start_node"]["properties"]["name"] = name_mapping[start_name]
                    if end_name in name_mapping:
                        relationship["end_node"]["properties"]["name"] = name_mapping[end_name]

        with self._driver.session(database=self._database) as session:
            for rel in relationships:
                rel["start_node"]["properties"]["file_names"] = [file_name]
                rel["end_node"]["properties"]["file_names"] = [file_name]
                session.execute_write(self._upsert_relationship, collection_id, rel)

        self._sync_pagerank(collection_id)
        graph = self.load_collection_graph(collection_id)
        return graph if graph is not None else nx.MultiDiGraph()

    def delete_file(self, collection_id: str, file_name: str) -> nx.MultiDiGraph | None:
        graph = self.load_collection_graph(collection_id)
        if graph is None:
            return None
        for node_name, attr in list(graph.nodes(data=True)):
            file_names = attr.get("properties", {}).get("file_names", [])
            filtered = [x for x in file_names if x != file_name]
            attr["properties"]["file_names"] = filtered
            if not filtered:
                graph.remove_node(node_name)
        rebuilt: List[Dict] = []
        for u, v, data in graph.edges(data=True):
            u_data = graph.nodes[u]
            v_data = graph.nodes[v]
            rebuilt.append(
                {
                    "start_node": {
                        "label": u_data.get("label", ""),
                        "properties": u_data.get("properties", {}),
                        "description": u_data.get("description", ""),
                    },
                    "relation": data.get("relation", ""),
                    "end_node": {
                        "label": v_data.get("label", ""),
                        "properties": v_data.get("properties", {}),
                        "description": v_data.get("description", ""),
                    },
                }
            )

        self._replace_collection_graph(collection_id, rebuilt)
        return self.load_collection_graph(collection_id)

    def _replace_collection_graph(self, collection_id: str, relationships: List[Dict]) -> None:
        self.delete_collection(collection_id)
        if not relationships:
            return
        with self._driver.session(database=self._database) as session:
            for rel in relationships:
                session.execute_write(self._upsert_relationship, collection_id, rel)
        self._sync_pagerank(collection_id)

    def replace_collection_graph(
        self,
        collection_id: str,
        relationships: List[Dict],
        config,
    ) -> nx.MultiDiGraph:
        self._replace_collection_graph(collection_id, relationships)
        graph = self.load_collection_graph(collection_id)
        return graph if graph is not None else nx.MultiDiGraph()

    def delete_collection(self, collection_id: str) -> None:
        with self._driver.session(database=self._database) as session:
            session.run(
                """
                MATCH (n:GraphNode {collection_id: $collection_id})
                DETACH DELETE n
                """,
                collection_id=collection_id,
            )
        primary = self._community_reports_path(collection_id)
        legacy = f"{self.base_graph_dir}/{to_external_collection_id(collection_id)}_community_reports.json"
        for community_path in {primary, legacy}:
            if os.path.exists(community_path):
                os.remove(community_path)

    def load_collection_graph(self, collection_id: str) -> nx.MultiDiGraph | None:
        relationships = self._load_relationships(collection_id)
        if not relationships:
            return None
        return graph_processor.generate_subgraph(relationships)

    def save_community_reports(self, collection_id: str, reports: List[Dict]) -> None:
        out_path = self._community_reports_path(collection_id)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)

    def load_community_reports(self, collection_id: str) -> List[Dict] | None:
        path = resolve_community_reports_json_path(self.base_graph_dir, collection_id)
        if path is None:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


class DualWriteGraphRepository:
    """Dual-write wrapper with optional consistency checks."""

    def __init__(
        self,
        primary: GraphRepository,
        secondary: GraphRepository,
        check_consistency: bool = True,
    ):
        self.primary = primary
        self.secondary = secondary
        self.check_consistency = check_consistency

    @staticmethod
    def _graph_counts(graph: nx.MultiDiGraph | None) -> tuple[int, int]:
        if graph is None:
            return (0, 0)
        return (graph.number_of_nodes(), graph.number_of_edges())

    def _check_consistency(self, collection_id: str) -> None:
        if not self.check_consistency:
            return
        g1 = self.primary.load_collection_graph(collection_id)
        g2 = self.secondary.load_collection_graph(collection_id)
        c1 = self._graph_counts(g1)
        c2 = self._graph_counts(g2)
        if c1 != c2:
            raise RuntimeError(
                f"Dual-write consistency mismatch for collection={collection_id}: "
                f"primary nodes/edges={c1}, secondary nodes/edges={c2}"
            )

    def merge_relationships(
        self,
        collection_id: str,
        file_name: str,
        relationships: List[Dict],
        config,
    ) -> nx.MultiDiGraph:
        primary_graph = self.primary.merge_relationships(collection_id, file_name, relationships, config)
        self.secondary.merge_relationships(collection_id, file_name, relationships, config)
        self._check_consistency(collection_id)
        return primary_graph

    def delete_file(self, collection_id: str, file_name: str) -> nx.MultiDiGraph | None:
        g = self.primary.delete_file(collection_id, file_name)
        self.secondary.delete_file(collection_id, file_name)
        self._check_consistency(collection_id)
        return g

    def delete_collection(self, collection_id: str) -> None:
        self.primary.delete_collection(collection_id)
        self.secondary.delete_collection(collection_id)

    def load_collection_graph(self, collection_id: str) -> nx.MultiDiGraph | None:
        return self.primary.load_collection_graph(collection_id)

    def save_community_reports(self, collection_id: str, reports: List[Dict]) -> None:
        self.primary.save_community_reports(collection_id, reports)
        self.secondary.save_community_reports(collection_id, reports)

    def load_community_reports(self, collection_id: str) -> List[Dict] | None:
        return self.primary.load_community_reports(collection_id)

    def replace_collection_graph(
        self,
        collection_id: str,
        relationships: List[Dict],
        config,
    ) -> nx.MultiDiGraph:
        graph = self.primary.replace_collection_graph(collection_id, relationships, config)
        self.secondary.replace_collection_graph(collection_id, relationships, config)
        self._check_consistency(collection_id)
        return graph

    def close(self) -> None:
        for repo in (self.primary, self.secondary):
            close_fn = getattr(repo, "close", None)
            if callable(close_fn):
                close_fn()

