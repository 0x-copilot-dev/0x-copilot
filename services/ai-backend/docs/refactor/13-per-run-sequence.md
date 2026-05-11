# Refactor PRD — Per-run sequence allocator (P16)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §4.3](../architecture/refactor-audit.md#43-per-event-db-amplification) (point 2)
**Phase:** 4 — Targeted decoupling
**Roadmap entry:** [`00-roadmap.md` → P16](00-roadmap.md)
**Depends on:** [P4 — Per-event DB ops consolidation (`04-event-write-consolidation.md`)](00-roadmap.md). The combined append + set-latest CTE from P4 is the foundation for the lock-free allocator here.

---

## 1. Problem

Every `RuntimeEventProducer.append_event` call today executes **three Postgres operations**:

1. `INSERT INTO runtime_events (...)` — the event itself.
2. `UPDATE agent_runs SET latest_sequence_no = ?` — cursor advancement.
3. `SELECT … FOR UPDATE` on `agent_runs` — to serialize concurrent writers per run (Hazard fix H2 in [`PostgresRuntimeApiStore`](../../src/runtime_adapters/postgres/runtime_api_store.py) docstring).

The `SELECT FOR UPDATE` is paranoid serialization that may not be needed in practice. The producer set per run is small and bounded:

- The **run handler** (one per active run, claimed via the queue) is the primary writer.
- The **cancel handler** can write `RUN_CANCELLING` / `RUN_CANCELLED` while the run handler is still draining a chunk in flight ([f4](../architecture/f4-cancel.puml)).
- The **approval handler** can write `APPROVAL_RESOLVED` while the run is paused — but a paused run is not actively writing, so this is non-overlapping by construction.
- Background **rollup loops** read but do not write to `runtime_events` in the active path.

The realistic concurrent-write case is **cancel mid-stream**: the cancel handler appending `RUN_CANCELLING` while the run handler appends a final `MODEL_DELTA`. That's exactly the race [f4](../architecture/f4-cancel.puml) documents and accepts.

For that one rare race, **every event append takes a row-level lock**. On a 100-MODEL_DELTA turn, that's 100 lock acquires + 100 lock releases for protection a single in-flight chunk needs.

### Symptoms

- Per-event Postgres operation count is artificially high (3 ops where 1 should suffice).
- Per-run write throughput is bounded by row-lock acquire/release latency, not by the event volume itself.
- Replicating runs at scale (load testing, replay testing) hits unnecessary contention.

### What this is NOT

- Not a behavior change. `sequence_no` strict per-run monotonicity remains. `UNIQUE(run_id, sequence_no)` constraint remains. `set_run_latest_sequence` never-rewinds remains.
- Not a contract change. SSE consumers continue to see monotonic `sequence_no`; resume continues to work via `?after_sequence=N`.
- Not a sweeping rewrite of the persistence layer. This is one allocator change inside [`PostgresRuntimeApiStore.append_event`](../../src/runtime_adapters/postgres/runtime_api_store.py).
- Not changing how the in-memory adapter assigns `sequence_no`. The in-memory store can keep its current per-run counter.

### Open question to resolve before implementation

The audit suggested "per-run Postgres sequence" as a candidate. **This is almost certainly the wrong shape** — Postgres sequences are global database objects, not row-scoped, and creating one sequence per run would mean creating thousands of catalog objects. This PRD evaluates four candidates and recommends the lightest one. The pre-implementation checklist (§10) requires confirming the choice with a benchmark.

---

## 2. Goal and non-goals

### Goal

Replace the per-event `SELECT FOR UPDATE` with a **lock-free allocator** in `PostgresRuntimeApiStore.append_event` that preserves every observable behavior. Reduce per-event Postgres operations from 3 to 1 (in concert with [P4](00-roadmap.md)).

### Non-goals

- Do not change the `EventStorePort` Protocol shape.
- Do not change the in-memory adapter's allocator.
- Do not change `RuntimeEventEnvelope` or any event-level schema.
- Do not introduce a Redis / external sequence store. Keep allocation in Postgres.
- Do not refactor the queue / lease mechanics. Out of scope.

