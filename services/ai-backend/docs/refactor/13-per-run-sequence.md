# Refactor PRD — Drop the `agent_runs` row lock from `append_event` (P16)

**Status:** Draft (rewritten 2026-05-11 after reading the code)
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §4.3](../architecture/refactor-audit.md#43-per-event-db-amplification) (point 2)
**Phase:** 4 — Targeted decoupling
**Roadmap entry:** [`00-roadmap.md` → P16](00-roadmap.md)
**Depends on:** nothing. P2 (`LISTEN/NOTIFY`) and P4 (consolidated writes) have already shipped behind toggles ([`PostgresRuntimeApiStore._notify_after_append`](../../src/runtime_adapters/postgres/runtime_api_store.py) and `_consolidated_writes`). This PRD is independent.

---

## 1. Problem (precisely, after reading the code)

The current [`PostgresRuntimeApiStore.append_event`](../../src/runtime_adapters/postgres/runtime_api_store.py#L3684) executes:

```sql
-- 1. Row lock on the run (H1) — serializes all writers per run
SELECT org_id FROM agent_runs WHERE id = %s FOR UPDATE;

-- 2. Allocate next sequence within the lock
SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
FROM runtime_events
WHERE run_id = %s;

-- 3. INSERT the event
INSERT INTO runtime_events (..., sequence_no, ...) VALUES (...);

-- 4. (if _consolidated_writes) UPDATE the cursor in the same transaction
UPDATE agent_runs
SET latest_sequence_no = %s
WHERE id = %s AND (latest_sequence_no IS NULL OR latest_sequence_no < %s);

-- 5. (if _notify_after_append) NOTIFY in the same transaction
NOTIFY <channel>, '<run_id>:<sequence_no>';
```

The `FOR UPDATE` row lock at step 1 is the H1 hazard fix. Its purpose is to serialize concurrent writers per run so step 2's `MAX(sequence_no) + 1` is correct.

In practice, concurrent writers per run are **rare** — the run handler is the primary writer, the cancel handler can write `RUN_CANCELLING`/`RUN_CANCELLED` mid-stream, the approval handler writes `APPROVAL_RESOLVED` only when the run is paused. The cancel-mid-stream race ([f4](../architecture/f4-cancel.puml)) is the one realistic concurrent case. The row lock pays a cost on **every** event append (100+ per turn, 1000+ on long completions) to protect against a race that happens at most once per run.

The `UNIQUE(run_id, sequence_no)` constraint on `runtime_events` is the actual source of truth for monotonicity. The row lock is belt-and-suspenders: it prevents the UNIQUE from firing in the first place, but if the UNIQUE never fires, removing the lock changes nothing observable; if the UNIQUE fires once per run on cancel races, removing the lock costs one retry per affected run.

### Symptoms

- Every event append acquires + releases an `agent_runs(id)` row lock. A 100-`MODEL_DELTA` turn does this 100 times.
- The lock is held across the `SELECT MAX(...)` + the INSERT + (with `_consolidated_writes`) the UPDATE + (with `_notify_after_append`) the NOTIFY. Lock duration grows with the per-event work.
- Per-run write throughput is bounded by lock acquisition latency, not by the event volume or the per-event INSERT cost.

### What this is NOT

- Not a behavior change. `sequence_no` strict monotonicity, `UNIQUE(run_id, sequence_no)`, the H3 never-rewind predicate, and `_consolidated_writes` / `_notify_after_append` semantics all remain.
- Not a schema change. No migration.
- Not a queue change. Cancel-mid-stream race ([f4](../architecture/f4-cancel.puml)) still produces consecutive `sequence_no`s — that's the contract.
- Not an in-memory adapter change. The in-memory store keeps its current per-run counter.

---

## 2. Goal

Replace the `SELECT … FOR UPDATE` row lock with **retry on `UniqueViolation`**. The `UNIQUE(run_id, sequence_no)` constraint is the canonical guard; rely on it.

The proposed `append_event` body becomes:

```python
async with self._tenant_connection(org_id=event.org_id) as conn:
    for attempt in range(_MAX_APPEND_RETRIES):
        try:
            async with conn.transaction():
                # No FOR UPDATE. Read MAX, INSERT, optionally UPDATE + NOTIFY.
                cur = await conn.execute(
                    "SELECT org_id FROM agent_runs WHERE id = %s",
                    (event.run_id,),
                )
                run = await cur.fetchone()
                cur = await conn.execute(
                    """
                    SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
                    FROM runtime_events
                    WHERE run_id = %s
                    """,
                    (event.run_id,),
                )
                next_seq = (await cur.fetchone())[_Columns.NEXT_SEQUENCE]
                envelope = self._build_envelope(event, next_seq, run)
                await conn.execute(_INSERT_RUNTIME_EVENT_SQL, _params(envelope))
                if self._consolidated_writes:
                    await conn.execute(_UPDATE_LATEST_SEQ_SQL, ...)
                if self._notify_after_append:
                    await conn.execute("SELECT pg_notify(%s, %s)", ...)
                return envelope
        except UniqueViolation as exc:
            if not _is_event_sequence_conflict(exc):
                raise
            if attempt + 1 >= _MAX_APPEND_RETRIES:
                raise OptimisticConflict(
                    "runtime_events sequence_no race exceeded retries"
                ) from exc
            await asyncio.sleep(_jitter(attempt))
```

**Properties:**

- Common case: one transaction, one `MAX(...)`, one INSERT, no lock. Same DB round-trip count as today minus the lock acquire/release.
- Race case: a peer commits between our `MAX(...)` and our INSERT → UNIQUE fires → we retry. Next iteration's `MAX(...)` sees the peer's row and we insert at `peer_seq + 1`. Strict monotonicity preserved; no gaps; no duplicates.

`_MAX_APPEND_RETRIES = 3` is plenty given the rare race profile.

### Non-goals

- Do not change the `EventStorePort` Protocol.
- Do not change `append_events_batch` semantics. The H1 lock there serves a different purpose (allocating a contiguous range of sequence_nos for one batch) and must be evaluated separately if at all — out of scope.
- Do not introduce a Redis or external sequence store.
- Do not change the in-memory adapter.
- Do not change the H3 never-rewind predicate.
- Do not change the P2 NOTIFY format (`<run_id>:<sequence_no>`).

### Success criteria

- The `SELECT … FOR UPDATE` line at [runtime_api_store.py#L3706](../../src/runtime_adapters/postgres/runtime_api_store.py#L3706) is gone.
- A representative load test on staging shows ≥ 2× p99 throughput on per-run event append.
- A property test (e.g. Hypothesis-driven) drives concurrent appends per run and asserts strict monotonicity + no gaps + no duplicates over 10k iterations.
- The cancel-mid-stream test from [f4](../architecture/f4-cancel.puml) still passes; both `MODEL_DELTA` and `RUN_CANCELLING` get consecutive `sequence_no`s in some order.
- Existing `H1 hazard` regression test passes; the docstring on `PostgresRuntimeApiStore` updates to describe the new invariant chain (UNIQUE + retry + H3 monotonic guard).

---

## 3. Why this shape and not an alternative

A staff-engineer review of the four candidates the original PRD listed:

| Candidate                                                     | Verdict   | Why                                                                                                                             |
| ------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **A. Drop FOR UPDATE; retry on UniqueViolation**              | **Adopt** | Smallest change. Existing code already computes `MAX + 1`. UNIQUE is the source of truth. One source of truth, simple, elegant. |
| B. `pg_advisory_xact_lock(hashtext(run_id))`                  | Reject    | Still serializes per run; trades a row lock for a session lock. Different cost, same shape. Doesn't move the needle.            |
| C. Per-run `next_sequence_no` column on `agent_runs` with CAS | Reject    | Schema change. Moves the contention onto the `agent_runs` row (where the FOR UPDATE already lives). No improvement.             |
| D. Per-run Postgres sequence                                  | Reject    | Sequences are global catalog objects. Thousands per active run is heavyweight; no benefit over Candidate A.                     |

Candidate A is "do nothing extra and trust the constraint that's already there." It's the right answer because the constraint is already the source of truth — the lock is redundant.

---

## 4. Architecture

### 4.1 Module boundary

The change is entirely contained in [`runtime_adapters/postgres/runtime_api_store.py`](../../src/runtime_adapters/postgres/runtime_api_store.py):

- `PostgresRuntimeApiStore.append_event` — drop the FOR UPDATE; wrap the body in a retry loop.
- Update the class docstring (lines ~5–20) to remove the H1 description and replace with the new invariant chain.

No new files. No public API change.

### 4.2 Retry condition: detect "event sequence conflict" specifically

The retry must fire only on a `(run_id, sequence_no)` UNIQUE conflict — not on any other UNIQUE violation that could surface from this statement path. Implement:

```python
def _is_event_sequence_conflict(exc: UniqueViolation) -> bool:
    # asyncpg / psycopg surfaces the constraint name; match exactly.
    constraint = getattr(exc, "constraint_name", None)
    return constraint == "runtime_events_run_id_sequence_no_key"  # confirm exact name in code
```

The constraint name is fixed by the schema and stable. **Verify the exact constraint name when implementing** — Postgres autogenerates it from `<table>_<col>_<col>_key` by default. Match it; do not fall back to substring matching.

### 4.3 Retry policy

- `_MAX_APPEND_RETRIES = 3`.
- Backoff: `await asyncio.sleep(jitter * 2**attempt)` with `jitter ≈ 5ms`. The race window is microseconds; sleep is mostly to let the peer commit.
- After exhausting retries, raise the existing `OptimisticConflict` from [`agent_runtime/persistence/exceptions.py`](../../src/agent_runtime/persistence/exceptions.py). The producer's caller already handles it.

### 4.4 Interaction with `_consolidated_writes` and `_notify_after_append`

Both toggles continue to work inside the new retry path:

- Each retry attempt is its own transaction. The UPDATE + NOTIFY ride along inside the transaction that successfully INSERTs. Rolled-back attempts produce no UPDATE and no NOTIFY by Postgres semantics.
- The H3 monotonic guard on the UPDATE (`latest_sequence_no IS NULL OR latest_sequence_no < $new`) remains. A retry that lands at `sequence_no = N+1` after a peer wrote `N` correctly advances the cursor; a peer that wins the race lands at `N` first and our `N+1` UPDATE is still monotonic.

### 4.5 No change to `append_events_batch`

The batch path also has a FOR UPDATE (line ~3885). Its purpose differs: it allocates a contiguous range `[start, start+len)` so all batched events share one `MAX(...)` call. Removing it would require either:

- Atomic `RETURNING` from a CTE that allocates and inserts in one go, OR
- A different retry shape (retry the whole batch on conflict).

Both are larger changes than this PRD warrants. **Batch path stays.** If it becomes a bottleneck, address separately.

---

## 5. Behaviors that must be preserved

| Invariant                                                                                    | Where it's checked                                                             |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `sequence_no` strictly monotonic per run                                                     | `UNIQUE(run_id, sequence_no)` constraint                                       |
| No gaps in `sequence_no`                                                                     | `MAX(sequence_no) + 1` always picks the next available                         |
| No duplicate `sequence_no` per run                                                           | UNIQUE constraint + retry                                                      |
| `latest_sequence_no` never rewinds (H3)                                                      | The monotonic guard in the `_consolidated_writes` UPDATE                       |
| NOTIFY payload format `<run_id>:<sequence_no>`                                               | `_notify_after_append` block (unchanged)                                       |
| Cancel-mid-stream produces consecutive `sequence_no`s ([f4](../architecture/f4-cancel.puml)) | UNIQUE + retry handles the race correctly                                      |
| Worker crash mid-INSERT does not leak a partial row                                          | Transaction boundary; rollback discards everything in the failed attempt       |
| Cross-run independence (no cross-run contention)                                             | No locking on `agent_runs` row → readers of run X don't block writers of run Y |

---

## 6. Edge cases

1. **First event for a run.** `MAX(sequence_no) IS NULL` → `COALESCE(..., 0) + 1 = 1`. Same as today.
2. **High-frequency cancel race.** Cancel handler and run handler both call `append_event` for the same run within microseconds. UNIQUE fires; one retries; both events land at consecutive `sequence_no`s. Order depends on commit timing — both valid per [f4](../architecture/f4-cancel.puml).
3. **Retry exhausts under sustained concurrent load.** Property test must demonstrate that 3 retries are enough for the realistic concurrent-write profile. If they aren't, increase to 5 and re-test. If still not enough, the contention profile is different from what the diagram implies — investigate before shipping.
4. **`MAX(sequence_no)` query cost on long runs.** The query is index-bounded by the existing `UNIQUE(run_id, sequence_no)` btree. `EXPLAIN ANALYZE` in CI to confirm; if the planner regresses, add an explicit covering index.
5. **Worker writing while admin sweeps retention.** Retention sweeps tombstone old `runtime_events` rows. `MAX(sequence_no)` only considers surviving rows. If retention deletes events `1..10` and the live `MAX` is now `100`, new events continue at `101`. **Same as today.**
6. **Transaction rollback releases nothing on `agent_runs`.** Without FOR UPDATE we don't take a lock to release; rollback is trivial.
7. **`_consolidated_writes=False` (toggle off).** UPDATE runs in a separate transaction via `set_run_latest_sequence`. That path also has its own H3 guard (already in place). Retry logic does not change the toggle's behavior.

---

## 7. Security considerations

- No new SQL surface. Same prepared statements, same parameter binding.
- No change to RLS policies on `runtime_events` or `agent_runs`.
- No change to `application_name` role tagging on the connection.
- Removing the lock does not change tenant isolation (each tenant's writes were already independent through separate connections).

---

## 8. Observability

Add one metric:

```
runtime_events.append_retries_total{constraint="runtime_events_run_id_sequence_no_key"}
```

Counts retry occurrences per minute. Production should sit at ≈ 0 except during cancel-mid-stream races. A spike means either (a) a new concurrent-writer pattern we missed, or (b) the `MAX(...)` query regressed (slow → larger race window). Either is worth alerting on.

Existing tracing span on `append_event` continues. Span attribute `db.operation = "append_event"` still describes the same operation.

---

## 9. Rollout

Behind a toggle, matching the team's pattern for `_consolidated_writes` and `_notify_after_append`:

```python
self._lock_free_appends: bool = settings.runtime.lock_free_appends  # default False
```

Inside `append_event`:

```python
if self._lock_free_appends:
    return await self._append_event_lock_free(event)
return await self._append_event_legacy(event)  # current FOR UPDATE path
```

Ship dark. Flip on staging. Compare retry-rate metric, latency p99, and a property-test parity run. Flip on production after a stabilization window. Remove the toggle + legacy path in a follow-up PR after a quarter of clean prod data.

---

## 10. Risks

| Risk                                                                              | Mitigation                                                                                                                                                                                                                |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Race retry under sustained concurrent load overwhelms `MAX_RETRIES`               | Property test with 16 concurrent writers per run × 1000 events. If retries > 1% of appends, raise the cap and re-test. If still hot, fall back to Candidate B (advisory lock) — same toggle, different implementation.    |
| Constraint name not exactly what the code assumes                                 | Verify constraint name in the schema before deploying. Wrong name → all `UniqueViolation`s look the same and we'd retry on real bugs.                                                                                     |
| `MAX(sequence_no)` query slow on giant runs                                       | `EXPLAIN ANALYZE` on a 100k-event fixture. Add an explicit `(run_id, sequence_no DESC)` index if the planner doesn't pick the existing UNIQUE btree.                                                                      |
| `OptimisticConflict` surfaces to the producer's caller as a user-visible error    | Audit `RuntimeEventProducer.append_api_event`'s exception handling. If it converts conflicts to wire errors today, change to one more retry at the producer layer before surfacing. Lock-free path should be transparent. |
| Hidden writers of `runtime_events` outside `append_event` / `append_events_batch` | Grep before merging. There should be zero.                                                                                                                                                                                |
| `H1 hazard` docstring on the class becomes stale                                  | Update in the same PR. New text: "Concurrent appends per run rely on the UNIQUE(run_id, sequence_no) constraint and retry; no row lock. H3 monotonic guard on `agent_runs.latest_sequence_no` is unchanged."              |

---

## 11. Tests

### 11.1 New

1. **First-event allocation.** Append the first event for a fresh run; assert `sequence_no = 1`.
2. **Sequential allocation.** 100 sequential appends; assert sequence is `1..100` with no gaps.
3. **Concurrent allocation property test (Hypothesis).** N tasks for `N ∈ {2, 4, 8, 16}`, each appending M events to the same run concurrently. Assert: monotonic, no gaps, no duplicates, count = N×M. Track retry count; assert it stays below 5% of attempts.
4. **Cancel-mid-stream race.** Run handler appends `MODEL_DELTA`; cancel handler appends `RUN_CANCELLING` simultaneously. Assert both commit with consecutive `sequence_no`s.
5. **Retry exhaustion.** Force `MAX_APPEND_RETRIES + 1` violations via fixture; assert `OptimisticConflict` raised.
6. **Constraint-name discrimination.** Synthesize a `UniqueViolation` from a different constraint; assert no retry, exception propagates.
7. **`MAX(sequence_no)` query plan.** `EXPLAIN ANALYZE` test in CI: assert the existing index is used.
8. **Cross-run independence.** Two runs writing 1000 events each in parallel. Assert no cross-run contention (wall-clock within 1.1× of single-run baseline).
9. **`_consolidated_writes=True` parity.** Append 50 events with the toggle on; assert `latest_sequence_no` matches the last `sequence_no`.
10. **`_notify_after_append=True` parity.** Append 50 events with the toggle on; assert one NOTIFY per event with the right payload.

### 11.2 Existing

- `tests/unit/runtime_adapters/postgres/test_event_store*.py` — pass without modification.
- `tests/integration/postgres/test_event_append*.py` — same.
- The `H1 hazard` regression test (if it exists by that name) — repurpose its docstring to point at the new invariant chain.

### 11.3 Deleted

- Any test that asserts on `"FOR UPDATE"` appearing in query logs. Tests on implementation, not behavior.

---

## 12. Pre-implementation checklist

Run before writing code:

1. **Find the exact constraint name.** `\d runtime_events` in psql or `SELECT conname FROM pg_constraint WHERE conrelid = 'runtime_events'::regclass`. The retry condition matches on this name exactly.
2. **Grep all writers of `runtime_events`.** Outside `append_event` / `append_events_batch`, there should be zero. Confirm.
3. **Baseline current behavior.** Capture a 100k-event property test run on the current code; record retry rate (should be 0 — there's a lock), p99 latency, and total wall clock. Run again after the change; compare.
4. **Verify the `OptimisticConflict` exception type's caller behavior.** Today it's raised only when the constraint fires through some other path; this PRD makes it raise on retry exhaustion. Confirm the producer's caller's behavior is sensible for both cases.

---

## 13. Rollback

- The toggle from §9 is the rollback. Set `lock_free_appends=False` to revert to the legacy path instantly. No data migration required.
- After a stabilization window with the toggle ON in production, remove the legacy path + toggle in a follow-up PR.

---

## 14. Out of scope

- `append_events_batch` lock. Different purpose, different surgery. Address only if measurement shows it's a bottleneck.
- Any other `FOR UPDATE` in the postgres adapter. This PRD is `append_event` only.

---

_One source of truth is the UNIQUE constraint. The row lock is belt-and-suspenders. Trust the constraint._
