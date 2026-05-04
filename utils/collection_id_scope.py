"""Collection id scope: external (API) vs internal (storage, Neo4j, files).

External collection id: ``<collection_name>`` (no prefix).
Internal collection id: ``core_rag_<collection_name>``.
"""

from __future__ import annotations

import os
from typing import Any, MutableMapping

CORE_RAG_COLLECTION_PREFIX = "core_rag_"


def to_internal_collection_id(external_id: str) -> str:
    """Map API collection id to internal id used by this service."""
    if external_id is None:
        return ""
    s = str(external_id).strip()
    if not s:
        return s
    if s.startswith(CORE_RAG_COLLECTION_PREFIX):
        return s
    return f"{CORE_RAG_COLLECTION_PREFIX}{s}"


def to_external_collection_id(internal_id: str) -> str:
    """Map internal collection id to API-facing id (strip prefix once)."""
    if internal_id is None:
        return ""
    s = str(internal_id).strip()
    if s.startswith(CORE_RAG_COLLECTION_PREFIX):
        return s[len(CORE_RAG_COLLECTION_PREFIX) :]
    return s


def apply_inbound_collection_ids(obj: Any) -> Any:
    """Recursively prefix ``collection_id`` string values for request bodies."""
    if isinstance(obj, dict):
        d: MutableMapping[str, Any] = obj
        for k, v in list(d.items()):
            if k == "collection_id" and isinstance(v, str):
                d[k] = to_internal_collection_id(v)
            else:
                apply_inbound_collection_ids(v)
    elif isinstance(obj, list):
        for item in obj:
            apply_inbound_collection_ids(item)
    return obj


def apply_outbound_collection_ids(obj: Any) -> Any:
    """Recursively strip ``core_rag_`` from ``collection_id`` string values in responses."""
    if isinstance(obj, dict):
        d: MutableMapping[str, Any] = obj
        for k, v in list(d.items()):
            if k == "collection_id" and isinstance(v, str):
                d[k] = to_external_collection_id(v)
            else:
                apply_outbound_collection_ids(v)
    elif isinstance(obj, list):
        for item in obj:
            apply_outbound_collection_ids(item)
    return obj


def resolve_graph_json_path(base_graph_dir: str, collection_id: str) -> str | None:
    """Return path to an existing graph JSON file (internal name first, then legacy unprefixed)."""
    primary = os.path.join(base_graph_dir, f"{collection_id}.json")
    if os.path.exists(primary):
        return primary
    external = to_external_collection_id(collection_id)
    if external == collection_id:
        return None
    legacy = os.path.join(base_graph_dir, f"{external}.json")
    if os.path.exists(legacy):
        return legacy
    return None


def resolve_community_reports_json_path(base_graph_dir: str, collection_id: str) -> str | None:
    """Return path to existing community reports JSON (internal then legacy unprefixed)."""
    primary = os.path.join(base_graph_dir, f"{collection_id}_community_reports.json")
    if os.path.exists(primary):
        return primary
    external = to_external_collection_id(collection_id)
    if external == collection_id:
        return None
    legacy = os.path.join(base_graph_dir, f"{external}_community_reports.json")
    if os.path.exists(legacy):
        return legacy
    return None