### Success criteria

- `SELECT … FOR UPDATE` removed from `append_event`.
- Per-event Postgres operations: 1 (one `INSERT … RETURNING …` with embedded sequence resolution).
- A representative load test shows ≥ 2× throughput on per-run event append at p99.
- `UNIQUE(run_id, sequence_no)` continues to enforce the invariant; any race under the new allocator falls back to retry, not corruption.
- A property-based test (e.g. via Hypothesis) drives concurrent appends per run and asserts strict monotonicity + no gaps + no duplicates over 10k iterations.
- Cancel-mid-stream test (f4 race) continues to pass — one extra `MODEL_DELTA` may arrive after `RUN_CANCELLING`; both events get sequential `sequence_no` values.

---

## 3. Allocator candidates

The four realistic shapes, ranked from lightest to heaviest:

### 3.1 Candidate A — `INSERT` with embedded `MAX + 1` (recommended starting point)

```sql
INSERT INTO runtime_events (run_id, sequence_no, event_type, payload, ...)
VALUES (
  $1,
  COALESCE(
    (SELECT MAX(sequence_no) FROM runtime_events WHERE run_id = $1),
    0
  ) + 1,
  $2, $3, ...
)
RETURNING sequence_no, event_id, created_at;
```

**Properties:**

- One round-trip per event.
- No row lock. The `SELECT MAX` runs in the same transaction as the `INSERT`.
- `UNIQUE(run_id, sequence_no)` constraint catches the race when two writers compute the same `MAX + 1`.
- On `UniqueViolation`, retry with backoff. Common case (no race) is one INSERT with no retry.

**Race behavior:** two writers compute `MAX = 5` simultaneously, both try `sequence_no = 6`. One commits, the other fails on `UNIQUE`. The losing writer retries: `MAX` is now 6, it inserts 7. **Strict monotonicity preserved; no gaps; no duplicates.**

**Cost on the rare race:** an extra round-trip on the loser. For the documented cancel-mid-stream race, that's one event affected per run. Negligible.

**Caveat:** the `SELECT MAX` is index-bounded by `(run_id, sequence_no)` — the existing index from `UNIQUE(run_id, sequence_no)` makes this cheap.

### 3.2 Candidate B — `pg_advisory_xact_lock(hashtext(run_id))`

Replace `SELECT FOR UPDATE` with `pg_advisory_xact_lock(hashtext(run_id::text))` at the start of the transaction.

**Properties:**

- Lock is keyed on a hash of the run_id. No row lock; no on-disk state.
- Auto-released at transaction end.
- Cheaper than row lock (no IO).
- Still serializes per-run writes; on the rare race, one writer waits.

**Tradeoff:** still serializes. If the goal is throughput improvement, Candidate A is better. If the goal is "minimal change to existing logic," Candidate B is safer.

### 3.3 Candidate C — Per-run counter column with optimistic CAS

Add `agent_runs.next_sequence_no INT NOT NULL DEFAULT 1`. On append:

```sql
UPDATE agent_runs SET next_sequence_no = next_sequence_no + 1
  WHERE run_id = $1 AND next_sequence_no = $expected
  RETURNING next_sequence_no;
```

If 0 rows updated → race; reread, retry.

**Properties:**

- One round-trip in the common case.
- No locks.

**Tradeoff:** schema change required. The `agent_runs` row becomes contended for writes. Not better than Candidate A.

### 3.4 Candidate D — Per-run Postgres sequence (NOT recommended)

Create a sequence per run. Sequences are global catalog objects. Thousands of sequences is heavyweight, complicates schema dumps, and has no benefit over Candidate A.

### 3.5 Recommendation

**Start with Candidate A.** Benchmark against Candidate B as a sanity check. Adopt B only if A's race-retry under heavy concurrent-cancel load proves problematic — which is unlikely given the rare race profile.

---

## 4. Architecture

### 4.1 Module boundary

This change is contained to:

