# Refactor PRD — pg_partman partitioning for retention (P18 / Phase 5)

**Status:** Draft — pre-investigation
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §1.5](../architecture/refactor-audit.md#15-custom-retention-sweep--5-level-policy-resolver)
**Roadmap slot:** [P18](00-roadmap.md#phase-5--major-library-swaps--structural-shifts)
**Pre-requisite:** none (orthogonal to other Phase 5 work)
**Touches compliance:** retention policy is a regulated control — buyer sign-off may be required before merging.

---

## 1. Problem

Retention today is application-layer:

- **`RetentionPolicyResolver`** in [`agent_runtime/retention/policy_resolver.py`](../../src/agent_runtime/retention/policy_resolver.py) walks `CONVERSATION > ASSISTANT > USER > ORG > default` to resolve a `ResolvedPolicy(kind, ttl, scope)`.
- **`RetentionSweeperLoop`** in [`runtime_worker/jobs/retention_sweeper.py`](../../src/runtime_worker/jobs/retention_sweeper.py) iterates rows past TTL and tombstones them (messages, events, payloads, memory items, citations, etc.).
- **Per-table sweep paths** live in the various persistence adapters; each high-volume table has its own delete loop.

This is reinventing TTL on top of a database that supports time-partitioning natively. Symptoms:

- Sweep cost grows linearly with row count. As `runtime_events` and `agent_messages` accumulate (the two highest-volume tables in the system per the diagrams), sweep duration grows; index bloat grows faster.
- `VACUUM` after tombstone-delete does not reclaim disk in observable time on busy tables; large deletes cause replication lag.
- Field-level encryption rotation (whatever `FieldCodec` does today) and retention sweep both want to touch every row — they end up fighting for write bandwidth.
- The 5-level policy hierarchy is real and load-bearing (per [refactor-audit § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved)). It is correct that the _resolution_ logic stays in code. What does not need to stay in code is the _sweep_.

### What this is NOT

- Not removing the `RetentionPolicyResolver`. The 5-level hierarchy is a domain decision; resolvers stay.
- Not changing user-visible deletion semantics. A "delete my data" request must still result in deletion in observable time.
- Not a one-size policy. Different rows in the same table can resolve to different TTLs; this PRD's design must handle that.

---

## 2. Verification required before approval

The audit-chain PRD reversed direction after code-level investigation. Apply the same discipline here. The recommendation may shrink (sweep stays for some tables, partition only the high-volume ones) or expand (entire retention subsystem becomes DB-native) depending on findings.

| Question                                                                                                                                                                       | How to answer                                                                                                                                                   | If answer is X, then PRD shape changes how                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Which tables are actually high-volume in production? Top 5 by row count + write rate?                                                                                          | Query `pg_stat_user_tables` + `pg_stat_io` in staging / a recent prod snapshot.                                                                                 | Partition only those. Low-volume tables keep app-layer sweep; complexity not worth it.                                                                                                  |
| Does any row in a high-volume table carry an **individually-resolved retention TTL** (e.g. one conversation pinned at 7 days while sibling conversations get the org default)? | Read `RetentionPolicyResolver` + grep callers; check whether resolver decision is stamped onto rows or recomputed at sweep time.                                | If individually-resolved: time-partitioning by `created_at` doesn't cleanly map. Solution: partition by `created_at` _and_ nullify rows that need different TTL via app sweep (hybrid). |
| Does any high-volume table need to support **GDPR / right-to-be-forgotten** style targeted deletion of a single user's data across partitions?                                 | Read retention spec + grep for `delete_user_data` / GDPR-shaped API.                                                                                            | If yes: partitioning by `created_at` is fine, but a _separate_ selective-delete path (per-user index + DELETE) is also needed. PRD adds that as a sub-task.                             |
| Does field-level encryption rotate keys per-row in place, or is the rotation an offline migration?                                                                             | Read `FieldCodec` and any rotation job (likely [`runtime_worker/jobs/encrypt_existing_columns.py`](../../src/runtime_worker/jobs/encrypt_existing_columns.py)). | If in-place: ensure rotation can run against partitioned tables. If offline: orthogonal.                                                                                                |
| Does `pg_partman` need to be a managed extension on the production database (RDS/Aurora/Cloud SQL/self-managed)? Is it currently available?                                    | Check infra README + `\dx` on the production database.                                                                                                          | If not available: open a separate platform PR to install it; this PRD blocks on that.                                                                                                   |
| What is the actual retention policy for `runtime_events`? `agent_messages`? `runtime_run_usage`? `runtime_model_call_usage`?                                                   | Read the spec + grep `default_retention_days` resolution.                                                                                                       | Determines partition interval. Daily for events, monthly for usage rollups, etc.                                                                                                        |
| Are there tests today that pin the user-visible deletion contract (delete arrives in observable time)?                                                                         | Grep for retention sweep tests.                                                                                                                                 | If yes: keep them green. If no: write them in this PRD's test plan before any cutover.                                                                                                  |

---

## 3. Goal and non-goals

### Goal

For each high-volume table, replace app-layer tombstone-and-vacuum with **`pg_partman`-managed time-partitioning by `created_at`**. Use `DROP PARTITION` to reclaim disk in milliseconds. Keep `RetentionPolicyResolver` and the resolver-driven app sweep for tables where partitioning doesn't fit (typically low-volume or fine-grained-TTL tables).

### Non-goals

- **Not** changing the 5-level policy hierarchy.
- **Not** removing `RetentionSweeperLoop` entirely — it stays for tables that can't be cleanly partitioned (e.g. memory items keyed by scope, audit log, anything with row-level varying TTL).
- **Not** changing user-visible deletion semantics. GDPR / user-targeted delete remains a separate, prompt path.
- **Not** introducing a different partitioning strategy than time (hash / list partitioning are out of scope here).

### Success criteria

- Identified high-volume tables (post-verification) converted to native partitioned tables with `pg_partman` managing partition creation + drop.
- For each partitioned table: a documented partition interval (daily / weekly / monthly), a documented retention window per scope, and a documented partition-creation lead time.
- `RetentionSweeperLoop` no longer touches partitioned tables; still runs against unpartitioned ones with the same `RetentionPolicyResolver` source of truth.
- `DROP PARTITION` events emit a `RetentionDropEvent` to the audit log (for compliance traceability — the operation is observable).
- A GDPR / user-targeted delete path exists (see §2) that does a per-user index lookup + targeted DELETE across partitions and continues to work after partition drops.
- Latency benchmark: sweep p99 reduced by an order of magnitude; replication lag during retention operations effectively zero.
- Re-encryption migrations (when they run) work against partitioned tables without rewriting historical partitions.

---

## 4. Systems touched

**Pending verification.** Likely scope below.

### 4.1 Database changes

| Object                     | Change                                                                                                                       |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `runtime_events`           | Convert to partitioned by `created_at`, interval = daily. pg_partman maintains 30 days forward + drop per resolved policy.   |
| `agent_messages`           | Convert to partitioned by `created_at`, interval = daily (or weekly — confirm volume).                                       |
| `runtime_run_usage`        | Convert to partitioned by `usage_date`, interval = monthly.                                                                  |
| `runtime_model_call_usage` | Convert to partitioned by `created_at`, interval = daily or weekly.                                                          |
| `runtime_outbox`           | Likely **not** partitioned — short-lived, sweep cost low, FK to runs. Confirm.                                               |
| `agent_runs`               | Likely **not** partitioned — referenced from many tables; partition key for it would need careful FK story. Sweep continues. |

### 4.2 Code changes

| File                                                                                                 | Change                                                                                                                                                                                           |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`agent_runtime/retention/policy_resolver.py`](../../src/agent_runtime/retention/policy_resolver.py) | Add `partition_drop_eligible(scope, ttl)` decision method that the partition-drop job consults. Resolver itself unchanged.                                                                       |
| [`runtime_worker/jobs/retention_sweeper.py`](../../src/runtime_worker/jobs/retention_sweeper.py)     | Stop iterating partitioned tables; continue for unpartitioned. Add a sibling job `partition_drop_job.py` that consults `RetentionPolicyResolver` and issues `DROP PARTITION` via pg_partman API. |
| New: `runtime_worker/jobs/partition_drop_job.py`                                                     | Per-table partition-drop driver. Reads expired partitions; checks resolver; calls `partman.drop_partition_time(...)`; emits audit event.                                                         |
| New: `agent_runtime/persistence/schema/partitioning.py`                                              | Helpers wrapping pg_partman SQL — `register_table`, `set_retention(ttl)`, list partitions, drop partition.                                                                                       |
| Alembic migrations                                                                                   | Series of migrations: detach existing index, recreate as partitioned, copy data (during maintenance window), reattach. Each migration in its own PR is acceptable.                               |

### 4.3 New ops surface

- pg_partman extension installed on production DB.
- pg_partman maintenance job (cron, runs in DB) creates future partitions ahead of time.
- Monitoring: partition count per table, partition-drop event volume, oldest partition age vs resolved policy.

---

## 5. Behaviors preserved

From [refactor-audit § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved):

- **5-level retention resolution hierarchy** (`CONVERSATION > ASSISTANT > USER > ORG > default`). Resolver code unchanged.
- **User-visible deletion in observable time.** A user requesting "delete my data" gets it done in O(seconds-to-minutes), not waiting for a partition drop. Implemented via a separate per-user DELETE path that runs across partitions, plus emitting tombstones for any rows pending the next partition drop.
- **PII scope retention** still honored. If a record's resolved scope has a 30-day TTL, it disappears in ≤ 30 days regardless of which partition it lives in.
- **Field-level encryption rotation** must work against partitioned tables. Verify in §2.
- **Audit chain immutability** (per [audit-chain PRD](01-audit-chain.md)) — audit log is **not** partitioned for retention drops. Audit data is retained per its own policy.

---

## 6. Phasing

Multi-phase by table, _not_ by capability. Each table converts in its own PR with its own migration window.

### Phase A — Investigation spike (3–5 days)

Answer every row in §2. Produce a table-by-table partitioning plan with retention windows, partition intervals, and FK considerations. **No code changes.**

### Phase B — Infra prep

Install pg_partman in staging. Verify it's installable in production (cloud-DB vs self-managed). Document the install path. Set up monitoring for partition counts.

### Phase C — Per-table conversion (one PR per table)

For each high-volume table identified in §2:

1. Create new partitioned table with same schema.
2. Migrate historical data in a maintenance window (or with `pg_partman.partition_data_time` if online migration works at scale).
3. Switch app writes to the new table.
4. Drop old table.
5. Register with pg_partman; configure retention.
6. Update `RetentionSweeperLoop` to skip the table.

Suggested order (lowest risk first):

- `runtime_model_call_usage` — append-only, daily volume known, no cross-table FK headaches.
- `runtime_run_usage` — same shape, slower-growing.
- `agent_messages` — higher volume + has client-facing reads; needs more care.
- `runtime_events` — highest volume; do last, after the pattern is proven on the others.

### Phase D — Selective deletion path

If §2 finds GDPR / user-delete is needed (almost certainly yes for any regulated buyer), this is its own PR. Implements `delete_user_data(user_id)` that walks every relevant partition + emits audit + tombstones.

### Phase E — Retire old sweep paths

After all partitioned tables are live, delete now-unused sweep methods from adapters. `RetentionSweeperLoop` remains for unpartitioned tables (`runtime_outbox` short-lived; memory items; audit log).

---

## 7. Risks

| Risk                                                                                                                                             | Severity          | Mitigation                                                                                                                                                           |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Migration of a large `runtime_events` table requires extended downtime                                                                           | High              | Use `pg_partman.partition_data_time` for incremental partitioning. Schedule per customer's maintenance window. Have a documented rollback path (rename tables).      |
| Foreign keys to partitioned tables can be tricky (Postgres < 12 doesn't support FKs into partitioned children)                                   | Medium            | Modern Postgres supports this; verify version. If FK is from a partitioned table TO `agent_runs`, that direction is fine. The other direction needs design.          |
| Different rows in the same table have different retention TTLs                                                                                   | Medium-High       | Partition by `created_at`; resolver-driven sweep handles the exceptions. If exceptions are too frequent, partitioning isn't appropriate for that table — keep sweep. |
| GDPR / user-delete across partitions becomes a per-partition operation                                                                           | Medium            | Index on `user_id` per partition (pg_partman handles parent index propagation); DELETE walks partitions via the parent.                                              |
| pg_partman version mismatch between dev and prod                                                                                                 | Low               | Pin extension version in DB provisioning + CI fixture.                                                                                                               |
| Replication / read-replica lag during partition operations                                                                                       | Medium            | `DROP PARTITION` is cheap (a metadata-only op); `ATTACH PARTITION` similarly. Conversion phase is the hot spot — schedule in maintenance window.                     |
| Audit-chain integrity (per [audit-chain PRD](01-audit-chain.md)) is unaffected — _verify_ the audit log table is never partitioned for retention | High (compliance) | Pin a test that asserts `runtime_audit_log` is unpartitioned and not in pg_partman's managed-table list.                                                             |

---

## 8. Unit testing requirements

- **`test_partition_drop_respects_resolver.py`** — partition-drop job consults `RetentionPolicyResolver` before dropping; never drops a partition that resolves to a longer TTL than the partition's age.
- **`test_resolver_unchanged.py`** — every existing resolver test passes byte-identically.
- **`test_user_delete_across_partitions.py`** — `delete_user_data(user_id)` walks all partitions; verifies no rows remain; emits the expected audit event.
- **`test_audit_log_not_partitioned.py`** — guardrail. `runtime_audit_log` is not in pg_partman's managed list; cannot be dropped via partition-drop job.
- **`test_partition_drop_emits_audit_event.py`** — every partition drop is observable via the audit log.
- **`test_sweep_skips_partitioned_tables.py`** — `RetentionSweeperLoop` does not touch partitioned tables; still touches unpartitioned ones.
- **Integration test (per table)** — write rows spanning multiple partition windows; trigger pg_partman maintenance; assert old partition is dropped, recent partition retains rows, queries spanning partitions return expected results.

---

## 9. Rollback plan

Partitioning is hard to reverse without downtime. Mitigations:

- **Per-table conversion is its own PR** with its own rollback. Tables already converted stay converted.
- Before each per-table conversion, take a `pg_dump` of that table.
- The old non-partitioned table is retained (renamed) for one retention window. If issues arise: swap the names back; replay outbox entries written in the interim.
- pg_partman retention can be paused (`p_premake = 0`, `p_retention = NULL`) without dropping partitions. Useful if monitoring shows policy is wrong.

---

## 10. Open questions tracked from §2

(Filled in during Phase A spike.)

- [ ] Which tables qualify as high-volume?
- [ ] Does any high-volume table have per-row varying TTL?
- [ ] GDPR / right-to-be-forgotten API contract — exists today? What's the SLA?
- [ ] Field-level encryption rotation strategy — in-place or offline?
- [ ] pg_partman available in production DB platform?
- [ ] Retention windows per table per scope — documented?
- [ ] Existing tests covering deletion-in-observable-time?
