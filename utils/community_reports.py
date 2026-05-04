import copy
import json
import re
import os
from typing import Callable
from dataclasses import dataclass
import networkx as nx
import pandas as pd
from graph.config.prompt_templates import COMMUNITY_REPORT_PROMPT, ATTRIBUTE_PROMPT
from graph.utils import leiden
from graph.utils.leiden import add_community_info2graph
from graph.config import get_config
from graph.utils import call_llm_api
from concurrent import futures
from graph.utils.logger import logger

def perform_variable_replacements(
    input: str, history: list[dict] | None = None, variables: dict | None = None
) -> str:
    """Perform variable replacements on the input string and in a chat log."""
    if history is None:
        history = []
    if variables is None:
        variables = {}
    result = input

    def replace_all(input: str) -> str:
        result = input
        for k, v in variables.items():
            result = result.replace(f"{{{k}}}", str(v))
        return result

    result = replace_all(result)
    for i, entry in enumerate(history):
        if entry.get("role") == "system":
            entry["content"] = replace_all(entry.get("content") or "")

    return result


def dict_has_keys_with_types(
    data: dict, expected_fields: list[tuple[str, type]]
) -> bool:
    """Return True if the given dictionary has the given keys with the given types."""
    for field, field_type in expected_fields:
        if field not in data:
            return False

        value = data[field]
        if not isinstance(value, field_type):
            return False
    return True


@dataclass
class CommunityReportsResult:
    """Community reports result class definition."""

    output: list[str]
    structured_output: list[dict]


def is_valid_name(name: str) -> bool:
    """True if name is non-empty, longer than one char, and not only punctuation."""
    if not name or not isinstance(name, str):
        return False
    name = name.strip()
    if not name or len(name) <= 1:
        return False
    # Reject strings that are only punctuation or whitespace
    if re.fullmatch(r'[:：,，.。;；!！?？\-_=+\s]+', name):
        return False
    return True


