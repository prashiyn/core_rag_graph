import json
import os
from pathlib import Path
import networkx as nx

from graph.utils.logger import logger
from graph.utils.community_reports import CommunityReportsExtractor
from graph.utils.resolution import LLMEntityResolver
from graph.utils.collection_id_scope import (
    resolve_graph_json_path,
    to_external_collection_id,
)


GRAPH_FIELD_SEP = "<SEP>"
OP_METRICS = {
    "merge_conflicts_total": 0,
    "failed_resolutions_total": 0,
}


def get_operational_metrics() -> dict:
    return dict(OP_METRICS)

def generate_subgraph(relationships: list) -> nx.MultiDiGraph:
    """
    generate a sub knowledge graph

    Expected JSON format:
    [
        {
            "start_node": {
                "label": "entity",
                "properties": {"name": "Entity Name", "description": "..."}
            },
            "relation": "relation_type",
            "end_node": {
                "label": "entity",
                "properties": {"name": "Entity Name", "description": "..."}
            }
        }
    ]
    """
    graph = nx.MultiDiGraph()
    node_mapping = {}  # (name, schema_type) -> node_id

    for rel in relationships:
        start_node_data = rel["start_node"]
        end_node_data = rel["end_node"]
        relation = rel["relation"]

        # Create unique key for start node - ensure name is a string
        start_name = start_node_data["properties"].get("name", "")
        if isinstance(start_name, list):
            start_name = ", ".join(str(item) for item in start_name)
        elif not isinstance(start_name, str):
            start_name = str(start_name)

        schema_type = start_node_data["properties"].get("schema_type", "")
        start_key = (start_name, schema_type)
        if start_key not in node_mapping:
            node_id = start_name
            # if schema_type:
            #     node_id = f"{start_name}_{schema_type}"
            node_mapping[start_key] = node_id

            # Add node with all properties
            node_attrs = {
                "label": start_node_data["label"],
                "properties": start_node_data["properties"],
                "description": "",
            }

            # Add level based on label
            if start_node_data["label"] == "attribute":
                node_attrs["level"] = 1
            elif start_node_data["label"] == "entity":
                node_attrs["level"] = 2
            elif start_node_data["label"] == "keyword":
                node_attrs["level"] = 3
            elif start_node_data["label"] == "community":
                node_attrs["level"] = 4
            else:
                node_attrs["level"] = 2  # Default level

            graph.add_node(node_id, **node_attrs)

        # Create unique key for end node - ensure name is a string
        end_name = end_node_data["properties"].get("name", "")
        if isinstance(end_name, list):
            end_name = ", ".join(str(item) for item in end_name)
        elif not isinstance(end_name, str):
            end_name = str(end_name)

        schema_type = end_node_data["properties"].get("schema_type", "")
        end_key = (end_name, schema_type)
        if end_key not in node_mapping:
            node_id = end_name
            # if schema_type:
            #     node_id = f"{end_name}_{schema_type}"
            node_mapping[end_key] = node_id

            # Add node with all properties
            node_attrs = {
                "label": end_node_data["label"],
                "properties": end_node_data["properties"],
                "description": "",
            }

            # Add level based on label
            if end_node_data["label"] == "attribute":
                node_attrs["level"] = 1
            elif end_node_data["label"] == "entity":
                node_attrs["level"] = 2
            elif end_node_data["label"] == "keyword":
                node_attrs["level"] = 3
            elif end_node_data["label"] == "community":
                node_attrs["level"] = 4
            else:
                node_attrs["level"] = 2  # Default level

            graph.add_node(node_id, **node_attrs)

        # Add edge
        start_id = node_mapping[start_key]
        end_id = node_mapping[end_key]
        graph.add_edge(start_id, end_id, relation=relation)

    return graph


def load_graph_from_json(input_path: str) -> nx.MultiDiGraph:
    """
    Load a knowledge graph from JSON format
    
    Expected JSON format:
    [
        {
            "start_node": {
                "label": "entity",
                "properties": {"name": "Entity Name", "description": "..."}
            },
            "relation": "relation_type",
            "end_node": {
                "label": "entity", 
                "properties": {"name": "Entity Name", "description": "..."}
            }
        }
    ]
    """
    with open(input_path, 'r', encoding='utf-8') as f:
        relationships = json.load(f)

    return generate_subgraph(relationships)