- [`runtime_adapters/postgres/runtime_api_store.py`](../../src/runtime_adapters/postgres/runtime_api_store.py) — `PostgresRuntimeApiStore.append_event` rewritten.
- [`runtime_adapters/postgres/event_store.py`](../../src/runtime_adapters/postgres/event_store.py) (if separate) — same.
- The retry policy lives in this module; do not introduce a generic retry framework.

### 4.2 Combined-statement form (depends on [P4](00-roadmap.md))

After [P4](00-roadmap.md) lands, `append_event` is one CTE:

```sql
WITH new_event AS (
  INSERT INTO runtime_events (run_id, sequence_no, event_type, payload, ...)
  VALUES (
    $1,
    COALESCE(
      (SELECT MAX(sequence_no) FROM runtime_events WHERE run_id = $1),
      0
    ) + 1,
    $2, $3, ...
  )
  RETURNING run_id, sequence_no, event_id, created_at
)
UPDATE agent_runs
SET latest_sequence_no = (SELECT sequence_no FROM new_event)
WHERE run_id = (SELECT run_id FROM new_event)
  AND COALESCE(latest_sequence_no, 0) < (SELECT sequence_no FROM new_event)
RETURNING (SELECT sequence_no FROM new_event),
          (SELECT event_id FROM new_event),
          (SELECT created_at FROM new_event);
```

The `COALESCE(latest_sequence_no, 0) < new_seq` predicate enforces the never-rewind invariant.

**One round-trip. No `FOR UPDATE`. UNIQUE constraint on `(run_id, sequence_no)` is the ultimate guard.**

### 4.3 Retry path

```python
async def append_event(self, ...) -> RuntimeEventEnvelope:
    for attempt in range(MAX_RETRIES):
        try:
            row = await self._pool.fetchrow(APPEND_EVENT_CTE_SQL, ...)
            return self._row_to_envelope(row)
        except asyncpg.UniqueViolationError as exc:
            # The (run_id, sequence_no) UNIQUE caught a concurrent allocator.
            # Backoff and retry; the next iteration's MAX will see the committed peer.
            if attempt + 1 == MAX_RETRIES:
                raise OptimisticConflict(...) from exc
            await asyncio.sleep(_backoff(attempt))
```

`MAX_RETRIES = 3` is plenty given the rare race profile. `_backoff` is exponential with a few-millisecond base.

### 4.4 The `set_run_latest_sequence` never-rewinds invariant

Preserved by the `COALESCE(latest_sequence_no, 0) < new_seq` predicate inside the CTE. If a parallel writer has already moved `latest_sequence_no` past this event's `sequence_no` (impossible given the per-run sequence_no monotonicity, but defensively asserted), the UPDATE is a no-op and the INSERT still committed. Add a unit test for this defensive predicate.

---

## 5. Edge cases

1. **Cancel mid-stream race ([f4](../architecture/f4-cancel.puml)).** Run handler appends `MODEL_DELTA` (computes `MAX = 12` → tries `sequence_no = 13`). Cancel handler appends `RUN_CANCELLING` simultaneously (same `MAX = 12` → also tries `13`). One commits at 13, other retries: re-reads `MAX = 13`, inserts at 14. Result: events are `MODEL_DELTA(13)` + `RUN_CANCELLING(14)` or vice versa. Order depends on commit timing — both are valid per [f4](../architecture/f4-cancel.puml)'s documented race.
2. **Empty run.** First event for a run: `MAX(sequence_no) IS NULL` → `COALESCE` returns 0 → `sequence_no = 1`. Pinned test.
3. **Run with thousands of events.** `MAX(sequence_no)` is index-bounded; no full table scan. Verify with `EXPLAIN ANALYZE` on a fixture run.
4. **Concurrent appends across many runs.** No cross-run serialization; throughput scales with connection-pool size.
5. **`set_run_latest_sequence` invariant.** `agent_runs.latest_sequence_no` only ever increases. The predicate enforces it.
6. **Resume after worker crash.** Worker crashed mid-write — Postgres rolls back the in-flight transaction. Next worker reads `MAX(sequence_no)` and continues from there. No gap in the user-visible sequence.
7. **MAX_RETRIES exhausted.** Raise `OptimisticConflict` (already a known persistence exception per [`persistence/exceptions.py`](../../src/agent_runtime/persistence/exceptions.py)). The caller — `RuntimeEventProducer.append_api_event` — handles it the same way it handles any persistence error today.
8. **Read-during-write consistency.** Replay queries (`list_events_after(run_id, N)`) only see committed events. The `INSERT` happens in the same transaction as the read inside the CTE; no dirty reads.
9. **Worker writing while admin sweeps retention.** Retention sweeps tombstone old events; the allocator is unaffected because `MAX(sequence_no)` only considers existing rows. If retention deletes events 1–10, `MAX = 100` of the surviving rows; new events continue at 101. **This is the same as today** — verify, but no regression expected.

