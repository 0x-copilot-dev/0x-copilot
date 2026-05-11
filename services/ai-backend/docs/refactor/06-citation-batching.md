# Refactor PRD — Batch Citation Ingestion (Phase 2)

**Status:** PR1 shipped (infrastructure + FE handler); PR2 shipped (projector switch behind `RUNTIME_BATCH_SOURCE_INGESTION` flag, default off)
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §4.5](../architecture/refactor-audit.md#45-sequential-citation-ingestion)
**Roadmap:** [00-roadmap.md](00-roadmap.md) → P7
**Related flow:** [f5-citations.puml](../architecture/f5-citations.puml)

> **Implementation note (May 2026).** The original PRD assumed three things
> that turned out to be wrong under code inspection. See
> [§11 Implementation post-mortem](#11-implementation-post-mortem) for the
> full divergence list. The sub-sections below were updated to match what
> actually shipped in PR1.

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

- Does `CitationProjectingMcpMiddleware` exist as a single file today, or is the projection logic spread? **Resolved**: `cite_mcp.py` is a 36-LOC alias for `CitationProjector`; real logic in [`citation_projection.py`](../../src/agent_runtime/capabilities/citation_projection.py).
- What's the current event-stream consumption pattern on the frontend Sources pane — event-driven or aggregate-poll? **Resolved**: event-driven via [`citationReducer.ts`](../../../../apps/frontend/src/features/chat/chatModel/citationReducer.ts) + [`sourcesReducer.ts`](../../../../apps/frontend/src/features/chat/chatModel/sourcesReducer.ts). Both got `sources_ingested` branches in PR1.
- What's the realistic max source count from a single MCP tool call? **Resolved**: bounded by `CitationProjector.Limits.PER_RESULT_MAX = 25` per result, `CitationLedger._Limits.PER_RUN_MAX = 50` per run total. Multi-row INSERT capped at 50 × 14 = 700 placeholders, well under asyncpg's 32767 limit.
- Does the `tool_call_id`-based ordinal idempotency apply to the citation path? **Resolved (and corrected the PRD)**: NO. That ordinal is allocated by `ConversationOrdinalAllocator` for tool-call ordinals (`[[N]]`), not citation ordinals (`[c1]`). Citation ordinals are allocated in-memory in `CitationLedger`. See [§11](#11-implementation-post-mortem).

---

## 11. Implementation post-mortem

The original PRD was written from the audit + flow diagrams without code reads. Pre-flight verification (May 2026) found three substantive divergences. Documenting here so future readers don't re-walk the same ground.

### Divergence 1: ordinal allocator is NOT in the citation path

**PRD claimed**: every cited source triggers `ConversationOrdinalAllocator.record` → `CitationStorePort.insert_or_get` → `SOURCE_INGESTED` event (3 things per source).

**Reality** ([citations.py:113-164](../../src/agent_runtime/capabilities/citations.py#L113-L164)): the citation path is `_store.insert_or_get` + `producer.append_api_event`. The ordinal is `len(self._cache) + 1` — purely in-memory.

`ConversationOrdinalAllocator` is a different system: it allocates **tool-call ordinals** (`[[N]]`) per tool dispatch, not citation ordinals (`[c1]`) per cited source. It's bound separately in `runtime_worker/handlers/run.py` and `approval.py`, and consumed by `CitationResolver` watching `[[N]]` markers in streamed text.

**Impact on PR scope**: dropped the planned `ConversationToolOrdinalStorePort.record_many` addition. Out of scope.

### Divergence 2: latent sync/async bug in `insert_or_get`

The Port declared `def insert_or_get` (sync). In-memory adapter implemented sync. Postgres adapter implemented `async def insert_or_get`. The CitationLedger called `self._store.insert_or_get(record)` without `await`.

**Effect in production**: the Postgres path was setting `persisted` to a coroutine (never awaited), then dereferencing `.ordinal` would have raised `AttributeError`. Either citations weren't actually firing on Postgres, or some intermediate path was masking it. We didn't dig further — the fix came for free from the port replacement.

**Impact on PR scope**: replacing `insert_or_get` with `insert_many_or_get` (uniformly `async def` on port and both adapters) fixed the latent bug as a side effect. The single-source caller (`register`) now also goes through the async port, eliminating the bug.

### Divergence 3: frontend impact understated

**PRD claimed**: "FE handler additive: needs handler for `SOURCES_INGESTED`."

**Reality**: the FE has a deeper citation pipeline than the PRD acknowledged — [`citationReducer.ts`](../../../../apps/frontend/src/features/chat/chatModel/citationReducer.ts), [`sourcesReducer.ts`](../../../../apps/frontend/src/features/chat/chatModel/sourcesReducer.ts), [`citationsRegistry.ts`](../../../../apps/frontend/src/features/chat/chatModel/citationsRegistry.ts), [`citationStore.invariant.test.ts`](../../../../apps/frontend/src/features/chat/chatModel/citationStore.invariant.test.ts), [`README.md`](../../../../apps/frontend/src/features/chat/chatModel/README.md), and a cross-store invariant ("dual citation store — deliberate, enforced by test"). Adding `sources_ingested` required updating each of these.

**Impact on PR scope**: PR1 grew to ~14 files instead of the ~6 the PRD anticipated. Test count grew accordingly.

### What shipped in PR1

**Backend:**

- [`agent_runtime/persistence/ports.py`](../../src/agent_runtime/persistence/ports.py) — `CitationStorePort.insert_or_get` (sync) replaced with `insert_many_or_get` (async).
- [`runtime_adapters/in_memory/citation_store.py`](../../src/runtime_adapters/in_memory/citation_store.py) — async `insert_many_or_get` implementation.
- [`runtime_adapters/postgres/runtime_api_store.py`](../../src/runtime_adapters/postgres/runtime_api_store.py) — async `insert_many_or_get`: one multi-VALUES `INSERT ... ON CONFLICT DO NOTHING` + one `SELECT ... WHERE ... IN (...)`. **Two DB round trips for any batch size.**
- [`runtime_api/schemas/common.py`](../../src/runtime_api/schemas/common.py) — `RuntimeApiEventType.SOURCES_INGESTED = "sources_ingested"`.
- [`runtime_api/schemas/events.py`](../../src/runtime_api/schemas/events.py) — wired SOURCES_INGESTED at 4 sites: payload allow-list, activity_kind tool bucket, display title (`sources_cited_title(N)`), status COMPLETED. Added `_sources_ingested_payload` helper; refactored `_source_ingested_payload` to share a `_safe_citation_ref` static helper (same behavior, no duplication).
- [`agent_runtime/api/constants.py`](../../src/agent_runtime/api/constants.py) — `Messages.Event.sources_cited_title(count)` + `Messages.Event.SOURCES_INGESTED` constant.
- [`agent_runtime/capabilities/citations.py`](../../src/agent_runtime/capabilities/citations.py) — added `register_many` (emits one `sources_ingested` per call) + `_register_internal` (cache-check + bulk-persist, used by both APIs); refactored `register` to delegate. **Critical detail**: `_register_internal` dedupes in-batch duplicates via a local `(connector, doc_id) → record` map before allocating an ordinal — a unit test caught this; would have shipped a bug emitting the same record twice in the event payload.

**API-types:**

- [`packages/api-types/src/index.ts`](../../../../packages/api-types/src/index.ts) — `SourcesIngestedPayload` interface, `isSourcesIngestedPayload` type guard, `"sources_ingested"` enum entry, `EventTypeToPayload` map entry.

**Frontend:**

- [`apps/frontend/src/features/chat/chatModel/citationReducer.ts`](../../../../apps/frontend/src/features/chat/chatModel/citationReducer.ts) — handles `sources_ingested` branch via `upsertCitations`.
- [`apps/frontend/src/features/chat/chatModel/sourcesReducer.ts`](../../../../apps/frontend/src/features/chat/chatModel/sourcesReducer.ts) — extracted `mergeOne` helper; handles batch by iterating and merging.
- [`apps/frontend/src/features/chat/chatModel/README.md`](../../../../apps/frontend/src/features/chat/chatModel/README.md) — documents that the dual-store invariant covers both event shapes.

**Tests:**

- Backend: 17 new tests for `register_many` (idempotency, in-batch dedup, mixed cache hits, cap behavior, mixed singular+batched calls, payload allow-list, display-title parametrization). 3 existing tests in `test_workspace_feed_stores.py` migrated from sync `insert_or_get` to async `insert_many_or_get`.
- API-types cross-contract test (`test_typescript_runtime_event_constants_match_backend_enums`) caught the BE/FE drift mid-implementation; passes after api-types update.
- Frontend: extended `citationReducer.test.ts`, `sourcesReducer.test.ts`, `citationStore.invariant.test.ts` with `sources_ingested` cases (parametrized invariant test re-runs across both event shapes).

**Verification:**

- 1027 backend tests pass; 0 regressions in citation/event-related trees.
- 762 frontend tests pass; 0 regressions.
- api-types + frontend typechecks clean.

### What did NOT ship in PR1

The projector still calls `register` per-source. **`SOURCES_INGESTED` is wired end-to-end but not yet emitted by anyone in production.** PR1 confirms the wire shape works without changing user-visible behavior.

### PR2 plan (next)

Single change in [`agent_runtime/capabilities/citation_projection.py`](../../src/agent_runtime/capabilities/citation_projection.py): replace the per-source loop at lines 99-102 with one `await ledger.register_many(sources[: cls.Limits.PER_RESULT_MAX])` call. After PR2 lands:

- Each MCP tool result emits **one** `sources_ingested` event instead of N `source_ingested` events.
- Two DB round trips total for the batch instead of N.
- The latency win finally materializes.

PR2 is small (one file, ~5 lines) but should ship behind a feature flag (`RUNTIME_BATCH_SOURCE_INGESTION`) so it can be flipped in staging first. Skipped in PR1 because the wire shape needed end-to-end validation first.

### What shipped in PR2

**Backend:**

- [`agent_runtime/settings.py`](../../src/agent_runtime/settings.py) — added `RuntimeExecutionSettings.batch_source_ingestion: bool = False` field + `BATCH_SOURCE_INGESTION = "RUNTIME_BATCH_SOURCE_INGESTION"` env constant + `from_env` parse. Defaults `false` so PR2 ships dark.
- [`agent_runtime/capabilities/citations.py`](../../src/agent_runtime/capabilities/citations.py) — added `batch_enabled: bool = False` parameter to `CitationLedger.__init__`, exposed as `batch_enabled` property. The flag changes only which method the projector picks; both `register` and `register_many` share `_register_internal`, so ordinals, idempotency, and cap behavior are identical.
- [`runtime_worker/handlers/run.py`](../../src/runtime_worker/handlers/run.py) — `_bind_citation_ledger` now passes `batch_enabled=self.settings.execution.batch_source_ingestion` into the ledger constructor (one-line change at the composition site).
- [`agent_runtime/capabilities/citation_projection.py`](../../src/agent_runtime/capabilities/citation_projection.py) — `CitationProjector.project` now hoists the `tool_call_id` decoration out of both branches, then either calls `register_many` once (flag on) or loops `register` per source (flag off). Best-effort degradation paths (no ledger, unknown shape, exception) remain unchanged.

**Tests:**

- New `TestProjectorBatched` class in [`tests/unit/agent_runtime/mcp/test_cite_mcp.py`](../../tests/unit/agent_runtime/mcp/test_cite_mcp.py) — opts into `batch_enabled=True` via the fixture mixin and asserts the new behavior:
  - Multi-result tool result emits exactly one `sources_ingested` event with N citations in input order.
  - Single-source under flag still uses `sources_ingested` (consistent batching contract).
  - Unrecognized shape produces no event (degradation parity with legacy path).
  - `tool_call_id` is attached to every batched source (parity with legacy `model_copy` decoration).
  - `PER_RESULT_MAX = 25` cap still applied before the ledger sees the input (a 30-result tool truncates to 25 stored rows, identical to legacy).
- The existing 7 tests in `test_cite_mcp.py` (which assert N events for the legacy per-source path) remain unchanged and pass — they exercise `batch_enabled=False` (the default fixture).
- Mixin signature extended with `batch_enabled: bool = False` kw-only, so existing test classes don't change at all.

**Verification:**

- 63 P7-related tests pass: `test_cite_mcp.py` + `test_citations.py` + `test_runtime_settings.py` + `test_workspace_feed_stores.py`.
- 1031 wider backend tests pass across `tests/unit/agent_runtime` + `tests/unit/runtime_api`; zero regressions.
- The flag's default `false` means production behavior is unchanged on this PR's merge — operator must explicitly set `RUNTIME_BATCH_SOURCE_INGESTION=true` to take the new path.

### Rollout plan (PR2 → production)

1. **PR2 merges.** Default behavior unchanged. CI green.
2. **Staging:** set `RUNTIME_BATCH_SOURCE_INGESTION=true` on the worker process. Run a research-heavy conversation with 3+ MCP tool calls; observe via SSE that one `sources_ingested` event arrives per tool result instead of N `source_ingested` events. Confirm Sources tab + chip resolution still render correctly.
3. **Latency check on staging:** measure end-to-end turn time for a 20-source MCP result; expect ≥300ms improvement vs. baseline (per the PRD's §1 estimate).
4. **Production:** flip `RUNTIME_BATCH_SOURCE_INGESTION=true` on prod workers.
5. **Cleanup PR (P7-PR3, eventual):** after 1–2 weeks of clean prod operation, remove the flag and the legacy per-source branch in `citation_projection.py`. Keep the `register` method on the ledger (still used by `citation_capturing_tool` and provider grounding paths). Mark `register_many` as the canonical batched API.

### Known limitations (intentional)

- `citation_capturing_tool.py` still calls `CitationProjector.project` per single source — those paths are unaffected by the flag (a single source goes through the same `_register_internal` either way; the only change is which event type fires). Migrating those callers is out of P7 scope; revisit in P14 (citation infrastructure consolidation).
- Provider grounding (`CitationStreamPipeline`) still calls `ledger.register` per chunk — same reasoning. Per-chunk delivery is the natural shape of provider streaming and doesn't benefit from batching.

---

_Phase 2 PR. Lands after [P5 async-only ports](01-async-only-ports.md) so the new batch port methods are added only to the async surface._