def save_graph_to_json(graph: nx.MultiDiGraph, output_path: str):
    """
    Save a knowledge graph to JSON format
    
    Output format:
    [
        {
            "start_node": {
                "label": "entity",
                "properties": {"name": "Entity Name", "description": "..."}
            },
            "relation": "relation_type", 
            "end_node": {
                "label": "entity",
                "properties": {"name": "Entity Name", "description": "..."}
            }
        }
    ]
    """
    output = []
    
    for u, v, data in graph.edges(data=True):
        u_data = graph.nodes[u]
        v_data = graph.nodes[v]
        
        relationship = {
            "start_node": {
                "label": u_data["label"],
                "properties": u_data["properties"],
                "description": u_data["description"],
            },
            "relation": data["relation"],
            "end_node": {
                "label": v_data["label"],
                "properties": v_data["properties"],
                "description": u_data["description"],
            },
        }
        output.append(relationship)

    # Ensure parent directory exists
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


# Legacy function for backward compatibility
def load_graph(input_path: str) -> nx.MultiDiGraph:
    """
    Load graph from either JSON or GraphML format (legacy support)
    """
    if input_path.endswith('.json'):
        return load_graph_from_json(input_path)
    elif input_path.endswith('.graphml'):
        return load_graph_from_graphml(input_path)
    else:
        raise ValueError(f"Unsupported file format: {input_path}")


def load_graph_from_graphml(input_path: str) -> nx.MultiDiGraph:
    """
    Load graph from GraphML format (legacy function)
    """
    graph_data = nx.read_graphml(input_path)
    
    for node_id, data in graph_data.nodes(data=True):
        # Handle properties (d1)
        if "d1" in data:
            try:
                data["properties"] = json.loads(data["d1"])
                del data["d1"]
            except json.JSONDecodeError:
                logger.warning(f"Warning: Could not parse properties for node {node_id}")
                data["properties"] = {"name": str(data["d1"])}
                del data["d1"]
        
        # Handle level (d2)
        if "d2" in data:
            try:
                data["level"] = int(data["d2"])
                del data["d2"]
            except (ValueError, TypeError):
                data["level"] = 2  # Default level if conversion fails
                del data["d2"]
        
        # Handle label (d0)
        if "d0" in data:
            data["label"] = str(data["d0"])
            del data["d0"]
    
    for u, v, data in graph_data.edges(data=True):
        # Handle relation (d3)
        if "d3" in data:
            data["relation"] = str(data["d3"]).strip('"')
            del data["d3"]
    
    return graph_data


def save_graph(graph: nx.MultiDiGraph, output_path: str):
    """
    Save graph to either JSON or GraphML format based on file extension
    """
    if output_path.endswith('.json'):
        save_graph_to_json(graph, output_path)
    elif output_path.endswith('.graphml'):
        save_graph_to_graphml(graph, output_path)
    else:
        raise ValueError(f"Unsupported output format: {output_path}")


def save_graph_to_graphml(graph: nx.MultiDiGraph, output_path: str):
    """
    Save graph to GraphML format (legacy function)
    """
    # Create a copy of the graph to avoid modifying the original
    graph_copy = graph.copy()
    
    for n, data in graph_copy.nodes(data=True):
        for k, v in list(data.items()):  
            if isinstance(v, dict):
                graph_copy.nodes[n][k] = json.dumps(v, ensure_ascii=False)

    for u, v, data in graph_copy.edges(data=True):
        for k, v in list(data.items()):
            if isinstance(v, dict):
                graph_copy.edges[u, v][k] = json.dumps(v, ensure_ascii=False)

    nx.write_graphml(graph_copy, output_path)


def delete_file(collection_id: str, file_name: str):
    to_remove_nodes = []
    graph = None

    file_path = Path(file_name)
    internal_graph_path = f"./data/graph/{collection_id}.json"
    load_path = resolve_graph_json_path("./data/graph", collection_id)
    if load_path is None:
        load_path = internal_graph_path
    if os.path.exists(load_path):
        graph = load_graph(load_path)

    if graph is not None:
        for node_name, attr in graph.nodes(data=True):
            file_names = attr['properties']['file_names']
            file_names = [x for x in file_names if x != file_name]
            attr['properties']['file_names'] = file_names
            if not file_names:
                to_remove_nodes.append(node_name)

        for node_name in to_remove_nodes:
            graph.remove_node(node_name)

        for node_degree in graph.degree:
            graph.nodes[str(node_degree[0])]["rank"] = int(node_degree[1])

        save_graph(graph, internal_graph_path)
        if load_path != internal_graph_path and os.path.exists(load_path):
            os.remove(load_path)

    return graph

def delete_collection(collection_id: str):
    internal_path = f"./data/graph/{collection_id}.json"
    legacy_path = f"./data/graph/{to_external_collection_id(collection_id)}.json"
    for graph_path in {internal_path, legacy_path}:
        if os.path.exists(graph_path):
            os.remove(graph_path)


