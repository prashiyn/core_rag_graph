import os
import sys
import json
import asyncio
import copy
from typing import List, Dict, Optional, Any
import time
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

# FastAPI imports
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from graph.utils.collection_id_middleware import CollectionIdMiddleware
from graph.utils.collection_id_scope import to_external_collection_id, to_internal_collection_id
from graph.utils.logger import logger
from graph.utils import kt_gen as constructor
from graph.utils import call_llm_api
from graph.config import get_config, prompt_templates
from graph.utils import graph_processor
from graph.utils.graph_repository import (
    NetworkXJsonGraphRepository,
    Neo4jGraphRepository,
    DualWriteGraphRepository,
    GraphRepository,
)
from logging_config import init_logging

app = FastAPI(title="graph Unified Interface", version="1.0.0")
init_logging()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CollectionIdMiddleware)

# Global variables
active_connections: Dict[str, WebSocket] = {}

CONFIG = get_config()
RUNTIME_METRICS = {
    "ingest_latency_ms_total": 0.0,
    "ingest_requests_total": 0,
    "query_latency_ms_total": 0.0,
    "query_requests_total": 0,
}


def _create_graph_repository() -> GraphRepository:
    def _build_single_backend(name: str) -> GraphRepository:
        backend = name.strip().lower()
        if backend == "neo4j":
            uri = os.getenv("NEO4J_URI", "").strip()
            user = os.getenv("NEO4J_USER", "").strip()
            password = os.getenv("NEO4J_PASSWORD", "").strip()
            database = os.getenv("NEO4J_DATABASE", "neo4j").strip()
            if not uri or not user or not password:
                raise RuntimeError(
                    "neo4j backend requires NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD"
                )
            return Neo4jGraphRepository(uri=uri, user=user, password=password, database=database)
        if backend == "networkx":
            return NetworkXJsonGraphRepository()
        raise RuntimeError(f"Unsupported GRAPH_BACKEND: {name}")

    primary_name = os.getenv("GRAPH_BACKEND", "networkx")
    primary = _build_single_backend(primary_name)

    dual_write = os.getenv("GRAPH_DUAL_WRITE", "false").strip().lower() in ("1", "true", "yes")
    if not dual_write:
        logger.info(f"Graph repository backend: {primary_name.strip().lower()}")
        return primary

    secondary_name = os.getenv("GRAPH_SECONDARY_BACKEND", "networkx").strip().lower()
    if secondary_name == primary_name.strip().lower():
        raise RuntimeError("GRAPH_SECONDARY_BACKEND must differ from GRAPH_BACKEND when GRAPH_DUAL_WRITE=true")
    secondary = _build_single_backend(secondary_name)
    strict = os.getenv("GRAPH_DUAL_WRITE_STRICT", "true").strip().lower() in ("1", "true", "yes")
    logger.info(
        "Graph repository dual-write enabled: "
        f"primary={primary_name.strip().lower()}, secondary={secondary_name}, strict={strict}"
    )
    return DualWriteGraphRepository(primary=primary, secondary=secondary, check_consistency=strict)


GRAPH_REPOSITORY: GraphRepository = _create_graph_repository()