---

## 6. Security considerations

- No new SQL surface. Same prepared statement style, same parameter binding.
- No change to row-level security policies on `runtime_events`. Tenant isolation continues via existing RLS / WHERE clauses.
- No change to `application_name` role tagging.

---

## 7. Observability

- Add a metric: `runtime_events.append_retries_total{run_id_hash}` — counts retry occurrences. Production should see ≈ 0 retries except during cancel races. A spike indicates either:
  - A new concurrent-writer pattern we didn't account for.
  - A slow query (`MAX(sequence_no)` regression on a missing index).
- Existing tracing on `append_event` continues; span attribute `db.operation = "append_event"` becomes representative of one round-trip rather than three.

---

## 8. Risks

| Risk                                                                        | Mitigation                                                                                                                                                                                                      |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Race retry under sustained heavy concurrency overwhelms `MAX_RETRIES`       | Property test with synthetic concurrent writers (10+ tasks per run); confirm retry counts stay low. If they don't, fall back to Candidate B (advisory lock) and re-evaluate.                                    |
| `MAX(sequence_no)` query gets slow on giant runs                            | The existing `UNIQUE(run_id, sequence_no)` index covers this. Verify with `EXPLAIN ANALYZE` on a 100k-event fixture. If the planner picks a different path, add an explicit `(run_id, sequence_no DESC)` index. |
| The defensive `COALESCE(latest, 0) < new_seq` predicate masks a real bug    | Pin a test that constructs a run with `latest_sequence_no = 99` and tries to write `sequence_no = 50`. Assert the row is inserted (constraint allows it) and `latest_sequence_no` stays at 99.                  |
| `OptimisticConflict` raised more often than the existing producer expects   | Audit `RuntimeEventProducer.append_api_event`'s exception handling. If it converts conflicts to user-visible errors, change the conversion to retry one more time at the producer level. Keep it transparent.   |
| Hidden writers we missed (e.g. an admin script, a one-shot data migration)  | Pre-implementation grep: every writer of `runtime_events` outside `PostgresRuntimeApiStore.append_event`. There should be zero.                                                                                 |
| Schema dump / replication lag changes due to removed `FOR UPDATE` semantics | Logical replication treats both forms identically (only the committed write replicates). Verify in staging by running a replica and diffing the event stream.                                                   |
| `H2 hazard` documented in `PostgresRuntimeApiStore` docstring becomes stale | Update the docstring in the same PR. Document why the lock is no longer needed and what the new invariant chain is (UNIQUE constraint + retry + COALESCE predicate).                                            |

---

## 9. Unit testing requirements

### 9.1 New tests

