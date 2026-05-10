# Refactor PRD — Batch Citation Ingestion (Phase 2)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §4.5](../architecture/refactor-audit.md#45-sequential-citation-ingestion)
**Roadmap:** [00-roadmap.md](00-roadmap.md) → P7
**Related flow:** [f5-citations.puml](../architecture/f5-citations.puml)

---

## 1. Problem

Per [`f5-citations.puml`](../architecture/f5-citations.puml), MCP middleware ingests cited sources sequentially. For each cited source in a tool result:

1. `ConversationOrdinalAllocator.record(conversation_id, tool_call_id, conversation_ordinal=N, tool_name)` — DB write to `ConversationToolOrdinalStorePort` (idempotent on `tool_call_id`).
2. `CitationLedger.ingest(SourceRef{connector, doc_id, url, title})` — DB write to `CitationStorePort.insert_or_get` (idempotent on `(run_id, connector, doc_id)`).
3. Append `SOURCE_INGESTED` event via `RuntimeEventProducer` (per-event: INSERT runtime_events + UPDATE agent_runs.latest_sequence_no + NOTIFY).

A tool returning 20 cited sources triggers **20 sequential idempotent inserts and 20 sequential event appends** before the LLM can take its next turn. Research-heavy tool calls (Linear search, Notion query, Jira list) routinely return 10+ sources.

### Symptoms (today)

Estimated wall-clock cost on a research turn with 3 MCP tool calls of 15 sources each (45 sources total):