class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]

    async def send_message(self, message: dict, client_id: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error sending message to {client_id}: {e}")
                self.disconnect(client_id)


manager = ConnectionManager()


# Request/Response models
class ExtracGraphDataResponse(BaseModel):
    """Fields for res_data-style API responses."""
    success: bool
    message: str
    graph_chunks: List[Dict] = []
    graph_vocabulary_set: set = set()
    community_reports: List[Dict] = []


# Request/Response models
class CommunityReportsResponse(BaseModel):
    """Fields for res_data-style API responses."""
    success: bool
    message: str
    community_reports: List[Dict] = []

# Request/Response models
class RequestResponse(BaseModel):
    """Fields for res_data-style API responses."""
    success: bool
    message: str


class ChunkInput(BaseModel):
    chunk_id: str = Field(..., description="Stable chunk identifier")
    content: str = Field(..., description="Chunk text, table, or image description")
    type: str = Field(..., description="One of text, table, image")
    doc_id: str = Field(..., description="Document id")
    page: Optional[int] = Field(None, description="Source page when available")
    bundle_id: str = Field(..., description="Semantic bundle id")
    section_title: Optional[str] = Field(None, description="Nearest section heading")
    title_summary: str = Field("", description="LLM section summary")
    publish_date: Optional[str] = Field(None, description="Document publish date if provided")
    prev_chunk: Optional[str] = Field(None, description="Previous chunk id")
    next_chunk: Optional[str] = Field(None, description="Next chunk id")


class CollectionScopedRequest(BaseModel):
    collection_id: str
    client_id: str = "default"


class IngestChunksRequest(CollectionScopedRequest):
    chunks: List[ChunkInput]
    file_name: str
    temperature: float = 0.001
    schema: Optional[Dict[str, Any]] = None


class CommunityReportsRequest(CollectionScopedRequest):
    pass


class DeleteFileRequest(CollectionScopedRequest):
    file_name: str


class GetGraphRequest(CollectionScopedRequest):
    kb_id: Optional[str] = None


class RetrieveRequest(CollectionScopedRequest):
    query: str
    top_k: int = 10


class QueryRequest(CollectionScopedRequest):
    question: str
    top_k: int = 10
    temperature: float = 0.001


class TestPostRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


class CollectionMetadata(BaseModel):
    collection_id: str
    name: str
    description: str = ""
    created_at: str
    updated_at: str


class CollectionResponse(BaseModel):
    success: bool
    message: str
    collection: CollectionMetadata


class CollectionListResponse(BaseModel):
    success: bool
    message: str
    collections: List[CollectionMetadata]


class GetOrCreateCollectionRequest(BaseModel):
    collection_id: str
    name: Optional[str] = None
    description: str = ""


class GetCollectionByIdRequest(BaseModel):
    collection_id: str


COLLECTIONS_REGISTRY_PATH = "./data/collections/collections.json"


def _ensure_collections_registry() -> None:
    os.makedirs(os.path.dirname(COLLECTIONS_REGISTRY_PATH), exist_ok=True)
    if not os.path.exists(COLLECTIONS_REGISTRY_PATH):
        with open(COLLECTIONS_REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


def _load_collections_registry() -> List[Dict[str, Any]]:
    _ensure_collections_registry()
    with open(COLLECTIONS_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("collection_id"), str):
            row = dict(item)
            row["collection_id"] = to_internal_collection_id(row["collection_id"])
            normalized.append(row)
        elif isinstance(item, dict):
            normalized.append(item)
    return normalized


def _save_collections_registry(collections: List[Dict[str, Any]]) -> None:
    _ensure_collections_registry()
    with open(COLLECTIONS_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(collections, f, ensure_ascii=False, indent=2)


def _normalize_chunk(chunk: Dict[str, Any], file_name: str) -> Dict[str, Any]:
    """Normalize incoming chunk payload to internal structure.

    Canonical external schema:
    - chunk_id, content, type, doc_id, page, bundle_id, section_title, title_summary,
      publish_date, prev_chunk, next_chunk

    Internal fields used by existing graph pipeline:
    - section_title, content, meta_data
    """
    if not isinstance(chunk, dict):
        raise ValueError("Each chunk must be a JSON object")

    chunk_type = str(chunk.get("type", "text")).lower()
    content = chunk.get("content", "")
    title_summary = chunk.get("title_summary", "") or ""

    # For image chunks, use title_summary as textual representation.
    if chunk_type == "image":
        content = title_summary

    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)

    section_title = chunk.get("section_title") or file_name.split(".")[0]
    if not isinstance(section_title, str):
        section_title = str(section_title)

    normalized = {
        "section_title": section_title,
        "content": content,
        "meta_data": {
            "chunk_id": chunk.get("chunk_id"),
            "type": chunk_type,
            "doc_id": chunk.get("doc_id"),
            "page": chunk.get("page"),
            "bundle_id": chunk.get("bundle_id"),
            "section_title": chunk.get("section_title"),
            "title_summary": title_summary,
            "publish_date": chunk.get("publish_date"),
            "prev_chunk": chunk.get("prev_chunk"),
            "next_chunk": chunk.get("next_chunk"),
        },
    }
    return normalized


def _build_graph_from_chunks(payload: IngestChunksRequest) -> ExtracGraphDataResponse:
    """Shared ingestion logic for chunk payloads."""
    ingest_start = time.perf_counter()
    chunks = payload.chunks
    collection_id = _resolve_collection_id(payload.collection_id)
    file_name = payload.file_name
    temperature = payload.temperature
    schema = payload.schema

    normalized_chunks = [_normalize_chunk(chunk.model_dump(), file_name) for chunk in chunks]

    config = get_config()
    config.construction.mode = "general"  # "agent"
    dataset = "demo"
    dataset_config = config.get_dataset_config(dataset)
    dataset_config.corpus_path = "data/demo/custom_corpus.json"
    dataset_config.schema_path = "schemas/custom.json"
    dataset_config.graph_output = "output/graphs/custom_new.json"
    if schema:
        config.prompts["construction"]["general"] = prompt_templates.construction_prompt_with_schema
        config.prompts["construction"]["general_eng"] = prompt_templates.construction_prompt_with_schema_eng
    else:  # No schema: use generic templates
        config.prompts["construction"]["general"] = prompt_templates.construction_prompt_flexible
        config.prompts["construction"]["general_eng"] = prompt_templates.construction_prompt_flexible_eng
    config.construction.TEMPERATURE = temperature
    embedding_model = None
    builder = constructor.KTBuilder(
        dataset,
        embedding_model,
        dataset_config.schema_path,
        schema=schema,
        mode=config.construction.mode,
        config=config
    )
    res_data = builder.build_knowledge_graph(file_name, normalized_chunks)

    # =========== update graph ============
    GRAPH_REPOSITORY.merge_relationships(collection_id, file_name, res_data, config)

    # =========== build graph_vocabulary_set ============
    graph_vocabulary_set = set()
    for node in builder.graph.nodes:
        node_json = builder.graph.nodes[node]
        if node_json['properties'].get('schema_type'):
            schema_type = f"K:{node_json['properties'].get('schema_type')}"
        else:
            schema_type = "K:graph_node"
        node_msg = f"{node_json['properties']['name']}|||schema_type:{schema_type}"
        graph_vocabulary_set.add(node_msg)

    # =========== build graph_chunks ============
    graph_chunks = []
    for triple in res_data:
        reference_chunk_id = triple["start_node"]["properties"]["chunk id"]
        meta_data = builder.all_chunks[reference_chunk_id].get("meta_data", {})
        meta_data["reference_content"] = builder.all_chunks[reference_chunk_id].get("content", "")
        temp_triple = copy.deepcopy(triple)
        if 'chunk id' in temp_triple['start_node']['properties']:
            del temp_triple['start_node']['properties']['chunk id']
        if 'chunk id' in temp_triple['end_node']['properties']:
            del temp_triple['end_node']['properties']['chunk id']
        graph_data_text = (
            f"{triple['start_node']['properties']['name']} "
            f"{triple['relation']} "
            f"{triple['end_node']['properties']['name']}"
        )
        graph_chunks.append(
            {
                "chunk_type": "graph",
                "graph_data_text": graph_data_text,
                "graph_data": copy.deepcopy(temp_triple),
                "meta_data": meta_data,
            }
        )

    response = ExtracGraphDataResponse(
        success=True,
        message="Chunks ingested successfully",
        graph_chunks=graph_chunks,
        graph_vocabulary_set=graph_vocabulary_set,
    )
    elapsed_ms = (time.perf_counter() - ingest_start) * 1000.0
    RUNTIME_METRICS["ingest_latency_ms_total"] += elapsed_ms
    RUNTIME_METRICS["ingest_requests_total"] += 1
    return response


def _resolve_collection_id(collection_id: str) -> str:
    """Validate and normalize collection identifier to internal storage form."""
    if collection_id is None or str(collection_id).strip() == "":
        raise ValueError("collection_id is required")
    return to_internal_collection_id(str(collection_id).strip())


def _query_terms(text: str) -> set:
    return {t.lower() for t in str(text).split() if t.strip()}


def _rank_graph_evidence(graph, query: str, top_k: int) -> List[Dict[str, Any]]:
    terms = _query_terms(query)
    evidence: List[Dict[str, Any]] = []
    if not terms:
        return evidence

    for u, v, data in graph.edges(data=True):
        u_data = graph.nodes[u]
        v_data = graph.nodes[v]
        relation = str(data.get("relation", ""))
        text = f"{u} {relation} {v}"
        score = 0.0
        lower_text = text.lower()
        for t in terms:
            if t in lower_text:
                score += 1.0
        score += float(u_data.get("pagerank", 0.0)) + float(v_data.get("pagerank", 0.0))
        if score <= 0:
            continue
        evidence.append(
            {
                "type": "edge",
                "score": round(score, 6),
                "text": text,
                "source": u,
                "target": v,
                "relation": relation,
                "source_schema_type": u_data.get("properties", {}).get("schema_type", ""),
                "target_schema_type": v_data.get("properties", {}).get("schema_type", ""),
                "source_files": u_data.get("properties", {}).get("file_names", []),
                "target_files": v_data.get("properties", {}).get("file_names", []),
            }
        )

    evidence.sort(key=lambda x: x["score"], reverse=True)
    return evidence[: max(1, top_k)]


def _build_chunk_evidence(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    chunk_map: Dict[str, Dict[str, Any]] = {}
    for item in evidence:
        relation_text = item.get("text", "")
        for side in ("source", "target"):
            file_key = f"{side}_files"
            entity_name = str(item.get(side, ""))
            for file_name in item.get(file_key, []) or []:
                chunk_key = f"{file_name}::{entity_name}"
                if chunk_key not in chunk_map:
                    chunk_map[chunk_key] = {
                        "chunk_id": chunk_key,
                        "doc_id": file_name,
                        "entity": entity_name,
                        "evidence_texts": [],
                    }
                if relation_text and relation_text not in chunk_map[chunk_key]["evidence_texts"]:
                    chunk_map[chunk_key]["evidence_texts"].append(relation_text)

    chunk_evidence = list(chunk_map.values())
    chunk_evidence.sort(key=lambda x: len(x["evidence_texts"]), reverse=True)
    return chunk_evidence


def _build_context_from_evidence(evidence: List[Dict[str, Any]]) -> str:
    if not evidence:
        return ""
    lines = []
    for idx, item in enumerate(evidence, 1):
        lines.append(f"{idx}. {item['text']}")
    return "\n".join(lines)


async def send_progress_update(client_id: str, stage: str, progress: int, message: str):
    """Send progress update via WebSocket"""
    await manager.send_message({
        "type": "progress",
        "stage": stage,
        "progress": progress,
        "message": message,
        "timestamp": datetime.now().isoformat()
    }, client_id)

async def send_community_reports(client_id: str, reports: List[Dict], message: str = "community_reports ready"):
    await manager.send_message({
        "type": "community_reports",
        "data": reports,
        "message": message,
        "timestamp": datetime.now().isoformat()
    }, client_id)

async def _generate_community_reports_task(collection_id: str, config, client_id: str):
    try:
        await send_progress_update(client_id, "generate_community_reports", 1, "started")
        reports: List[Dict] = []
        new_graph = GRAPH_REPOSITORY.load_collection_graph(collection_id)
        if new_graph is not None:
            reports = await asyncio.to_thread(graph_processor.extract_community, new_graph, config)
            GRAPH_REPOSITORY.save_community_reports(collection_id, reports)
            await send_progress_update(client_id, "generate_community_reports", 90, "reports generated")
            await send_community_reports(client_id, reports, "completed")
            await send_progress_update(client_id, "generate_community_reports", 100, "completed")
        else:
            await send_progress_update(client_id, "generate_community_reports", 0, "graph not found")
            await send_community_reports(client_id, [], "graph not found")
    except Exception as e:
        await send_progress_update(client_id, "generate_community_reports", 0, f"failed: {str(e)}")
        await manager.send_message({
            "type": "community_reports_error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }, client_id)

@app.post("/api/extrac_graph_data", response_model=ExtracGraphDataResponse)
async def extrac_graph_data(payload: IngestChunksRequest):
    """Legacy route: ingest chunk list and build/merge graph."""
    try:
        return _build_graph_from_chunks(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ingest_chunks", response_model=ExtracGraphDataResponse)
async def ingest_chunks(payload: IngestChunksRequest):
    """Canonical chunk ingestion endpoint for Graph RAG construction."""
    try:
        return _build_graph_from_chunks(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/generate_community_reports", response_model=CommunityReportsResponse)
async def generate_community_reports(payload: CommunityReportsRequest):
    """extrac_graph_data endpoint  chunks: List[Dict], client_id: str = 'default' """
    try:
        collection_id = _resolve_collection_id(payload.collection_id)
        client_id = payload.client_id
        logger.info(f"generate_community_reports, collection_id: {collection_id}")
        config = get_config()
        config.construction.mode = "general"  # "agent"

        asyncio.create_task(_generate_community_reports_task(collection_id, config, client_id))

        return CommunityReportsResponse(
            success=True,
            message="generate_community_reports started",
            community_reports=[],
        )

    except Exception as e:
        await send_progress_update(client_id, "generate_community_reports", 0, f"failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/get_community_reports", response_model=CommunityReportsResponse)
async def get_community_reports(payload: CollectionScopedRequest):
    try:
        collection_id = _resolve_collection_id(payload.collection_id)
        client_id = payload.client_id
        reports = GRAPH_REPOSITORY.load_community_reports(collection_id)
        if reports is None:
            return CommunityReportsResponse(
                success=True,
                message="not ready",
                community_reports=[],
            )
        await send_progress_update(client_id, "get_community_reports", 10, "get_community_reports completed successfully!")
        return CommunityReportsResponse(
            success=True,
            message="ok",
            community_reports=reports,
        )
    except Exception as e:
        await send_progress_update(client_id, "get_community_reports", 0, f"failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/delete_file", response_model=ExtracGraphDataResponse)
async def delete_file(payload: DeleteFileRequest):
    """update graph """
    try:
        collection_id = _resolve_collection_id(payload.collection_id)
        file_name = payload.file_name
        client_id = payload.client_id

        # =========== update graph ============
        GRAPH_REPOSITORY.delete_file(collection_id, file_name)
        await send_progress_update(client_id, "delete_file", 10, "delete_file completed successfully!")

        return RequestResponse(
            success=True,
            message="Files deleted successfully",
        )

    except Exception as e:
        await send_progress_update(client_id, "delete_file", 0, f"deleted failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/delete_collection", response_model=ExtracGraphDataResponse)
async def delete_collection(payload: CollectionScopedRequest):
    """delete collection graph"""
    try:
        collection_id = _resolve_collection_id(payload.collection_id)
        client_id = payload.client_id

        # =========== update graph ============
        GRAPH_REPOSITORY.delete_collection(collection_id)
        collections = _load_collections_registry()
        collections = [c for c in collections if c.get("collection_id") != collection_id]
        _save_collections_registry(collections)
        await send_progress_update(client_id, "delete_collection", 10, "delete_collection completed successfully!")

        return RequestResponse(
            success=True,
            message="delete_collection successfully",
        )

    except Exception as e:
        await send_progress_update(client_id, "delete_collection", 0, f"deleted failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/collections/{collection_id}", response_model=RequestResponse)
async def delete_collection_rest(collection_id: str):
    """Canonical REST endpoint: delete collection by id."""
    return await delete_collection(CollectionScopedRequest(collection_id=collection_id, client_id="default"))


@app.post("/api/delete_kb", response_model=ExtracGraphDataResponse)
async def delete_kb(payload: CollectionScopedRequest):
    """Legacy alias for delete_collection."""
    return await delete_collection(payload)


@app.post("/api/test_post")
async def test_post(payload: TestPostRequest):
    """test post"""
    time.sleep(6)
    return {
        "code": 0,
        "success": True,
        "message": f"test: {payload.payload} successfully",
    }

class GraphDataResponse(BaseModel):
    """Fields for res_data-style API responses."""
    success: bool
    message: str
    graph_data: dict


class RetrieveResponse(BaseModel):
    success: bool
    message: str
    query: str
    collection_id: str
    evidence: List[Dict]
    chunk_evidence: List[Dict] = []


class QueryResponse(BaseModel):
    success: bool
    message: str
    question: str
    collection_id: str
    answer: str
    evidence: List[Dict]
    chunk_evidence: List[Dict] = []

@app.post("/api/get_kb_graph_data", response_model=GraphDataResponse)
async def get_kb_graph_data(payload: GetGraphRequest):
    """get graph data"""
    query_start = time.perf_counter()
    try:
        collection_id = _resolve_collection_id(payload.collection_id)
        client_id = payload.client_id

        graph_data = {
            "graph": {
                "directed": False,
                "multigraph": False,
                "nodes": [],
                "edges": []
            }
        }

        graph = GRAPH_REPOSITORY.load_collection_graph(collection_id)
        if graph is not None:
            for node in graph.nodes(data=True):
                if node[1]["label"] == "entity":
                    graph_data["graph"]["nodes"].append({
                        "entity_name": node[0],
                        "entity_type": node[1]["label"],
                        "description": "",
                        "source_id": node[1]["properties"]["file_names"]
                    })
            for edge in graph.edges(data=True):
                if edge[2]["relation"] != "has_attribute":
                    graph_data["graph"]["edges"].append({
                        "source_entity": edge[0],
                        "target_entity": edge[1],
                        "description": edge[2]["relation"],
                        "weight": 1.0,
                    })

        await send_progress_update(client_id, "get_kb_graph_data", 10, "get_kb_graph_data completed successfully!")

        response = GraphDataResponse(
            success=True,
            message="get_kb_graph_data successfully",
            graph_data=graph_data
        )
        elapsed_ms = (time.perf_counter() - query_start) * 1000.0
        RUNTIME_METRICS["query_latency_ms_total"] += elapsed_ms
        RUNTIME_METRICS["query_requests_total"] += 1
        return response

    except Exception as e:
        await send_progress_update(client_id, "get_kb_graph_data", 0, f"deleted failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/retrieve", response_model=RetrieveResponse)
async def retrieve(payload: RetrieveRequest):
    query_start = time.perf_counter()
    try:
        collection_id = _resolve_collection_id(payload.collection_id)
        graph = GRAPH_REPOSITORY.load_collection_graph(collection_id)
        if graph is None:
            return RetrieveResponse(
                success=True,
                message="collection graph not found",
                query=payload.query,
                collection_id=collection_id,
                evidence=[],
                chunk_evidence=[],
            )

        evidence = _rank_graph_evidence(graph, payload.query, payload.top_k)
        chunk_evidence = _build_chunk_evidence(evidence)
        elapsed_ms = (time.perf_counter() - query_start) * 1000.0
        RUNTIME_METRICS["query_latency_ms_total"] += elapsed_ms
        RUNTIME_METRICS["query_requests_total"] += 1

        return RetrieveResponse(
            success=True,
            message="retrieve success",
            query=payload.query,
            collection_id=collection_id,
            evidence=evidence,
            chunk_evidence=chunk_evidence,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/query", response_model=QueryResponse)
async def query(payload: QueryRequest):
    query_start = time.perf_counter()
    try:
        collection_id = _resolve_collection_id(payload.collection_id)
        graph = GRAPH_REPOSITORY.load_collection_graph(collection_id)
        if graph is None:
            return QueryResponse(
                success=True,
                message="collection graph not found",
                question=payload.question,
                collection_id=collection_id,
                answer="No graph found for this collection.",
                evidence=[],
                chunk_evidence=[],
            )

        evidence = _rank_graph_evidence(graph, payload.question, payload.top_k)
        chunk_evidence = _build_chunk_evidence(evidence)
        context = _build_context_from_evidence(evidence)

        config = get_config()
        prompt = config.get_prompt_formatted(
            "retrieval",
            "general",
            question=payload.question,
            context=context,
        )
        llm = call_llm_api.LLMCompletionCall(
            temperature=payload.temperature,
            use_case="query_answering",
        )
        answer = llm.call_api(prompt).strip()
        if not answer:
            answer = "No answer generated."

        elapsed_ms = (time.perf_counter() - query_start) * 1000.0
        RUNTIME_METRICS["query_latency_ms_total"] += elapsed_ms
        RUNTIME_METRICS["query_requests_total"] += 1

        return QueryResponse(
            success=True,
            message="query success",
            question=payload.question,
            collection_id=collection_id,
            answer=answer,
            evidence=evidence,
            chunk_evidence=chunk_evidence,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/get-or-create-collection", response_model=CollectionResponse)
async def get_or_create_collection(payload: GetOrCreateCollectionRequest):
    try:
        collection_id = payload.collection_id.strip()
        if not collection_id:
            raise HTTPException(status_code=400, detail="collection_id cannot be empty")
        now = datetime.now().isoformat()
        collections = _load_collections_registry()
        existing = next((c for c in collections if c.get("collection_id") == collection_id), None)
        if existing:
            existing["updated_at"] = now
            if payload.name:
                existing["name"] = payload.name
            if payload.description is not None:
                existing["description"] = payload.description
            _save_collections_registry(collections)
            return CollectionResponse(
                success=True,
                message="collection fetched",
                collection=CollectionMetadata(**existing),
            )

        new_obj = {
            "collection_id": collection_id,
            "name": payload.name or to_external_collection_id(collection_id),
            "description": payload.description or "",
            "created_at": now,
            "updated_at": now,
        }
        collections.append(new_obj)
        _save_collections_registry(collections)
        return CollectionResponse(
            success=True,
            message="collection created",
            collection=CollectionMetadata(**new_obj),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/get-collection-metadata-by-collection-id", response_model=CollectionResponse)
async def get_collection_metadata_by_collection_id(payload: GetCollectionByIdRequest):
    try:
        collection_id = payload.collection_id.strip()
        collections = _load_collections_registry()
        existing = next((c for c in collections if c.get("collection_id") == collection_id), None)
        if not existing:
            raise HTTPException(status_code=404, detail="collection not found")
        return CollectionResponse(
            success=True,
            message="collection fetched",
            collection=CollectionMetadata(**existing),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/get-all-collections", response_model=CollectionListResponse)
async def get_all_colecctions():
    try:
        collections = _load_collections_registry()
        parsed = [CollectionMetadata(**c) for c in collections]
        return CollectionListResponse(
            success=True,
            message="collections fetched",
            collections=parsed,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/collections", response_model=CollectionListResponse)
async def get_all_collections():
    """Canonical endpoint: list all collections."""
    return await get_all_colecctions()


@app.get("/api/collections/{collection_id}", response_model=CollectionResponse)
async def get_collection_metadata(collection_id: str):
    """Canonical endpoint: get collection metadata by id."""
    return await get_collection_metadata_by_collection_id(
        GetCollectionByIdRequest(collection_id=collection_id)
    )


@app.post("/api/collections/get-or-create", response_model=CollectionResponse)
async def get_or_create_collection_canonical(payload: GetOrCreateCollectionRequest):
    """Canonical endpoint: get existing collection or create it."""
    return await get_or_create_collection(payload)


@app.post("/api/collections", response_model=CollectionResponse)
async def create_collection_canonical(payload: GetOrCreateCollectionRequest):
    """Canonical REST endpoint: create collection metadata."""
    return await get_or_create_collection(payload)


@app.get("/api/metrics")
async def get_metrics():
    ingest_avg = (
        RUNTIME_METRICS["ingest_latency_ms_total"] / RUNTIME_METRICS["ingest_requests_total"]
        if RUNTIME_METRICS["ingest_requests_total"] > 0
        else 0.0
    )
    query_avg = (
        RUNTIME_METRICS["query_latency_ms_total"] / RUNTIME_METRICS["query_requests_total"]
        if RUNTIME_METRICS["query_requests_total"] > 0
        else 0.0
    )
    processor_metrics = graph_processor.get_operational_metrics()
    return {
        "ingestion_latency_ms_avg": round(ingest_avg, 3),
        "ingestion_requests_total": RUNTIME_METRICS["ingest_requests_total"],
        "query_latency_ms_avg": round(query_avg, 3),
        "query_requests_total": RUNTIME_METRICS["query_requests_total"],
        "merge_conflicts_total": processor_metrics.get("merge_conflicts_total", 0),
        "failed_resolutions_total": processor_metrics.get("failed_resolutions_total", 0),
    }


@app.post("/api/getOrCreateCollection", response_model=CollectionResponse)
async def get_or_create_collection_legacy(payload: GetOrCreateCollectionRequest):
    """Legacy alias for get-or-create-collection."""
    return await get_or_create_collection(payload)


@app.post("/api/getCollectionMetadataByCollectionId", response_model=CollectionResponse)
async def get_collection_metadata_by_collection_id_legacy(payload: GetCollectionByIdRequest):
    """Legacy alias for get-collection-metadata-by-collection-id."""
    return await get_collection_metadata_by_collection_id(payload)


@app.get("/api/getAllColecctions", response_model=CollectionListResponse)
async def get_all_colecctions_legacy():
    """Legacy alias for get-all-collections."""
    return await get_all_colecctions()


@app.get("/api/test")
async def test():
    """test"""
    return {
        "code": 0,
        "success": True,
        "message": f"test successfully",
    }


@app.on_event("shutdown")
async def shutdown_event():
    close_fn = getattr(GRAPH_REPOSITORY, "close", None)
    if callable(close_fn):
        close_fn()
        logger.info("Graph repository closed")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=20050)
