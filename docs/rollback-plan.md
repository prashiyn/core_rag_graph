# Rollback Plan

## Trigger conditions

Rollback immediately if any of the following occur:

- Repeated dual-write consistency failures.
- Elevated `failed_resolutions_total` correlated with ingestion failures.
- Read/query regressions after cutover.
- Neo4j availability/performance incidents.

## Rollback steps

1. Set environment:
   - `GRAPH_BACKEND=networkx`
   - `GRAPH_DUAL_WRITE=false`
2. Restart server.
3. Validate:
   - `GET /api/test`
   - `GET /api/metrics`
   - `POST /api/get_kb_graph_data` for affected collections
4. If data drift is suspected, re-run migration later after issue resolution:
   - `scripts/migrate_json_to_neo4j.py`
   - `scripts/check_backend_parity.py`

## Post-rollback checklist

- Incident timeline captured.
- Root-cause investigation created.
- Failed operations replay plan documented.
- Cutover window re-scheduled only after parity passes.