- 45 ordinal inserts + 45 citation inserts = 90 DB write round trips.
- 45 `SOURCE_INGESTED` events × 3 DB ops each (per [refactor-audit §4.3](../architecture/refactor-audit.md#43-per-event-db-amplification)) = 135 DB ops.
- Total: ~225 sequential DB round trips before the synthesis turn can begin.
- At a conservative 2ms per round trip on a healthy Postgres in the same VPC, that's ~450ms of pure ingestion latency on top of the model's own work.
- The `SOURCE_INGESTED` events also flow through SSE — the UI receives 45 individual source-card events that almost certainly render as a single Sources pane refresh.

### Why it's a problem

- Latency: half a second of DB round trips for one synthesis turn is user-visible "thinking" time the model isn't actually thinking.
- Event volume: 45 events for one tool result inflates the run's sequence_no count, making `replay_events` more expensive on resume.
- Ordering coupling: the per-source loop allocates an ordinal _and_ writes a citation _and_ emits an event in lockstep. The ordinal cursor is currently the only thing that needs to be sequential; the writes can be batched.

### What this is NOT

- Not a change to citation ordinals. Conversation-scoped ordinal namespace persists across turns and across subagents. Ordinal allocation order must continue to match the order the model references sources.
- Not a redesign of `CitationLedger` / `CitationRegistry` / `ConversationOrdinalAllocator`. The citation infrastructure consolidation (8 files → 3) is a separate refactor — see [P14](00-roadmap.md#phase-4--targeted-decoupling).
- Not a change to provider grounding extraction (`CitationStreamPipeline`). That path also feeds the ledger but is per-chunk, not per-source-bundle, and is handled separately.
- Not a change to the workspace Sources tab read path (`SourceStorePort.aggregate_for_conversation`).

---

## 2. Goal and non-goals

### Goal

Collapse N sequential ordinal inserts + N sequential citation inserts + N `SOURCE_INGESTED` events into one batch insert per port + one `SOURCES_INGESTED` event when the MCP middleware processes a single tool result. Preserve every idempotency invariant and ordinal ordering.

### Non-goals

- Reduce the citation file count or merge `CitationLedger` / `CitationRegistry`. (See P14.)
- Change the `[[N]]` marker projection format inside MCP result text. (Marker placement and format are unchanged.)
- Change provider grounding extraction. (`CitationStreamPipeline` keeps its per-chunk pattern.)
- Add new public event types beyond `SOURCES_INGESTED` (plural). The singular `SOURCE_INGESTED` is retained for non-MCP paths and one-off captures during a transition window; it can be retired in P14.

### Success criteria

- New port methods exist: `CitationStorePort.insert_many_or_get(records: Sequence[CitationRecord]) -> Sequence[CitationRecord]` and `ConversationToolOrdinalStorePort.record_many(records: Sequence[ConversationToolOrdinalRecord]) -> None`. Both implemented for in-memory + Postgres adapters.
- New event type `SOURCES_INGESTED` (plural) added to `RuntimeApiEventType`. Payload shape: `{ "sources": [{ ordinal, connector, doc_id, url, title, tool_call_id }, ... ] }`.
- `CitationProjectingMcpMiddleware` (per [refactor-audit §2.2](../architecture/refactor-audit.md#22-8-files-of-citation-infrastructure)) collects all sources from one tool result, calls the batch port methods once, and emits one `SOURCES_INGESTED` event.
- Ordinal allocation order matches the order sources appear in the MCP result, identical to today.
- Idempotency invariants pass: re-running the same tool result produces the same ordinals and the same citation rows (ON CONFLICT DO NOTHING on `(run_id, connector, doc_id)`).
- Per-source `SOURCE_INGESTED` path remains available for non-MCP callers (provider grounding, capturing tool) — feature-equivalent.
- Frontend Sources tab renders identically. (Verify before merge — see [§7](#7-frontend-coordination).)
- Latency benchmark: synthesis turn following a 3-tool / 45-source research turn is ≥300ms faster end-to-end on staging vs. baseline (Phase 1 measurement).

---

## 3. Systems touched

### 3.1 Files modified

| File                                                                                                                            | Change                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/persistence/ports.py`](../../src/agent_runtime/persistence/ports.py)                                            | Add `insert_many_or_get` to `CitationStorePort`; add `record_many` to `ConversationToolOrdinalStorePort` |
| [`runtime_api/schemas/events.py`](../../src/runtime_api/schemas/events.py)                                                      | Add `SOURCES_INGESTED` to `RuntimeApiEventType`; add payload schema (Pydantic model)                     |
| [`agent_runtime/capabilities/mcp/middleware/cite_mcp.py`](../../src/agent_runtime/capabilities/mcp/middleware/cite_mcp.py)      | Switch from per-source loop to batch path                                                                |
| [`agent_runtime/capabilities/citation_resolver.py`](../../src/agent_runtime/capabilities/citation_resolver.py) (CitationLedger) | Add `ingest_many(sources: Sequence[SourceRef]) -> Sequence[CitationRecord]`                              |
| [`agent_runtime/capabilities/conversation_ordinals.py`](../../src/agent_runtime/capabilities/conversation_ordinals.py)          | Add `record_many(...) -> Sequence[OrdinalBinding]` preserving allocation order                           |
| [`agent_runtime/api/events.py`](../../src/agent_runtime/api/events.py) (RuntimeEventProducer) and presentation projector        | Recognize `SOURCES_INGESTED` for `activity_kind` mapping (`activity_kind=event` or its own bucket)       |

### 3.2 Files added (adapters)

- In-memory implementations of the new batch methods (extend the existing in-memory citation + ordinal stores).
- Postgres implementations: a single `INSERT ... ON CONFLICT DO NOTHING RETURNING ...` for citations and a single multi-row insert for ordinals.

### 3.3 Frontend

- `apps/frontend/` (or wherever the Sources pane lives): handle `SOURCES_INGESTED` by iterating its `payload.sources` array exactly as if N `SOURCE_INGESTED` events had been received. **Ship the frontend handler in the same PR or one before** — never the same release as a backend that emits an event the frontend doesn't recognize.

### 3.4 Postgres SQL shape (sketch)

**Batch citation insert** (idempotent on `(run_id, connector, doc_id)`):

```sql
INSERT INTO runtime_citations (
    run_id, conversation_id, connector, doc_id, url, title,
    tool_call_id, ordinal, created_at
) VALUES
    ($1, $2, $3, $4, $5, $6, $7, $8, NOW()),
    ($1, $2, $3, $4, $5, $6, $7, $8, NOW()),
    ...
ON CONFLICT (run_id, connector, doc_id) DO NOTHING
RETURNING id, run_id, connector, doc_id, ordinal, ...
```

For preserving the binding from input to output (since `RETURNING` only returns inserted rows, not skipped duplicates), follow with a `SELECT` for the same `(run_id, connector, doc_id)` keys to assemble the full ordered output. Or use `INSERT ... ON CONFLICT DO UPDATE SET <noop> RETURNING ...` to force a return for every input row.

**Batch ordinal insert** (idempotent on `tool_call_id`):

```sql
INSERT INTO runtime_conversation_tool_ordinals (
    conversation_id, tool_call_id, conversation_ordinal, tool_name, recorded_at
) VALUES
    ($1, $2, $3, $4, NOW()),
    ...
ON CONFLICT (tool_call_id) DO NOTHING
```

---

## 4. Behaviors to preserve

| Behavior                                                                        | How preserved                                                                               |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Conversation-scoped ordinal namespace across turns AND subagents                | Ordinal allocator unchanged at the algorithmic level; only adds a batch entry point         |
| Ordinal allocation order matches the order sources appear in the MCP result     | `record_many` accepts an ordered sequence and assigns ordinals in input order               |
| Idempotency `(tool_call_id)` for ordinals                                       | `ON CONFLICT (tool_call_id) DO NOTHING` in batch insert                                     |
| Idempotency `(run_id, connector, doc_id)` for citations                         | `ON CONFLICT (run_id, connector, doc_id) DO NOTHING` in batch insert                        |
| `[[N]]` marker projection into MCP result text                                  | Projection logic unchanged; receives the same `(ordinal → source)` map as before            |
| Sealed snapshot at `FINAL_RESPONSE` via `CitationStorePort.list_for_run`        | Reads still see one row per `(run_id, connector, doc_id)`; batch writes don't change schema |
| Reconstruction from `CitationStorePort.list_for_run` on resume / crash recovery | Same — batched writes commit atomically, partial commits don't happen                       |
| Workspace Sources tab via `SourceStorePort.aggregate_for_conversation`          | Read path untouched                                                                         |
| Per-source `SOURCE_INGESTED` event emission for non-MCP paths                   | Singular event type retained; only MCP middleware switches to plural                        |
| `RuntimeEventEnvelope.payload` shape for SSE consumers                          | New `SOURCES_INGESTED` payload schema is additive; existing `SOURCE_INGESTED` unchanged     |

---

## 5. Risks

| Risk                                                                                                   | Likelihood | Mitigation                                                                                                                                                                                                      |
| ------------------------------------------------------------------------------------------------------ | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend doesn't handle `SOURCES_INGESTED`, Sources pane goes blank for new tool results               | Medium     | Ship frontend handler one release before backend emits the new event; add a feature flag to gate emission                                                                                                       |
| Batch insert + `ON CONFLICT DO NOTHING` returns fewer rows than input → ordinal-binding drift          | Medium     | Use `INSERT ... ON CONFLICT DO UPDATE SET <noop> RETURNING ...` or follow-up SELECT to reassemble the binding map for every input row                                                                           |
| Batch ordinal insert violates per-row ordinal monotonicity                                             | Low        | Allocate ordinals deterministically before the SQL call (Python-side counter + ordered input); SQL just stores                                                                                                  |
| Postgres parameter limit hit on very large source bundles                                              | Low        | Cap batch size at 500 rows per INSERT; loop the batch if the input exceeds — still O(1) round trips for normal bundles                                                                                          |
| Idempotency on retry is broken because the batch is now atomic but the ordinals were already allocated | Medium     | Allocate ordinals from a per-conversation Postgres sequence (deterministic across retries) OR record ordinals first, then citations, in two batch calls; ordinal allocator already idempotent on `tool_call_id` |
| `SOURCES_INGESTED` event payload is large (45 sources × URL + title) → exceeds redaction CPU budget    | Low        | Verify the redactor handles array payloads efficiently; if not, batch redaction by source rather than per-field                                                                                                 |

---

## 6. Unit testing requirements

### 6.1 New port method behavior

- `CitationStorePort.insert_many_or_get`:
  - Empty input returns empty output.
  - All-new sources: every input has a matching output row, ordinals match input order.
  - All-duplicate sources (same `(run_id, connector, doc_id)`): every input still gets a binding from the existing row; no duplicates created.
  - Mixed new + duplicate: ordering preserved in output; idempotency holds.
  - Concurrent batch inserts of overlapping sets: both succeed, total row count is the union, no duplicates.

- `ConversationToolOrdinalStorePort.record_many`:
  - Ordinal sequence is preserved in input order.
  - Re-running with the same `tool_call_id`s is a no-op (idempotency).
  - Mixed new + duplicate `tool_call_id`s: only new rows inserted, no error raised.

### 6.2 Middleware behavior

- `CitationProjectingMcpMiddleware` invoked on a tool result with N sources:
  - Calls `record_many` exactly once.
  - Calls `insert_many_or_get` exactly once.
  - Emits exactly one `SOURCES_INGESTED` event with `payload.sources.length == N`.
  - Projected `[[N]]` markers in the result text match the ordinals returned by the allocator.

- Re-invocation with the same tool result (retry / approval-resume):
  - Allocator returns the same ordinals.
  - Citation store has no new rows.
  - Exactly one `SOURCES_INGESTED` event again — idempotency upstream of the producer is the producer's job; downstream consumers handle dupes via `event_id`.

### 6.3 Latency assertion

- Add a benchmark test under `tests/perf/` that constructs a fake MCP result with 50 sources and asserts the middleware's wall-clock time is below a threshold (e.g. 50ms in CI). Compare to a baseline test using the per-source path. Skip in normal CI; run on the perf job.

### 6.4 Backwards-compatibility

- Existing tests that assert `SOURCE_INGESTED` events fire from non-MCP paths (provider grounding, capturing tool) must still pass — confirm those paths are untouched.
- `CitationStorePort.list_for_run` returns identical results whether the rows were inserted via `insert_or_get` or `insert_many_or_get`.

---

## 7. Frontend coordination

The new event type **must** be handled by the frontend before this PR can ship to users. Coordinate by:

1. **PR A (frontend):** add a handler for `SOURCES_INGESTED` that iterates `payload.sources` and dispatches the existing per-source render logic. Ships in the same release train as PR B.
2. **PR B (backend, this PRD):** adds the event type and switches MCP middleware to emit it. Behind a feature flag `RUNTIME_BATCH_SOURCE_INGESTION=false` initially.
3. **Release:** flip flag to `true` in staging first, monitor the Sources pane on a research-heavy conversation, then flip in prod.
4. **PR C:** remove the flag; remove the per-source path from `CitationProjectingMcpMiddleware` (the per-source path remains in non-MCP callers).

If the frontend renders Sources purely from `SourceStorePort.aggregate_for_conversation` polling and not from the event stream, frontend coordination is just confirming the events don't break anything — no behavior change needed.

**Verify before merge:** grep frontend for `SOURCE_INGESTED` consumption.

---

## 8. Rollback plan

| Failure mode                                                     | Rollback                                                                                                   |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Sources pane breaks                                              | Flip `RUNTIME_BATCH_SOURCE_INGESTION=false`; middleware reverts to per-source path. No DB rollback needed. |
| Ordinal-binding drift (sources missing markers)                  | Same flag flip + investigate `INSERT ... RETURNING` semantics                                              |
| Batch INSERT performance regression on a specific tool's payload | Same flag flip; tune batch size                                                                            |
| `SOURCES_INGESTED` event payload too large for SSE frame         | Cap event payload at K sources; emit multiple `SOURCES_INGESTED` events for very large bundles             |

---

## 9. Implementation order within the PR

1. Add port method signatures (no implementation yet) and `SOURCES_INGESTED` event type.
2. Implement in-memory batch ports.
3. Implement Postgres batch ports.
4. Implement `ingest_many` / `record_many` on `CitationLedger` + `ConversationOrdinalAllocator`.
5. Add the feature flag check in `CitationProjectingMcpMiddleware`.
6. Add tests (port behavior, middleware behavior, idempotency).
7. Frontend handler PR (parallel).
8. Flip flag in staging; observe; flip in prod.
9. Remove flag; remove per-source path from MCP middleware.

---

## 10. Open questions

- Does `CitationProjectingMcpMiddleware` exist as a single file today, or is the projection logic spread? Confirm before estimating work.
- What's the current event-stream consumption pattern on the frontend Sources pane — event-driven or aggregate-poll? Determines [§7](#7-frontend-coordination) urgency.
- Is the `tool_call_id`-based ordinal idempotency safe under the batch path, or does the per-tool-call ordinal cursor live in `CitationLedger` state that needs to flush atomically with the SQL write? If the latter, the batch path needs an explicit transaction.
- What's the realistic max source count from a single MCP tool call (Linear search, Notion query)? Confirms the 500-row batch cap is sufficient.
- Does the Postgres pool support multi-row INSERT with placeholder limits? If using asyncpg, the parameter limit is 32767; well above any realistic batch.

---

_Phase 2 PR. Lands after [P5 async-only ports](01-async-only-ports.md) so the new batch port methods are added only to the async surface._
