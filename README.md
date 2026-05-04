# core_rag_graph

Graph-enhanced RAG FastAPI service based on [arXiv:2603.25152v1](https://arxiv.org/html/2603.25152v1).

## Quickstart

### 1) Install dependencies

```bash
uv sync
```

### 2) Configure environment

```bash
cp .env.example .env
```

Edit `.env` for your backend:

- `GRAPH_BACKEND=networkx` (default) or `GRAPH_BACKEND=neo4j`
- Doc processing LLM gateway: `DOC_PROCESSING_BASE_URL`
- Optional LLM config path override: `LLM_CONFIG_PATH`

### 3) Neo4j quickstart (optional)

Run Neo4j locally:

```bash
docker run --name core-rag-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/changeme -d neo4j:5
```

Set env:

```bash
GRAPH_BACKEND=neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme
NEO4J_DATABASE=neo4j
```

### 4) Run server

```bash
uv run uvicorn graph_server:app --host 0.0.0.0 --port 20050
```

### 5) LLM routing config

All LLM calls from this service are routed to doc_processing `/llm/complete`.
Use `config/llm_config.yaml` to set provider/model per use case:

- `construction_extraction`
- `entity_resolution`
- `community_attribute_report`
- `community_cluster_report`
- `query_answering`

## API highlights

- `POST /api/ingest_chunks` - ingest normalized chunk payloads and build/merge graph
- `POST /api/get_kb_graph_data` - get graph data for a collection
- `POST /api/retrieve` - retrieve ranked graph evidence for a collection query
- `POST /api/query` - generate an answer using collection-scoped graph evidence
- `POST /api/generate_community_reports` - async report generation
- `POST /api/get_community_reports` - fetch generated reports
- `POST /api/delete_file` - remove document contributions from a collection
- `POST /api/delete_collection` - delete collection graph
- `GET /api/collections` - list collections
- `GET /api/collections/{collection_id}` - get collection metadata
- `POST /api/collections/get-or-create` - upsert collection metadata
- `GET /api/metrics` - service/runtime metrics

All collection-scoped APIs require `collection_id`.

Clients send and receive unprefixed `collection_id` values; internally the service uses `core_rag_<collection_id>` for storage and routing, with the prefix applied and stripped automatically at the HTTP boundary.

## P5 Operations

- Migration runbook: `docs/runbook-migration-cutover.md`
- Rollback plan: `docs/rollback-plan.md`
- Cutover checklist: `docs/cutover-checklist.md`

## OpenAPI Automation

- Regenerate spec:
  - `uv run python scripts/openapi_spec.py`
- CI check (fails if stale):
  - `uv run python scripts/openapi_spec.py --check`

`fastapi.testclient.TestClient` needs a compatible **httpx** release (below 0.28) with FastAPI 0.104 / Starlette 0.27; that range is pinned under `[tool.uv]` dev-dependencies.