def graph_merge(g1: nx.MultiDiGraph, g2: nx.MultiDiGraph):
    """Merge graph g2 into g1 in place."""
    for node_name, attr in g2.nodes(data=True):
        if not g1.has_node(node_name):
            g1.add_node(node_name, **attr)
            continue
        file_names = g1.nodes[node_name]['properties']['file_names']
        g1.nodes[node_name]['properties']['file_names'] = list(set(file_names + attr['properties']['file_names']))

    for source, target, attr in g2.edges(data=True):
        edge = g1.get_edge_data(source, target)
        if edge is None:
            g1.add_edge(source, target, **attr)
            continue

    for node_degree in g1.degree:
        g1.nodes[str(node_degree[0])]["rank"] = int(node_degree[1])

    return g1


def merge_subgraph(
    subgraph: nx.MultiDiGraph,
    save_graph_path: str,
    load_graph_path: str | None = None,
) -> nx.MultiDiGraph:
    """Merge ``subgraph`` into an existing graph loaded from disk, then save to ``save_graph_path``."""
    source_path = None
    if load_graph_path and os.path.exists(load_graph_path):
        source_path = load_graph_path
    elif os.path.exists(save_graph_path):
        source_path = save_graph_path

    if source_path and os.path.exists(source_path):
        old_graph = load_graph(source_path)
        if old_graph is not None:
            logger.info("Merge with an exiting graph...................")
            new_graph = graph_merge(old_graph, subgraph)
        else:
            new_graph = subgraph
    else:
        new_graph = subgraph
    pr = nx.pagerank(new_graph)
    for node_name, pagerank in pr.items():
        new_graph.nodes[node_name]["pagerank"] = pagerank

    save_graph(new_graph, save_graph_path)
    if source_path and source_path != save_graph_path and os.path.exists(source_path):
        os.remove(source_path)

    return new_graph


def extract_community(graph, config):
    ext = CommunityReportsExtractor(config)
    cr = ext(graph)
    community_structure = cr.structured_output
    community_reports = cr.output

    reports = []
    for structure, rep in zip(community_structure, community_reports):
        obj = {
            "report": rep,
            "entities": structure["entities"],
            "report_title": structure["title"],
            "report_summary": structure["summary"],
        }
        reports.append(obj)

    return reports


def update_graph(collection_id: str, file_name: str, relationships: list, config):
    # =========== build subgraph ============
    subgraph = generate_subgraph(relationships)

    # =========== merge subgraph ============
    internal_file_path = f"./data/graph/{collection_id}.json"
    load_path = resolve_graph_json_path("./data/graph", collection_id)

    if load_path:
        old_graph = load_graph(load_path)
        if old_graph is not None:
            logger.info("resolution graph...................")
            try:
                resolver = LLMEntityResolver(config)
                name_mapping = resolver.resolve_by_name_and_llm(old_graph, subgraph)
            except Exception as e:
                OP_METRICS["failed_resolutions_total"] += 1
                logger.error(f"resolution graph failed, fallback to direct merge: {e}")
                name_mapping = {}
            OP_METRICS["merge_conflicts_total"] += len(name_mapping)
            logger.info(f"resolution graph, name mapping: {name_mapping}")

            for relationship in relationships:
                start_name = relationship["start_node"]["properties"]["name"]
                if start_name in name_mapping:
                    relationship["start_node"]["properties"]["name"] = name_mapping[start_name]
                end_name = relationship["end_node"]["properties"]["name"]
                if end_name in name_mapping:
                    relationship["end_node"]["properties"]["name"] = name_mapping[end_name]

    subgraph = generate_subgraph(relationships)
    new_graph = merge_subgraph(subgraph, internal_file_path, load_path)

    return new_graph

if __name__ == "__main__":
    relationships = [{'start_node': {'label': 'entity',
                                     'properties': {'name': 'Tibetan opera mask', 'schema_type': 'artifact'}},
                      'relation': 'has_attribute',
                      'end_node': {'label': 'attribute',
                                   'properties': {'name': 'period: Ming–Qing and later'}}},
                     {'start_node': {'label': 'entity',
                                     'properties': {'name': 'Tibetan opera mask', 'schema_type': 'artifact'}},
                      'relation': 'has_attribute',
                      'end_node': {'label': 'attribute',
                                   'properties': {'name': 'kinds: king, queen, lama, deity, animal masks, etc.'}}},
                     {'start_node': {'label': 'entity',
                                     'properties': {'name': 'Tibetan opera mask', 'schema_type': 'artifact'}},
                      'relation': 'has_attribute',
                      'end_node': {'label': 'attribute', 'properties': {'name': 'materials: cloth, leather, wood'}}},
                     {'start_node': {'label': 'entity',
                                     'properties': {'name': 'Tibetan opera mask', 'schema_type': 'artifact'}},
                      'relation': 'has_attribute',
                      'end_node': {'label': 'attribute', 'properties': {'name': 'craft: painting, carving, sewing'}}}]
    sub_graph = load_graph_from_json(relationships)
    print(sub_graph)
