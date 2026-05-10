# Refactor PRD — Per-event DB ops consolidation (P4)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §4.3](../architecture/refactor-audit.md#43-per-event-db-amplification)
**Roadmap position:** [00-roadmap.md — Phase 1, P4](00-roadmap.md#phase-1--performance-wins-no-structural-change)
**Downstream dependency:** [P16 — eliminate `SELECT FOR UPDATE` via per-run sequence](00-roadmap.md#phase-4--targeted-decoupling) builds on the `INSERT … RETURNING` pattern this PRD establishes. P4 must land first.

---

## 1. Problem

Every `RuntimeEventEnvelope` written to the event store goes through `RuntimeEventProducer.append_api_event` ([`agent_runtime/api/events.py`](../../src/agent_runtime/api/events.py)). The current path makes **three Postgres round-trips per event**:

1. `SELECT … FOR UPDATE` on `agent_runs` to serialize concurrent appends per run (hazard fix H2 in [`runtime_adapters/postgres/`](../../src/runtime_adapters/postgres/)).
2. `INSERT INTO runtime_events` (`append_event`) — assigns `sequence_no`.
3. `UPDATE agent_runs SET latest_sequence_no = ?` (`set_run_latest_sequence`) — never-rewinding cursor used for resume.

`PRESENTATION_UPDATED` (the polish follow-up) doubles this for every user-visible event, so the worst case for a polished `TOOL_RESULT` is **6 ops per logical event**. P1 ([`01-presentation-polish-removal.md`](01-presentation-polish-removal.md)) collapses the 6 → 3 path; this PRD collapses 3 → 1 (or 1+lock for P4 scope; the lock itself goes in P16).

A second, independent contributor: provider streaming emits one `MODEL_DELTA` per text chunk. A long completion produces **100+ chunks per turn**, each one its own append. There is no coalescing. The architecture index records this as the dominant per-turn write volume.

### Symptoms (today)

Approximate, drawn from the audit and the documented architecture; **verify with measurement before committing the plan**:

- A streaming turn that emits ~30 visible events + 100 `MODEL_DELTA` chunks runs ~390 DB ops in the event-write path alone (3 × 130). With `PRESENTATION_UPDATED`, ~480.
- Each append acquires the row lock on `agent_runs`, so per-run write throughput is bounded by lock acquire/release latency.
- The two-step `INSERT` + `UPDATE` makes `latest_sequence_no` lag the latest event by one round-trip; SSE adapters that read `latest_sequence_no` to decide whether to call `replay_events` see stale values briefly.
- For the in-memory adapter, all three ops are also separate method calls — the abstraction is consistent across both adapters but the cost shape only matters for Postgres.

### Why two changes in one PRD

The two changes are independent in the code path but **share the same critical invariant**: `sequence_no` must remain strictly monotonic per run, with `UNIQUE(run_id, sequence_no)` enforced. Bundling them lets one set of pinned tests (and one rollback flag pair) cover both. They are gated by separate flags so each can be enabled independently in production.

### What this is NOT

- **Not the FOR UPDATE removal.** That's [P16](00-roadmap.md#phase-4--targeted-decoupling). P4 keeps the existing per-run row lock; only the `INSERT` and `UPDATE` collapse into one statement.
- **Not the polish removal.** That's [P1](01-presentation-polish-removal.md). P4 is independent of polish — it shrinks per-event cost regardless of how many logical events there are.
- **Not a wire-format change.** Default delta-coalescing variant ([§3.2](#32-stage-2--worker-side-model_delta-coalescing-batched-insert)) batches the _DB write_, not the SSE frame. SSE clients still see one `MODEL_DELTA` envelope per chunk.
- **Not a `payload.delta` schema change.** An optional Stage 2 variant exists ([§3.3](#33-stage-2-variant-multi-chunk-payload-opt-in)) that does change `payload.delta` to carry N chunks; that variant is opt-in behind its own flag and not the default.
- **Not an event-store schema migration.** No new tables, no new columns. `runtime_events` and `agent_runs` keep their existing shape.

---

## 2. Goal and non-goals

### Goal

Reduce the dominant per-event Postgres cost in two stages:

1. **Stage 1.** Collapse `INSERT runtime_events` + `UPDATE agent_runs.latest_sequence_no` into one statement using a CTE with `RETURNING`. The row lock acquired in step (1) of the current path stays.
2. **Stage 2.** Coalesce `MODEL_DELTA` chunk writes from the worker. Default: batched multi-row `INSERT` of N chunks (one row per chunk, but one round-trip per batch). Opt-in: collapse N chunks into a single `MODEL_DELTA` row whose `payload.delta` carries an array.

### Non-goals

- Removing or relaxing `SELECT FOR UPDATE` on `agent_runs`. Out of scope; tracked in [P16](00-roadmap.md#phase-4--targeted-decoupling).
- Changing the SSE wire format (`event: <name>\nid: <seq>\ndata: <json>`).
- Changing `RuntimeEventEnvelope` field shape, including `sequence_no` semantics.
- Changing the resume contract (`?after_sequence=N`).
- Changing the `EventStorePort` API surface beyond what's strictly required to expose batched append. The single-statement collapse is internal to the adapter; consumers continue to call `append_event(envelope)`.
- Coalescing event types other than `MODEL_DELTA`. Tool events, lifecycle events, approval events, citation events all stay one append per emission. (`MODEL_DELTA` is the only event type with documented chunk-storm behavior.)
- Touching the in-memory adapter beyond the parity work needed to keep its method semantics aligned with Postgres.

### Success criteria

- `runtime_adapters/postgres/postgres_runtime_api_store.py` (or its event-store module) implements `append_event` as a single statement with `RETURNING sequence_no, latest_sequence_no` (or equivalent CTE form).
- `set_run_latest_sequence` is no longer called as a separate adapter method from the producer's append path. The method itself remains on the port for callers that legitimately need to set the cursor without appending (e.g. recovery).
- `runtime_worker/streaming_executor.py` (or its `MODEL_DELTA` channel handler — likely [`stream_messages.py`](../../src/runtime_worker/stream_messages.py)) coalesces chunks within a configurable window (`RUNTIME_DELTA_COALESCE_WINDOW_MS`, default off for safe rollout).
- A new adapter method `append_events_batch(envelopes: list[RuntimeEventEnvelope]) -> list[int]` on `EventStorePort` (or `AsyncEventStorePort` post-[P5](01-async-only-ports.md)) returns the assigned `sequence_no` list in input order, all under one DB round-trip. Used only by the delta coalescer.
- Per-event Postgres ops measured for a representative streaming turn (100 chunks + 30 visible events): **≥60% reduction** vs. baseline.
- p99 of `runtime_event_append_duration_ms` (existing metric or new — see [§7](#7-observability)) drops by ≥30% on staging at the same throughput.
- No regression on: SSE delivery latency, run-create p99, approval-resolve p99, replay correctness, resume correctness, in-memory adapter parity tests.
- All current tests pass without skipping or xfail. New tests added per [§6](#6-unit-testing-requirements).

---

## 3. Systems touched

Inventory derived from the audit's references and the architecture index. **File paths marked "verify" must be confirmed by `grep` before implementation; the exact module split inside `runtime_adapters/postgres/` and the worker streaming pipeline isn't fully visible from the diagrams.**

### 3.1 Stage 1 — single-statement append

**Adapters:**

- [`runtime_adapters/postgres/postgres_runtime_api_store.py`](../../src/runtime_adapters/postgres/) (verify exact file) — rewrite `append_event` as one of:

  ```sql
  -- Form A: CTE with two writes returning the new latest cursor
  WITH inserted AS (
      INSERT INTO runtime_events (run_id, sequence_no, ...)
      VALUES ($1, nextval(...), ...)
      RETURNING sequence_no
  )
  UPDATE agent_runs
     SET latest_sequence_no = (SELECT sequence_no FROM inserted)
   WHERE run_id = $1
     AND latest_sequence_no < (SELECT sequence_no FROM inserted)
  RETURNING latest_sequence_no;
  ```

  The `latest_sequence_no < (SELECT sequence_no FROM inserted)` guard preserves the **never-rewinds** invariant. If the conditional UPDATE does not match, the row stays at its prior value — but that path should never trigger in practice because `sequence_no` is allocated monotonically inside the same transaction.

  Final form to be chosen during implementation. Variants:
  - **Form A:** as above. Two writes, one round-trip.
  - **Form B:** trigger-based — `AFTER INSERT ON runtime_events` updates `agent_runs.latest_sequence_no`. One write from app, but moves invariant logic into the schema. Rejected as default because it splits the invariant across two layers.
  - **Form C:** stored procedure / `LANGUAGE plpgsql` function. Same effect as Form A; not preferred — one more thing to migrate when changing logic.

  Recommended: Form A.

- [`runtime_adapters/in_memory/runtime_api_store.py`](../../src/runtime_adapters/in_memory/runtime_api_store.py) — `append_event` continues to do `sequence_no` allocation + `latest_sequence_no` update within one method call. No semantic change; method already runs under a single dict-mutation block. Add the same `latest > prior` guard for parity.

**Producer:**

- [`agent_runtime/api/events.py`](../../src/agent_runtime/api/events.py) — `RuntimeEventProducer.append_api_event` currently performs steps 4–5 of the architecture-doc note ("event_store.append_event → assigns sequence_no" then "set_run_latest_sequence"). After this PRD: only step 4 is called; the cursor update happens inside the adapter and the producer reads it back from the returned envelope.

**Port:**

- [`agent_runtime/api/async_ports.py`](../../src/agent_runtime/api/async_ports.py) (or `ports.py` post-[P5](01-async-only-ports.md)) — `append_event` already returns the populated envelope per the architecture doc. Confirm the contract: returned envelope must carry the assigned `sequence_no`. Document that `latest_sequence_no` on `agent_runs` is updated by the same statement; consumers must not call `set_run_latest_sequence` after `append_event`.

### 3.2 Stage 2 — worker-side `MODEL_DELTA` coalescing (batched insert)

**Worker:**

- [`runtime_worker/streaming_executor.py`](../../src/runtime_worker/streaming_executor.py) — wrap the existing per-chunk emission in a `DeltaCoalescer` instance that buffers chunks for up to `RUNTIME_DELTA_COALESCE_WINDOW_MS` (default `0` = disabled) and flushes either on window expiry, on a non-`MODEL_DELTA` event, on stream end, or on cancellation.
- [`runtime_worker/stream_messages.py`](../../src/runtime_worker/stream_messages.py) (verify) — likely the right home for `DeltaCoalescer` since it owns the messages channel. If `stream_parts.py` handles per-chunk emission, the coalescer goes there.

**Adapter:**

- New adapter method `append_events_batch(envelopes) -> list[int]` on the Postgres adapter and the in-memory adapter. Implementation:

  ```sql
  WITH inserted AS (
      INSERT INTO runtime_events (run_id, sequence_no, ...)
      VALUES ($1, $2, ...), ($1, $3, ...), ...
      RETURNING sequence_no
  )
  UPDATE agent_runs
     SET latest_sequence_no = (SELECT MAX(sequence_no) FROM inserted)
   WHERE run_id = $1
     AND latest_sequence_no < (SELECT MAX(sequence_no) FROM inserted)
  RETURNING latest_sequence_no;
  ```

  Sequence numbers are pre-allocated in the worker (next N values from a counter held by the adapter, or — once [P16](00-roadmap.md#phase-4--targeted-decoupling) lands — from a Postgres sequence). Pre-allocation guarantees the batch does not interleave with another writer's allocation.

**Port:**

- Add `append_events_batch` to `AsyncEventStorePort`. Methods marked optional with `@runtime_checkable` Protocol semantics; existing adapters that don't implement it fall back to N calls of `append_event`.

### 3.3 Stage 2 variant — multi-chunk payload (opt-in)

**Behavior change** — only enabled if `RUNTIME_DELTA_PAYLOAD_BATCHED=true`. Default false.

- `MODEL_DELTA` envelope's `payload.delta` becomes `payload.deltas: list[str]` (or stays `payload.delta` typed as `str | list[str]` for backward compatibility — to be decided in implementation, prefer the latter for SSE consumer compat).
- One row per N coalesced chunks instead of N rows.
- SSE adapter must serialize the batched envelope as one frame; clients must render N deltas from the batched payload.

**Why opt-in:** any existing client that reads `event.payload.delta` as a `str` will break. We do not have a clean inventory of consumers from the diagrams alone. The opt-in flag lets the change ship safely once the frontend is verified.

**Verification before flipping:** grep `apps/frontend/` for `delta` field reads. Update those readers to handle either shape, then flip the flag. (This work is **out of scope for P4** and would be a follow-up frontend PR.)

### 3.4 Configuration

New env vars on [`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py) and [`runtime_api/app.py`](../../src/runtime_api/app.py):

| Name                                | Default | Purpose                                                                                                              |
| ----------------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------- |
| `RUNTIME_EVENT_WRITE_CONSOLIDATED`  | `true`  | Stage 1 single-statement append. Default-on after staging soak; ship behind flag for the first deploy.               |
| `RUNTIME_DELTA_COALESCE_WINDOW_MS`  | `0`     | Stage 2 coalesce window. `0` disables coalescing; recommended `50` after measurement.                                |
| `RUNTIME_DELTA_COALESCE_MAX_CHUNKS` | `64`    | Hard cap on chunks per batch insert. Forces a flush even if window has not expired. Defends against runaway buffers. |
| `RUNTIME_DELTA_PAYLOAD_BATCHED`     | `false` | Stage 2 variant — multi-chunk payload. Requires frontend update; do not enable without it.                           |

---

## 4. Behaviors that must survive

Pulled from [refactor-audit § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved). Each behavior gets at least one pinned test ([§6](#6-unit-testing-requirements)).

**Streaming and resume:**

- Per-run monotonic `sequence_no`. No batch insert may reorder events emitted in a single window.
- `UNIQUE(run_id, sequence_no)` constraint must never be violated. If batch allocation collides with another writer (it shouldn't, given the row lock), the batch must roll back as one.
- `set_run_latest_sequence` must never rewind. The conditional `WHERE latest_sequence_no < new` guard enforces this in the SQL; the in-memory adapter must mirror it.
- `?after_sequence=N` resume returns events with `sequence_no > N` exactly once. Batched inserts must not produce gaps.
- Terminal events (`RUN_COMPLETED`, `RUN_FAILED`, `RUN_CANCELLED`, `RUN_REJECTED`) must always flush any pending coalesce buffer before being appended; clients must not see a terminal envelope before they see the deltas that preceded it.

**Concurrency:**

- The existing per-run write serialization via `SELECT … FOR UPDATE` on `agent_runs` is preserved as-is. P4 changes only the writes that happen _under_ the lock.
- The P4 change must not introduce deadlock between concurrent append paths. CTE form A holds the lock on `agent_runs` for one statement instead of two — strictly less time, no new lock objects.

**Cancellation:**

- A `MODEL_DELTA` chunk in flight when cancellation fires may still arrive (existing acknowledged race per [f4](../architecture/f4-cancel.puml)). After P4, that chunk is in the coalesce buffer; it is flushed as one final `MODEL_DELTA` (Stage 2 variant: as one row with up to N chunks) before `RUN_CANCELLED` is appended.
- The terminal-flush rule above subsumes cancellation: any non-`MODEL_DELTA` event triggers a buffer flush, so `RUN_CANCELLING` / `RUN_CANCELLED` always land after their preceding deltas.

**Observability:**

- `RuntimeEventEnvelope` field validation (incl. `ObservabilityRedactor.redact_json_object` on `payload` and `metadata`) runs per envelope, both in single-event and batched paths.
- `created_at` on each envelope is the time it was emitted by the worker, not the time it was flushed. Coalescing must preserve per-chunk timestamps if Stage 2 variant is used (then `payload.deltas` is `[{text, t}, {text, t}, ...]` rather than `[str, str, ...]`).

**Adapter parity:**

- For any sequence of `append_event` calls (and equivalent `append_events_batch` calls), the in-memory adapter and the Postgres adapter produce identical envelope sequences with identical `sequence_no` values.

---

## 5. Risks

### 5.1 CTE semantics and Postgres version compatibility

The two-statement CTE in Form A relies on `WITH … RETURNING` driving an `UPDATE`. Available since Postgres 9.1; supported in all current production versions. **Verify production Postgres version before implementation.**

The conditional `WHERE latest_sequence_no < (SELECT sequence_no FROM inserted)` requires the SELECT to evaluate the inserted row, not the prior table state. CTE visibility rules guarantee this; behavior is well-documented and stable. Test explicitly with a concurrent-write scenario.

**Mitigation:** an integration test against a real Postgres instance (or `pgx` in-memory simulator) covering 100 concurrent appends to the same run.

### 5.2 Coalescing changes the timing of `MODEL_DELTA` events

Stage 2 buffers chunks for up to `RUNTIME_DELTA_COALESCE_WINDOW_MS` before writing. SSE clients see chunks with that delay added.

- Default window of `50 ms` is below the typical human perceptibility threshold for streaming text (~100 ms).
- The hard cap (`RUNTIME_DELTA_COALESCE_MAX_CHUNKS=64`) bounds buffer size against pathological emit rates.
- Default off (`0`) — opt-in per environment, with measurement.

**Mitigation:** ship Stage 2 with the flag default-off; flip on staging only after measuring user-perceived smoothness on a representative chat session.

### 5.3 Worker crash mid-coalesce-window

If the worker crashes between a chunk landing in the coalescer buffer and the buffer being flushed, those chunks are lost from the event store. The model output up to the crash is also gone (LangGraph would rerun the run on retry), so the user-visible effect is the same as a non-coalescing crash — but the buffer adds a small additional window.

**Mitigation:**

- The default `50 ms` window is far smaller than typical model inter-chunk latency; expected buffer size at any moment is 1–3 chunks.
- On graceful shutdown (signal handler), the coalescer must flush before the worker exits.
- On explicit cancellation, the coalescer flushes before `RUN_CANCELLED` appends (per the terminal-flush rule).
- Tests cover SIGTERM, SIGINT, and uncaught-exception paths.

### 5.4 Stage 2 variant breaks `payload.delta` consumers

Any client that reads `event.payload.delta` as `str` would break if the field becomes `list[str]` or `list[ChunkDict]`.

**Mitigation:**

- Stage 2 variant is its own flag, default false.
- Type the field as `str | list[str | ChunkDict]` if both shapes coexist during transition. Pydantic discriminator handles the dispatch.
- Frontend grep before flipping — explicitly out of scope for this PRD; it's a follow-up gate.

### 5.5 The new `append_events_batch` port method

Adding a method to `EventStorePort` requires every implementer to either provide it or accept the default fallback. Since both adapters are in-house and only used inside this service, the surface change is contained.

**Mitigation:**

- Provide a Protocol default (or a `BaseEventStore` mixin) that implements `append_events_batch` as a loop over `append_event`. Adapters that want the optimization override it; tests of the loop fallback exist.
- Document on the Protocol that batch implementations must preserve input order, allocate sequence numbers contiguously to the input, and roll back as one transaction on failure.

### 5.6 Rollback boundary

Two flags, two stages — three production states to manage:

| `WRITE_CONSOLIDATED` | `COALESCE_WINDOW_MS` | Behavior                                        |
| -------------------- | -------------------- | ----------------------------------------------- |
| `false`              | `0`                  | Pre-P4 baseline. Always available as rollback.  |
| `true`               | `0`                  | Stage 1 only. Safe rollback for Stage 2 issues. |
| `true`               | `> 0`                | Stage 1 + Stage 2 batched insert.               |

Flipping `WRITE_CONSOLIDATED=false` requires re-introducing the two-step path; the original code path stays in place behind the flag for one release cycle, then is deleted.

---

## 6. Unit testing requirements

### 6.1 Adapter tests (Postgres + in-memory parity)

For each adapter, both as a unit test and as a parity test (run the same test against both adapters in a single fixture):

- `test_append_event_returns_sequence_no` — single append assigns `sequence_no=1`; second assigns `2`; envelope returned by `append_event` carries the assigned `sequence_no`.
- `test_append_event_updates_latest_sequence_no` — after `append_event`, `get_run(run_id).latest_sequence_no` equals the appended envelope's `sequence_no`.
- `test_append_event_never_rewinds_latest` — given an `agent_runs` row with `latest_sequence_no=10`, calling `append_event` for the same run with a manually-set `sequence_no=5` (test-only path) must not lower the cursor. (Realistically this can't happen in production since sequence allocation is monotonic; the test pins the SQL guard regardless.)
- `test_concurrent_appends_assign_unique_sequence_nos` — 100 concurrent `append_event` calls on the same run assign distinct `sequence_no` values 1–100; `UNIQUE(run_id, sequence_no)` is never violated.
- `test_append_events_batch_assigns_sequential_sequence_nos` — `append_events_batch([e1, e2, e3])` returns `[N+1, N+2, N+3]` in input order, latest cursor advances to `N+3`.
- `test_append_events_batch_rolls_back_on_failure` — if any envelope in the batch fails validation, the entire batch is not written; latest cursor is unchanged.
- `test_append_events_batch_under_concurrent_load` — two concurrent batches of 10 each on the same run produce 20 distinct sequence numbers; no interleaving within a batch.

### 6.2 Producer tests

- `test_producer_does_not_call_set_run_latest_sequence_after_consolidation` — confirm `RuntimeEventProducer.append_api_event` no longer calls the port's `set_run_latest_sequence` after the producer's own append. (Test against a spy port.)
- `test_producer_returns_envelope_with_sequence_no` — unchanged behavior; test pins it.

### 6.3 Coalescer tests (Stage 2)

In [`tests/unit/runtime_worker/test_delta_coalescer.py`](../../tests/unit/runtime_worker/) (new file):

- `test_coalescer_disabled_window_zero_writes_per_chunk` — `RUNTIME_DELTA_COALESCE_WINDOW_MS=0` produces one append per chunk (matches pre-P4 behavior).
- `test_coalescer_flushes_on_window_expiry` — chunks emitted within the window land in one batch; window expires → batched append fires.
- `test_coalescer_flushes_on_non_delta_event` — emitting a `TOOL_CALL` mid-buffer triggers immediate flush of pending deltas before the `TOOL_CALL` appends.
- `test_coalescer_flushes_on_stream_end` — `FINAL_RESPONSE` flushes the buffer, then appends; buffer is empty after.
- `test_coalescer_flushes_on_cancel` — `RUN_CANCELLING` triggers flush before append.
- `test_coalescer_respects_max_chunks` — emitting 100 chunks within a single window with `MAX_CHUNKS=64` produces ≥2 batches.
- `test_coalescer_preserves_chunk_order` — after coalescing, the per-batch envelope's chunks (or the consecutive single-chunk envelopes, in the default variant) appear in emission order.
- `test_coalescer_flushes_on_graceful_shutdown` — signal handler shuts down the coalescer; pending chunks are flushed.
- `test_coalescer_loses_buffered_chunks_on_uncaught_exception` — explicitly documents the failure mode; pin so a future change can't accidentally claim durability.

### 6.4 End-to-end SSE replay tests

In [`tests/unit/runtime_api/`](../../tests/unit/runtime_api/) (extend existing):

- `test_streaming_replay_with_coalescer_default_variant_unchanged_wire_format` — turn that emits 50 chunks; SSE consumer sees 50 `MODEL_DELTA` envelopes in order; `?after_sequence=N` resume midway returns the correct slice. Default variant (one envelope per chunk, batched DB write) is invisible to clients.
- `test_streaming_replay_with_payload_batched_variant` (gated on `RUNTIME_DELTA_PAYLOAD_BATCHED=true`) — 50 chunks emit ≤ ceil(50 / batch_size) `MODEL_DELTA` envelopes; each envelope's `payload.deltas` carries N chunks; client renders all 50.

### 6.5 Performance harness (separate from unit tests)

A new bench script under [`tests/perf/`](../../tests/perf/) (new directory; verify naming convention):

- `bench_append_event.py` — measures p50/p99 of `append_event` against a real Postgres instance, baseline vs. P4. Reports the count of DB ops via `pg_stat_statements`.
- `bench_streaming_turn.py` — simulates a 100-chunk turn, measures total wall-clock time and `runtime_events` row count.

These are not part of the unit suite. They run on staging during the rollout decision.

---

## 7. Observability

Existing metrics ([`agent_runtime/observability/`](../../src/agent_runtime/observability/)) cover most of what's needed; this PRD adds two:

- `runtime_event_append_duration_ms` — histogram. Existing or add. p99 must drop ≥30% post-P4 at the same throughput.
- `runtime_delta_coalesce_batch_size` — histogram of chunks per batch. Distribution shape tells us whether the window setting is right.
- `runtime_delta_coalesce_pending_at_shutdown` — counter. Must be zero on graceful shutdown.
- `runtime_event_append_db_ops_total` — counter, labeled by `op_kind` (`single_statement` / `batched`). Confirms the consolidation is actually happening in production.

Logs:

- Worker logs at `INFO` when the coalescer is enabled, including the window value at startup.
- `WARNING` if a batched insert returns a non-contiguous sequence_no list (would indicate a serious adapter bug).

OTel spans:

- `runtime_event_append` span attributes: `batch_size`, `consolidated` (bool), `run_id`. Inherits trace context from the event-emitting code path.

---

## 8. Rollout plan

1. **PR 1 (Stage 1).** Implement the CTE in the Postgres adapter behind `RUNTIME_EVENT_WRITE_CONSOLIDATED` (default `true`). Update in-memory adapter for parity. Update producer to drop the explicit `set_run_latest_sequence` call. Land all Stage 1 tests. Ship; soak on staging for ≥48 hours; verify metrics. If clean, default-on in production.
2. **PR 2 (Stage 2 default variant).** Implement `append_events_batch` on both adapters; implement `DeltaCoalescer` in the worker. Default `RUNTIME_DELTA_COALESCE_WINDOW_MS=0` (off). Land Stage 2 tests including the coalescer suite. Ship.
3. **Decision point.** Measure on staging with `WINDOW_MS=50`. Confirm SSE delivery feels smooth (subjective + p99 latency metric). If yes, default-on in production.
4. **Out of scope (separate PR).** Frontend grep + update for the multi-chunk `payload.delta` variant. Not gated on this PRD; not required for the latency win.

Each PR ships independently and can be reverted via flag.

---

## 9. Open questions

These must be resolved before implementation. None are blockers for the PRD itself.

- **Postgres version in production.** CTE Form A requires 9.1+; should be fine, but verify.
- **Exact location of `MODEL_DELTA` emission in the worker.** The diagram lists [`stream_messages.py`](../../src/runtime_worker/stream_messages.py) and [`stream_parts.py`](../../src/runtime_worker/stream_parts.py); confirm which one owns the emission point and put the coalescer there.
- **Whether `append_events_batch` should be an addition to `EventStorePort` or live on a sibling interface.** Adding to the existing port keeps the coupling tight (preferred). A sibling interface avoids growing the port further but adds wiring.
- **Whether the producer's call sites currently rely on the second-step `set_run_latest_sequence` for any side effect** beyond updating the cursor. Grep before deletion.
- **Stage 2 variant: `payload.delta: str | list[str]` vs. `payload.deltas: list[str]`.** Decide based on Pydantic discriminator ergonomics and frontend impact. Out of scope until the variant is being shipped.
- **Whether [`RUNTIME_DELTA_COALESCE_MAX_CHUNKS`](#34-configuration) should be per-run-token-budget aware** — i.e. flush more aggressively for runs that are close to a context limit. Probably not for v1; revisit if the metric distribution suggests pathological tails.

---

## 10. Definition of done

- Stage 1 PR shipped, default-on in production, ≥48-hour soak on staging clean, metrics confirm DB-op reduction.
- Stage 2 PR shipped, coalescer code deployed with default `WINDOW_MS=0`.
- Performance bench numbers committed to [`tests/perf/`](../../tests/perf/) so future regressions are catchable.
- This PRD's status updated to `Shipped`; [00-roadmap.md](00-roadmap.md) status checklist ticked for P4.
- [P16](00-roadmap.md#phase-4--targeted-decoupling) PRD can now reference the established `INSERT … RETURNING` pattern.