1. **First-event allocation.** Append the first event for a fresh run; assert `sequence_no = 1`, `latest_sequence_no = 1`.
2. **Sequential allocation.** 100 sequential appends; assert sequence is `1..100` with no gaps.
3. **Concurrent allocation property test.** Hypothesis-driven: spawn N tasks (N in `[2, 4, 8, 16]`), each appending M events to the same run concurrently. Assert: monotonic, no gaps, no duplicates, count = N\*M.
4. **Cancel-mid-stream race.** Two tasks: one appends `MODEL_DELTA`, the other appends `RUN_CANCELLING` against the same run. Assert both succeed with consecutive `sequence_no`s.
5. **Retry counter.** Force a `UniqueViolation` once via fixture; assert one retry happens and the second attempt succeeds.
6. **Retry exhaustion.** Force `MAX_RETRIES + 1` violations; assert `OptimisticConflict` raised.
7. **Never-rewind invariant.** Manually set `agent_runs.latest_sequence_no = 99`. Append at `sequence_no = 50`. Assert UPDATE is no-op; INSERT committed.
8. **Cross-run independence.** Two parallel runs writing 1000 events each. Assert no cross-run contention (measure via wall-clock time vs. single-run baseline).
9. **`MAX(sequence_no)` plan.** `EXPLAIN ANALYZE` test in CI: assert the query uses the existing index, not a sequential scan.
10. **`OptimisticConflict` exception path.** Assert `RuntimeEventProducer.append_api_event` handles it the same way it handles other persistence errors today (no behavior change at the producer's caller).

### 9.2 Existing tests touched

- All tests in `tests/unit/runtime_adapters/postgres/test_event_store*.py` — verify they pass without modification.
- All tests in `tests/integration/postgres/test_event_append*.py` — same.
- The `H2 hazard` regression test — verify it still passes; update the test docstring to point at the new invariant chain.

### 9.3 Tests deleted

- Any test that asserts on `SELECT … FOR UPDATE` text in query logs (those are testing implementation, not behavior).

---

## 10. Pre-implementation checklist

Run before writing code:

1. **Confirm [P4](00-roadmap.md) has shipped.** This PRD's CTE form depends on the combined-append+set-latest pattern from P4. If P4 hasn't landed, either land it first or scope this PRD to also include the combination.
2. **Grep all writers of `runtime_events`.** They must all live in `PostgresRuntimeApiStore.append_event`. Any other writer must be brought into scope or explicitly exempted.
3. **`EXPLAIN ANALYZE` the proposed `INSERT … (SELECT MAX …) …` form on a 100k-event fixture.** Confirm the index is used. If not, add the explicit `(run_id, sequence_no DESC)` index in the same PR.
4. **Read the existing `H2 hazard` docstring in [`PostgresRuntimeApiStore`](../../src/runtime_adapters/postgres/runtime_api_store.py).** Document in this PRD what specifically the docstring promises. The new code must keep those promises.
5. **Benchmark Candidate A vs. Candidate B** on staging. The recommendation is A; the benchmark either confirms or steers to B.
6. **Verify the `OptimisticConflict` exception type already exists in [`persistence/exceptions.py`](../../src/agent_runtime/persistence/exceptions.py).** Use the existing type; don't introduce a new one.
7. **Run the existing concurrency-property tests on `main` to capture a baseline.** Compare against the new implementation.

---

## 11. Rollback plan

- Single PR; rollback = revert.
- No schema change in Candidate A. No data migration.
- If retries cause user-visible errors after rollout, switch to Candidate B (advisory lock) without a schema change. Both shapes can coexist behind a `RUNTIME_EVENT_ALLOCATOR=cas|advisory_lock` setting if a graceful migration is preferred.

---

## 12. Out of scope (handled by other PRDs)

- Combining `append_event` with `set_run_latest_sequence` into one statement — that's [P4](00-roadmap.md), the precondition.
- SSE delivery via `LISTEN/NOTIFY` — [P2](00-roadmap.md).
- Retention partitioning — [P18](00-roadmap.md).
- Repository-pattern collapse of the persistence layer — [P19](00-roadmap.md).

---

_Per the team's spec-first workflow ([`docs/CLAUDE.md`](../CLAUDE.md)): do not start implementation until §10 is complete and this PRD is reviewed. The hardest part of this change is the property test that proves strict monotonicity under concurrent appends — write that test first, watch it fail under the current `FOR UPDATE` if you remove the lock without the new allocator, then implement the allocator until the test passes._
