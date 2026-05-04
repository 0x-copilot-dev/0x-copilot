# Spec: B5 — `/context` slash command (per-conversation usage view)

**Roadmap PR:** [docs/roadmap/19-b5-context-command.md](../../../../../docs/roadmap/19-b5-context-command.md) — full functional + technical spec.
**Wave:** 5 (Usage UX + Budgets). **Depends on:** B1, B2, B3, B4.

This document is the _implementation contract_ — what we add, what we reuse, what we deliberately skip. The roadmap PR is the source of truth for behavior.

## Architecture

A new read-only endpoint `GET /v1/agent/conversations/{conversation_id}/context` joins three already-persisted streams:

```
runtime_run_usage   (B1, latest run for the conversation, cost stamp from B3)
   └─ runtime_model_call_usage   (B2, per-LLM-call rows for that run)
   └─ runtime_compression_events (existing since 0001, never queried until now)
   └─ model_pricing.context_window_tokens (B3)
```

The frontend slash command opens a side panel — **no message is sent, no run is started**. All percentage values are integers (0..100) computed server-side; the UI never re-derives them.

## Module boundaries

- **Read ports (new methods on `AsyncPersistencePort`)**:
  - `query_latest_run_usage_for_conversation(org_id, user_id, conversation_id) -> RuntimeRunUsageRecord | None`
  - `query_compression_events_for_run(org_id, run_id) -> Sequence[CompressionEventRecord]`
- **Service**: extend `agent_runtime/api/usage_service.py` with a `ConversationContextBuilder` classmethod — pure; no I/O. Takes pre-fetched records, returns the response model.
- **Route**: `RuntimeApiRoutes.get_conversation_context` (new method on the existing class), wired beside `/conversations/{id}/messages`.
- **Frontend**: register `/context` slash command in `AssistantComposer`, render side panel via design-system `Card` / `Badge` / progress primitive.

Reuses `pricing_catalog.lookup` for `context_window_tokens` (no new pricing path).

## Pydantic contracts

```python
class ContextWindowSummary(RuntimeContract):
    provider: str
    name: str
    context_window_tokens: NonNegativeInt | None  # None = model not in pricing

class ContextCurrentSlice(RuntimeContract):
    last_run_id: str | None
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    available_tokens: NonNegativeInt | None = None  # None when window unknown
    headroom_pct: int | None = Field(default=None, ge=0, le=100)

class ContextCallRow(RuntimeContract):
    event_id: str
    model_name: str
    input: NonNegativeInt
    output: NonNegativeInt
    cached_input: NonNegativeInt
    task_id: str | None = None

class ContextSubagentRow(RuntimeContract):
    subagent_id: str
    name: str
    total: NonNegativeInt
    call_count: NonNegativeInt

class ContextCompressionRow(RuntimeContract):
    before: NonNegativeInt
    after: NonNegativeInt
    strategy: str
    at: datetime

class ContextBreakdown(RuntimeContract):
    by_call: tuple[ContextCallRow, ...] = ()
    by_subagent: tuple[ContextSubagentRow, ...] = ()
    compression_events: tuple[ContextCompressionRow, ...] = ()

class ConversationContextResponse(RuntimeContract):
    model: ContextWindowSummary
    current: ContextCurrentSlice
    breakdown: ContextBreakdown
```

## Edge cases

- **Empty conversation** (no completed runs): return `current.last_run_id = None` and zero breakdown. Headroom uses the configured default model from `RuntimeSettings` so the UI can still render the gauge label.
- **Model not in `model_pricing`**: `context_window_tokens = None`, `headroom_pct = None`. UI renders "unknown" state.
- **PII-purged run** (`pii_purged_at IS NOT NULL`): excluded from `current` slice (consistent with B4 per-user policy).
- **Subagent attribution**: `by_subagent` aggregates `runtime_model_call_usage` by `subagent_id`. Rows with `subagent_id IS NULL` go into `by_call` only.
- **Reconciliation invariant**: `sum(by_call.input + by_call.cached_input) == current.input + current.cached_input` for a single run. Tested with a fixture.

## Security

- Standard `(org_id, user_id, conversation_id)` scoping. Foreign-conversation lookup → **404, not 403** (so we don't leak existence).
- No PII in the response payload — only token counts, model names, and event IDs that the same user already sees on the timeline.

## Observability

- Counter `context_endpoint_request_total{outcome}` (outcome ∈ `ok`, `not_found`, `empty`).
- Reuse existing route logging via `RuntimeApiRoutes`.

## Tests

- **Unit (service)**: `ConversationContextBuilder` fixtures: empty conversation, multi-call run, subagent run, compression event, unknown-model fallback, reconciliation invariant.
- **Integration (HTTP)**: 404 for foreign-tenant conversation; 200 with shape on populated conversation.
- **Frontend (vitest)**: slash command opens panel without dispatching a send; renders the response shape verbatim; never re-derives `headroom_pct`.

## What we deliberately skip

- Forecasting tokens for the _next_ run (spec out of scope).
- Cross-conversation aggregate (B6 covers `/usage`).
- Caching the response — runs settle in seconds, the panel is opened on demand, and the joins all hit the same `(org_id, run_id)` hot path. Revisit only if telemetry shows >50ms p99.
