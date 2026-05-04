import re
from typing import Any, List, Dict, Tuple

import json_repair
import networkx as nx

from graph.config import get_config
from graph.utils import call_llm_api
from graph.utils.logger import logger

def _levenshtein_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la

    # ensure a is the shorter string to use less memory
    if la > lb:
        a, b = b, a
        la, lb = lb, la

    previous_row = list(range(la + 1))
    for i in range(1, lb + 1):
        c = b[i - 1]
        current_row = [i] + [0] * la
        for j in range(1, la + 1):
            insertions = previous_row[j] + 1
            deletions = current_row[j - 1] + 1
            substitutions = previous_row[j - 1] + (a[j - 1] != c)
            current_row[j] = min(insertions, deletions, substitutions)
        previous_row = current_row
    return previous_row[la]


class LLMEntityResolver:
    def __init__(self, config=None):
        if config is None:
            config = get_config()
        """Init method definition."""
        self._llm_client = call_llm_api.LLMCompletionCall(use_case="entity_resolution")

    def resolve_by_name_and_llm(
            self,
            old_graph: nx.Graph,
            new_graph: nx.Graph
    ) -> Dict[str, str]:
        """
        Resolve entities using name similarity plus LLM confirmation.
        Returns mapping: {new_entity_name: old_entity_name}
        """
        logger.info("Starting entity resolution...")
        # 1. Candidate pairs from name similarity
        candidate_pairs = self._find_similar_entities(old_graph, new_graph)
        logger.info(f"Candidate entity pairs: {len(candidate_pairs)}")
        # 2. Keep best target per source node
        best_pairs = self._select_best_match(candidate_pairs)
        logger.info(f"Pairs after best-match filter: {len(best_pairs)}")
        # 3. LLM confirmation
        confirmed_mappings = self._llm_decision(best_pairs, old_graph, new_graph)
        logger.info(f"Confirmed entity pairs: {len(confirmed_mappings)}")
        return confirmed_mappings

    def _find_similar_entities(
            self,
            old_graph: nx.Graph,
            new_graph: nx.Graph
    ) -> List[Tuple[str, str]]:
        """Find candidate entity pairs by name similarity, including within new_graph."""
        candidates = []
        # 1. old_graph vs new_graph
        for new_node in new_graph.nodes():
            new_attrs = new_graph.nodes[new_node]
            new_type = new_attrs.get('label')
            new_schema_type = new_attrs['properties'].get('schema_type', '')

            for old_node in old_graph.nodes():
                old_attrs = old_graph.nodes[old_node]
                old_type = old_attrs.get('label')
                old_schema_type = new_attrs['properties'].get('schema_type', '')

                # Same type and similar names
                if new_type == old_type and new_schema_type == old_schema_type and self._is_name_similar(new_node, old_node):
                    candidates.append((new_node, old_node))

        # 2. Within new_graph (pairwise)
        new_nodes = list(new_graph.nodes())
        n = len(new_nodes)
        for i in range(n):
            node_i = new_nodes[i]
            attrs_i = new_graph.nodes[node_i]
            type_i = attrs_i.get('label')
            schema_type_i = attrs_i['properties'].get('schema_type', '')
            for j in range(i + 1, n):
                node_j = new_nodes[j]
                attrs_j = new_graph.nodes[node_j]
                type_j = attrs_j.get('label')
                schema_type_j = attrs_j['properties'].get('schema_type', '')
                # Same type and schema_type and similar names
                if type_i == type_j and schema_type_i == schema_type_j and self._is_name_similar(node_i, node_j):
                    candidates.append((node_i, node_j))
        return candidates

    def _is_name_similar(self, name1: str, name2: str) -> bool:
        """Return True if two names are similar (edit distance heuristic)."""
        distance = _levenshtein_distance(name1.lower(), name2.lower())
        return distance <= min(len(name1), len(name2)) // 2

    def _llm_decision(
            self,
            candidate_pairs: List[Tuple[str, str]],
            old_graph: nx.Graph,
            new_graph: nx.Graph
    ) -> Dict[str, str]:
        """Use the LLM to confirm whether each candidate pair refers to the same thing."""
        if not candidate_pairs:
            return {}

        # Build prompt
        prompt = self._build_llm_prompt(candidate_pairs, old_graph, new_graph)
        logger.info(f"_llm_decision prompt: {prompt}")
        # Call LLM
        response = self._llm_client.call_api(prompt)
        logger.info(f"_llm_decision response: {response}")
        # Parse response
        return self._parse_llm_response(response, candidate_pairs)

    def _build_llm_prompt(
            self,
            candidate_pairs: List[Tuple[str, str]],
            old_graph: nx.Graph,
            new_graph: nx.Graph
    ) -> str:
        """Build the LLM prompt: pairs in order, response must be JSON only."""
        n = len(candidate_pairs)
        prompt_lines = [
            "For each numbered pair below, decide whether A and B refer to the same real-world entity "
            "(for entity nodes) or the same attribute meaning (for attribute nodes).",
            "",
            "Respond with ONLY valid JSON (no markdown fences, no commentary). "
            f'The object must have key "same" whose value is a JSON array of exactly {n} booleans, '
            "one per pair below in order (pair 1 first).",
            'Example shape for 3 pairs: {"same": [true, false, true]}',
            "",
            "Pairs to judge:",
            "",
        ]
        for idx, (new_name, old_name) in enumerate(candidate_pairs, 1):
            new_attrs = new_graph.nodes[new_name]['properties']
            if old_name in old_graph.nodes:
                old_attrs = old_graph.nodes[old_name]['properties']
            elif old_name in new_graph.nodes:
                old_attrs = new_graph.nodes[old_name]['properties']
            else:
                old_attrs = {}
                logger.warning(
                    f"Entity resolution prompt: B-side node {old_name!r} missing from graphs"
                )

            label = new_graph.nodes[new_name].get('label')
            if label == "entity":
                prompt_lines.append(f"Pair {idx} (entity):")
                prompt_lines.append(f"  A: {new_name}")
                prompt_lines.append(f"    type: {new_attrs.get('schema_type', '')}")
                prompt_lines.append(f"  B: {old_name}")
                prompt_lines.append(f"    type: {old_attrs.get('schema_type', '')}")
                prompt_lines.append("")
            elif label == "attribute":
                prompt_lines.append(f"Pair {idx} (attribute):")
                prompt_lines.append(f"  A: {new_name}")
                prompt_lines.append(f"  B: {old_name}")
                prompt_lines.append("")
            else:
                prompt_lines.append(f"Pair {idx} ({label or 'unknown'}):")
                prompt_lines.append(f"  A: {new_name}")
                prompt_lines.append(f"  B: {old_name}")
                prompt_lines.append("")
        prompt_lines.append(
            f'Return JSON only: {{"same": [ ... ]}} with length {n} (same order as pairs 1..{n}).'
        )
        return "\n".join(prompt_lines)

    @staticmethod
    def _coerce_same_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1")
        if isinstance(value, (int, float)):
            return value == 1
        return False

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        s = text.strip()
        if not s.startswith("```"):
            return s
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
        return s.strip()

    def _parse_llm_response(
            self,
            response: str,
            candidate_pairs: List[Tuple[str, str]]
    ) -> Dict[str, str]:
        """Parse JSON { \"same\": [bool, ...] } into a canonical name mapping."""
        raw = self._strip_code_fence(response)
        try:
            data = json_repair.loads(raw)
        except Exception as e:
            logger.warning(f"Entity resolution: invalid JSON from LLM: {e}")
            return {}

        if not isinstance(data, dict):
            logger.warning("Entity resolution: LLM output is not a JSON object")
            return {}

        same_list = data.get("same")
        if not isinstance(same_list, list):
            logger.warning("Entity resolution: expected top-level key 'same' with a JSON array")
            return {}

        n = len(candidate_pairs)
        if len(same_list) != n:
            logger.warning(
                f"Entity resolution: expected 'same' length {n}, got {len(same_list)}; "
                "using minimum length"
            )

        mappings: Dict[str, str] = {}
        for i in range(min(n, len(same_list))):
            if not self._coerce_same_flag(same_list[i]):
                continue
            new_name, old_name = candidate_pairs[i]
            if new_name != old_name:
                mappings[new_name] = old_name

        return mappings

    def _select_best_match(self, candidate_pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """For each source node, keep only the candidate with smallest edit distance."""
        best_map = {}
        for node_i, node_j in candidate_pairs:
            dist = _levenshtein_distance(node_i.lower(), node_j.lower())
            if node_i not in best_map or dist < best_map[node_i][1]:
                best_map[node_i] = (node_j, dist)
        return [(node_i, node_j) for node_i, (node_j, _) in best_map.items()]