class CommunityReportsExtractor:
    """Community reports extractor class definition."""

    _extraction_prompt: str
    _output_formatter_prompt: str
    _max_report_length: int

    def __init__(
            self,
            max_report_length: int | None = None,
            config=None,
    ):
        if config is None:
            config = get_config()
        """Init method definition."""
        self._llm_client = call_llm_api.LLMCompletionCall(use_case="community_cluster_report")
        self._config = config
        self._extraction_prompt = COMMUNITY_REPORT_PROMPT
        self._max_report_length = max_report_length or 1500

    def __call__(self, graph: nx.MultiDiGraph):
        for node_degree in graph.degree:
            graph.nodes[str(node_degree[0])]["rank"] = int(node_degree[1])

        max_workers = min(self._config.construction.max_workers, (os.cpu_count() or 1) + 4)
        communities: dict[str, dict[str, list]] = leiden.run(graph, {})
        res_str = []
        res_dict = []

        def extract_report_from_response(response: str, entities: list):
            nonlocal res_str, res_dict
            response = re.sub(r"^[^\{]*", "", response)
            response = re.sub(r"[^\}]*$", "", response)
            response = re.sub(r"\{\{", "{", response)
            response = re.sub(r"\}\}", "}", response)
            logger.debug(response)
            try:
                response = json.loads(response)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Response content: {response}")
                return
            if not dict_has_keys_with_types(response, [
                        ("title", str),
                        ("summary", str),
                        ("findings", list),
                        ("rating", float),
                        ("rating_explanation", str),
                    ]):
                return
            response["entities"] = entities
            add_community_info2graph(graph, entities, response["title"])
            res_str.append(self._get_text_output(response))
            res_dict.append(response)

        def extract_community_report_by_attr(community):
            attribute, cm = community
            entities = cm["nodes"]

            # Collect reference_snippets from related chunks (deduplicated)
            all_snippets = set()
            for ent in entities:
                node_data = graph.nodes[ent]
                reference_snippets = node_data.get("properties", {}).get("reference_snippets", [])
                for snippet in reference_snippets:
                    all_snippets.add(snippet)

            # Merge snippets into one context block
            context = "\n\n".join(list(all_snippets)) if all_snippets else ""

            # Build user message
            if context:
                user_content = ATTRIBUTE_PROMPT + "\n\n" + "Input:\n" + f"entities = {json.dumps(entities, ensure_ascii=False)}\n" + f'attribute = "{attribute}"\n' + f'related_chunks = """{context}"""'
            else:
                user_content = ATTRIBUTE_PROMPT + "\n\n" + "Input:\n" + f"entities = {json.dumps(entities, ensure_ascii=False)}\n" + f'attribute = "{attribute}"'

            response = self._llm_client.call_api(user_content, use_case="community_attribute_report")
            extract_report_from_response(response, entities)

        def extract_community_report(community):
            nonlocal res_str, res_dict
            cm_id, cm = community
            ents = cm["nodes"]
            if len(ents) <= 2:
                return

            new_ents = copy.deepcopy(ents)
            for ent in ents:
                neighbors = graph.neighbors(ent)
                for nei in neighbors:
                    if nei not in new_ents:
                        new_ents.append(nei)

            ents = new_ents

            # Collect reference_snippets (deduplicated)
            all_snippets = set()
            for ent in ents:
                node_data = graph.nodes[ent]
                reference_snippets = node_data.get("properties", {}).get("reference_snippets", [])
                for snippet in reference_snippets:
                    all_snippets.add(snippet)

            # Merge snippets into context
            context = "\n\n".join(list(all_snippets)) if all_snippets else ""

            ent_list = [{"entity": ent, "description": graph.nodes[ent]["description"]} for ent in ents]
            ent_df = pd.DataFrame(ent_list)

            rela_list = []
            k = 0
            for i in range(0, len(ents)):
                if k >= 10000:
                    break
                for j in range(i + 1, len(ents)):
                    if k >= 10000:
                        break
                    edge = graph.get_edge_data(ents[i], ents[j])
                    if edge is None:
                        continue
                    rela_list.append({"source": ents[i], "target": ents[j], "description": edge.values()})
                    k += 1
            rela_df = pd.DataFrame(rela_list)

            prompt_variables = {
                "entity_df": ent_df.to_csv(index_label="id"),
                "relation_df": rela_df.to_csv(index_label="id")
            }

            # Append optional context to the extraction prompt
            text = perform_variable_replacements(self._extraction_prompt, variables=prompt_variables)
            if context:
                text += f"\n\nRelated context:\n{context}"

            response = self._llm_client.call_api(text, use_case="community_cluster_report")
            extract_report_from_response(response, ents)

        attribute_comm = {}
        for edge in graph.edges(data=True):
            if edge[2]["relation"] == "has_attribute":
                target_node = edge[1]
                source_node = edge[0]
                # Skip invalid entity/attribute labels
                if not is_valid_name(target_node) or not is_valid_name(source_node):
                    continue
                if target_node not in attribute_comm:
                    attribute_comm[target_node] = [source_node, target_node]  # include attribute node itself
                else:
                    attribute_comm[target_node].append(source_node)

        # Drop attribute communities with too few nodes
        attribute_comm = {k: {"nodes": v} for k, v in attribute_comm.items() if len(v) > 3}

        attribute_keys_to_remove = set()
        attribute_sets = {k: set(v["nodes"]) for k, v in attribute_comm.items()}
        # Only Leiden level 0
        for level, comm in communities.items():
            if int(level) > 0:
                continue
            for cm_id, cm in comm.items():
                comm_nodes_set = set(cm["nodes"])
                for attr_key, attr_nodes_set in attribute_sets.items():
                    if attr_nodes_set == comm_nodes_set:
                        attribute_keys_to_remove.add(attr_key)
        for k in attribute_keys_to_remove:
            attribute_comm.pop(k, None)

        logger.info(f"attribute_comm: {attribute_comm}")

        with futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            all_futures = [executor.submit(extract_community_report_by_attr, community) for community in attribute_comm.items()]
            for i, future in enumerate(futures.as_completed(all_futures)):
                try:
                    future.result()
                except Exception as e:
                    logger.info(f"extract_community_report_by_attr Failed, error: {e}")

        for level, comm in communities.items():
            if int(level) > 0:
                continue
            logger.info(f"Level {level}: Community: {len(comm.keys())}")
            with futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                all_futures = [executor.submit(extract_community_report, community) for community in comm.items()]
                for i, future in enumerate(futures.as_completed(all_futures)):
                    try:
                        future.result()
                    except Exception as e:
                        logger.info(f"extract_community_report Failed, error: {e}")

        return CommunityReportsResult(
            structured_output=res_dict,
            output=res_str,
        )

    def _get_text_output(self, parsed_output: dict) -> str:
        title = parsed_output.get("title", "Report")
        summary = parsed_output.get("summary", "")
        findings = parsed_output.get("findings", [])

        def finding_summary(finding: dict):
            if isinstance(finding, str):
                return finding
            return finding.get("summary")

        def finding_explanation(finding: dict):
            if isinstance(finding, str):
                return ""
            return finding.get("explanation")

        report_sections = "\n\n".join(
            f"## {finding_summary(f)}\n\n{finding_explanation(f)}" for f in findings
        )
        return f"# {title}\n\n{summary}\n\n{report_sections}"
