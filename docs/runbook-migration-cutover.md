# Migration and Cutover Runbook

## Preconditions

- Neo4j is running and reachable.
- Environment variables are set:
  - `NEO4J_URI`
  - `NEO4J_USER`
  - `NEO4J_PASSWORD`
  - `NEO4J_DATABASE` (optional, defaults to `neo4j`)
- Existing JSON collection graphs are present in `./data/graph/{collection_id}.json`.

## 1) Baseline checks

1. Start server in current mode:
   - `GRAPH_BACKEND=networkx`
2. Verify health endpoints:
   - `GET /api/test`
3. Capture baseline metrics:
   - `GET /api/metrics`

## 2) One-time migration

Run migration:

```bash
uv run python scripts/migrate_json_to_neo4j.py --base-graph-dir ./data/graph --drop-target-first
```

Optional targeted migration:

```bash
uv run python scripts/migrate_json_to_neo4j.py --collections collection_a collection_b
```

## 3) Parity validation

Compare node/edge counts for migrated collections:

```bash
uv run python scripts/check_backend_parity.py --collections collection_a collection_b
```

Expected: all collections print `OK`.

## 4) Dual-write burn-in

Enable dual-write mode:

- `GRAPH_BACKEND=networkx`
- `GRAPH_DUAL_WRITE=true`
- `GRAPH_SECONDARY_BACKEND=neo4j`
- `GRAPH_DUAL_WRITE_STRICT=true`

Run representative ingest/delete/report workflows and watch:

- Server logs for dual-write mismatches
- `GET /api/metrics`:
  - `ingestion_latency_ms_avg`
  - `query_latency_ms_avg`
  - `merge_conflicts_total`
  - `failed_resolutions_total`

## 5) Cutover

Switch primary backend:

- `GRAPH_BACKEND=neo4j`
- `GRAPH_DUAL_WRITE=true`
- `GRAPH_SECONDARY_BACKEND=networkx`
- `GRAPH_DUAL_WRITE_STRICT=true`

Run smoke tests:

- ingest chunks (`/api/ingest_chunks`)
- graph fetch (`/api/get_kb_graph_data`)
- community report generation/read

If stable for agreed window, disable dual-write:

- `GRAPH_DUAL_WRITE=false`

