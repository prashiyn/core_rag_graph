---
title: Collection-Aware + Neo4j Migration Plan
status: in_progress
owner: backend-platform
last_updated: 2026-04-30
objective: Introduce first-class collections and migrate graph storage from local JSON/NetworkX to Neo4j with scalable isolation.
machine_readable:
  current_state:
    collection_model: true
    tenant_boundary: ["collection_id"]
    graph_store: "selectable via GRAPH_BACKEND (networkx json or neo4j)"
    graph_runtime: "repository abstraction + backend selection"
    retrieval_query_api: true
  target_state:
    collection_model: true
    api_requires_collection_id: true
    graph_store: "neo4j"
    graph_runtime: "repository abstraction (networkx and neo4j backends)"
  phases:
    - id: P0
      name: "Stabilize contracts and naming"
      effort_weeks: 1
    - id: P1
      name: "Introduce first-class collection domain"
      effort_weeks: 1
    - id: P2
      name: "Storage abstraction layer"
      effort_weeks: 1
    - id: P3
      name: "Neo4j backend (write/read paths)"
      effort_weeks: 2
    - id: P4
      name: "Retrieval/query APIs collection-scoped"
      effort_weeks: 2
    - id: P5
      name: "Migration, hardening, cutover"
      effort_weeks: 1
---

# Assessment Summary

## 1) Does the project have a collection concept today?

Not explicitly.

What existed before refactor was a practical scope key: `user_id + kb_name`.

- Graph write/read/delete paths are scoped by:
  - `./data/graph/{collection_id}.json` (after P0 refactor)
- This appears in:
  - `graph_server.py` request handling
  - `utils/graph_processor.py` (`update_graph`, `delete_file`, `delete_kb`)

This is similar to a collection boundary but is not a first-class `collection` domain model with its own lifecycle and metadata.
User-level partitioning is treated as out-of-scope for this service and should be handled at higher layers.

## 2) Is graph data in a graph server or files?

Today:

- Runtime graph operations: `networkx.MultiDiGraph`
- Persistence: local JSON files on disk
- No external graph server/database in active path

# Current Gaps vs Target

## Functional gaps

1. No explicit `Collection` entity (id, name, owner, lifecycle, status).
2. API contracts do not consistently require `collection_id`.
3. No dedicated retrieval/query endpoint for collection-scoped graph RAG.
4. Tight coupling to file-path graph storage in `graph_processor.py`.

## Scalability/operability gaps

1. Single-host local-file persistence limits horizontal scaling.
2. Concurrency and merge contention are not coordinated across processes.
3. No indexed graph store for large-scale traversal/search.
4. No migration/versioning strategy for graph backend changes.

# Proposed Target Architecture

## Collection model

Introduce first-class collection identity:

- `collection_id` (globally unique stable ID)
- optional metadata: `name`, `owner_id`, `description`, `created_at`, `status`

Every operation becomes collection-scoped:

- Ingest chunks
- Generate community reports
- Graph retrieval/query
- File-level delete in collection
- Collection delete/archive

Note: this service will not enforce per-user partitioning. All isolation in this layer is collection-level.

## Storage abstraction

Create a graph repository interface so app logic stops depending on NetworkX/file paths:

- `GraphRepository` (protocol/interface)
  - `load_collection_graph(collection_id)`
  - `merge_relationships(collection_id, relationships, file_name, ...)`
  - `delete_file(collection_id, file_name)`
  - `delete_collection(collection_id)`
  - `get_collection_graph(collection_id)` (for API response)

Backends:

- `NetworkXJsonRepository` (current behavior, transitional)
- `Neo4jRepository` (target behavior)

## Neo4j model (recommended)

Use collection as graph partition key.

- Nodes:
  - `(:Entity {name, schema_type, collection_id, ...})`
  - `(:Attribute {name, collection_id, ...})`
  - optional `(:Document {...})`, `(:Chunk {...})`
- Relationships:
  - dynamic relation types for extracted predicates (or normalized `:REL {type: ...}`)
  - `(:Entity)-[:HAS_ATTRIBUTE]->(:Attribute)`
  - optional provenance edges (`:MENTIONED_IN`, `:FROM_DOCUMENT`)

Core indexes/constraints:

- Runtime schema setup creates:
  - unique constraint on `GraphNode(collection_id, name, schema_type, label)`
  - index on `GraphNode(collection_id)`
- Optional preflight migration script:
  - `scripts/ensure_neo4j_schema.py`
- `Document(collection_id, doc_id)` unique if document nodes introduced

# Phase-wise Implementation Plan

## P0 — Stabilize Contracts and Naming (Completed)

Scope:

- Keep current behavior, define canonical request contracts.
- Standardize on `collection_id`.

Changes:

- Add request models for ingestion and graph operations.
- Validation rule:
  - require `collection_id` directly.
- Add deprecation warnings in legacy routes.

Deliverables:

- Typed Pydantic models
- Collection-scoped request contract in server

Risks:

- Client breakage if old fields are still used

Mitigation:

- Keep endpoint aliases while enforcing `collection_id`.

---

## P1 — First-Class Collection Domain (Implemented)

Scope:

- Introduce collection lifecycle and metadata.

Changes:

- Add collection service and metadata store (start simple: JSON/SQLite/Postgres).
- New APIs:
  - `POST /api/collections`
  - `GET /api/collections/{collection_id}`
  - `DELETE /api/collections/{collection_id}`
