# Cutover Checklist

- [ ] Neo4j credentials configured and connectivity verified.
- [ ] Migration completed for all target collections.
- [ ] Parity check script passed for all migrated collections.
- [ ] Dual-write burn-in completed without consistency mismatches.
- [ ] Metrics baseline captured pre-cutover.
- [ ] On-call ownership assigned for cutover window.
- [ ] Rollback plan reviewed and approved.
- [ ] Primary switched to Neo4j.
- [ ] Smoke tests passed after switch.
- [ ] Dual-write disabled after stability window.