- Update all existing APIs to require `collection_id`.

Deliverables:

- Collection CRUD
- Routing update matrix

Implemented artifacts:

- Collection metadata registry in `./data/collections/collections.json`
- Legacy compatibility APIs:
  - `GET /api/getAllColecctions`
  - `POST /api/getCollectionMetadataByCollectionId`
  - `POST /api/getOrCreateCollection`
- Canonical APIs:
  - `GET /api/collections`
  - `GET /api/collections/{collection_id}`
  - `POST /api/collections/get-or-create`
  - `POST /api/collections`
  - `DELETE /api/collections/{collection_id}`

Risks:

- Legacy client payloads without `collection_id`

Mitigation:

- deterministic mapping function and migration script.

---

## P2 — Graph Storage Abstraction Layer (1 week)

Scope:

- Decouple `graph_processor` from file and NetworkX specifics.

Changes:

- Introduce `GraphRepository` interface.
- Move current file/json logic into `NetworkXJsonRepository`.
- Refactor:
  - `graph_server.py` -> service layer -> repository
  - `graph_processor.py` becomes backend-agnostic orchestration or split.

Deliverables:

- Clean storage abstraction
- Existing behavior unchanged using NetworkX backend

Risks:

- Refactor regressions in merge and resolution flows

Mitigation:

- Golden tests for ingestion -> graph JSON snapshots.

---

## P3 — Neo4j Backend (2 weeks)

Scope:

- Implement `Neo4jRepository` using the official Neo4j Python driver.

Changes:

- Connection/config:
  - `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
- Implement merge/upsert semantics equivalent to current `graph_merge`.
- Keep entity-resolution pipeline; apply resolved names before writes.
- Add indexes/constraints migration script.

Deliverables:

- Working Neo4j read/write graph backend
- Performance baseline report

Risks:

- Semantic mismatch with NetworkX multigraph behavior
- Dynamic relation type handling complexity

Mitigation:

- Define canonical Cypher upsert patterns
- Restrict or normalize relation typing strategy early.

---

## P4 — Collection-Scoped Retrieval/Query APIs (Implemented)

Scope:

- Add missing query/retrieval APIs, explicitly collection-bound.

Suggested APIs:

- `POST /api/query`
  - inputs: `collection_id`, `question`, llm params
  - output: answer, evidence chunks, graph paths/subgraph
- `POST /api/retrieve`
  - inputs: `collection_id`, query
  - output: ranked graph/chunk evidence

Changes:

- Implement retrieval over collection-specific graph.
- Optionally integrate vector store later; keep graph retrieval first.

Deliverables:

- End-to-end collection-scoped ingest + query path

Implemented artifacts:

- `POST /api/retrieve`
- `POST /api/query`
- Retrieval evidence ranking from collection graph edges
- Chunk-level evidence objects in retrieval/query responses
- Answer generation using retrieval prompt template (`retrieval.general`)
- Query latency included in `/api/metrics`

Risks:

- Query quality gaps due to lack of dedicated retrieval ranking

Mitigation:

- Add reranking module and evaluation harness.

---

## P5 — Migration, Hardening, Cutover (Implemented)

Scope:

- Migrate existing JSON graphs to Neo4j.
- Production hardening and cutover controls.

Changes:

- One-time migrator:
  - read `./data/graph/{collection_id}.json`
  - map to `collection_id`
  - write Neo4j
- Dual-write (temporary) and consistency checks.
- Observability:
  - ingestion latency
  - query latency
  - merge conflicts
  - failed resolutions

Implemented artifacts:

- `scripts/migrate_json_to_neo4j.py`
- `scripts/check_backend_parity.py`
- backend controls in `.env.sample`:
  - `GRAPH_DUAL_WRITE`
  - `GRAPH_SECONDARY_BACKEND`
  - `GRAPH_DUAL_WRITE_STRICT`
- metrics endpoint:
  - `GET /api/metrics`
- runbooks:
  - `docs/runbook-migration-cutover.md`
  - `docs/rollback-plan.md`
  - `docs/cutover-checklist.md`

Deliverables:

- Migration runbook
- Rollback plan
- Cutover checklist

# Effort Estimate (Total)

- Engineering: ~8 weeks (single team, sequential)
- With parallelization (2-3 engineers): ~4-5 weeks

Breakdown:

- Collection/domain/API contracts: 2 weeks
- Storage abstraction + Neo4j backend: 3 weeks
- Query/retrieval APIs: 2 weeks
- Migration/hardening: 1 week

# NxNeo4j Assessment

`nxneo4j` is explicitly out of scope for this migration.

Decision:

- Use repository abstraction regardless.
- Implement Neo4j backend with official driver only.
- Write Cypher explicitly for correctness, indexing, and operational control.

# Closure Status

- P0 through P5 are implemented.
- Remaining production work is operational rollout:
  - run migration and parity checks in target environments
  - complete dual-write burn-in, then cut over to Neo4j
  - monitor `/api/metrics` and runbook checkpoints during cutover

# Definition of Done (Final State)

- Every public API accepts and enforces `collection_id`.
- Collection data is mutually isolated by storage and query constraints.
- Graph operations run on Neo4j in production.
- Ingest/query/reports work end-to-end per collection.
- No `user_id`/`kb_name` partitioning in this service layer.

