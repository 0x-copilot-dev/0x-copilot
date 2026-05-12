# Sub-PRD 01c — Single UsageRecorder Boundary

**Status:** Shipped 2026-05-11
**Parent:** [01-usage-capture-and-attribution.md](01-usage-capture-and-attribution.md)
**Position in plan:** P11.7.c (third of four sub-PRDs)
**Depends on:** [01a — Normalized token shape](01a-usage-normalized-token-shape.md) ✅ shipped, [01b — Carry attribution](01b-usage-attribution-context.md) ✅ shipped
**Risk:** Medium. Touches the run-completion write path; preserves a fail-soft contract.

> **What this PR is.** Today `handlers/run.py` holds two parallel writer methods that each issue four port calls (insert row + lookup pricing + compute cost + update cost). The shape is repeated, the pricing lookup is duplicated, and the per-call writer is called _from inside_ the run-level writer — coupling them. 01c collapses every usage write under one `UsageRecorder` Protocol with two methods (`record_call`, `record_run`). The Protocol is the boundary; the production impl owns row-write + pricing-lookup + cost-stamp; tests get an in-memory fake; dev gets a null sink for replays. `handlers/run.py` becomes a coordinator that calls the recorder + threads its result into the budget charger.

---

## 1. Problem

### 1.1 Two parallel writers with duplicated structure

[`handlers/run.py:706-784`](../../src/runtime_worker/handlers/run.py) `_record_run_usage` and [`handlers/run.py:786-845`](../../src/runtime_worker/handlers/run.py) `_record_per_call_usage` each do the same dance:

```
build record(s)
INSERT row(s) via persistence.record_*_usage(...)
try:
    pricing = await self.pricing_catalog.lookup(provider, model, region, at)
    if pricing is not None:
        cost = CostCalculator.compute(input, output, cached_input, pricing)
        await self.persistence.update_*_usage_cost(id, cost, pricing.id, pricing.version)
except Exception: log + swallow
```

Two pricing lookups per run. Different `at=` timestamps (`completed_at` vs `datetime.now()`) — the catalog caches at minute resolution so they usually collide, but the contract is sloppy: a clock crossing the minute boundary mid-run produces two different `pricing_version` stamps within the same run. Two try/except patterns that swallow at slightly different granularities.

The per-call writer is also called _from inside_ the run-level writer at line 748 — they're not independent boundaries, they're a coupled pair.

### 1.2 Four port methods caller-managed

`agent_runtime/api/ports.py:354-390` defines:

- `record_run_usage(record)` — INSERT, ON CONFLICT (run_id) DO NOTHING
- `record_model_call_usage(record)` — INSERT, no ON CONFLICT (caller dedups by row id == message_id)
- `update_run_usage_cost(run_id, cost, pricing_id, pricing_version)` — UPDATE
- `update_model_call_usage_cost(usage_id, cost, pricing_id, pricing_version)` — UPDATE

The handler orchestrates all four. The two INSERT methods + their two UPDATE methods are a single domain operation ("persist a usage observation with its cost") split four ways across the port surface. Callers must know which to call in which order. Substitution principle violated: a test fake has to implement all four to mirror behavior.

### 1.3 Budget charger is downstream of the cost write, but reads its result from a local

[`handlers/run.py:779-784`](../../src/runtime_worker/handlers/run.py) calls `_charge_budgets(observed_micro_usd=cost_micro_usd_observed, ...)`. The `cost_micro_usd_observed` local is set inside the try/except above. If the writer were a clean boundary, the charger's input would come from the recorder's _typed return value_ rather than a mutable local that the surrounding scope reads back.

### 1.4 New token kinds (01a) are captured but never priced

`RuntimeModelCallUsageRecord` and `RuntimeRunUsageRecord` carry `reasoning_tokens`, `cache_creation_input_tokens`, `audio_input_tokens`, `audio_output_tokens` after 01a. `CostCalculator.compute(input_tokens, output_tokens, cached_input_tokens, pricing)` does not consume them. `ModelPricingRecord` has no rate columns for them either. Cost on rows with reasoning / prompt-cache-write / audio workloads silently undercounts.

This is a real defect for o-series + Anthropic prompt-cached workloads but it is **scoped to P12** (pricing-source-from-LiteLLM). 01c will surface the gap by introducing a clear pricing boundary; the rate columns + LiteLLM source are P12's job. See §3 non-goals.

### 1.5 `summarization.py` is the documented D4 silent leak — but it's dead code today

The audit confirmed `agent_runtime/context/memory/summarization.py` is unused in production: the SDK closure surface exists but no caller in `services/ai-backend/src` ever passes a non-None `summarizer=`. So D4 isn't actively leaking — but the structural concern remains. When summarization is wired (future feature), it MUST route through the recorder; the architectural boundary needs to exist before the feature lands so the contract is enforced from day one.

---

## 2. Goals

1. **Single boundary for usage writes.** One `UsageRecorder` Protocol exposed at `agent_runtime/observability/usage_recorder.py`. Two methods: `record_call(record)` and `record_run(record)`. Every usage write in the codebase goes through it.
2. **Recorder owns: row-write + pricing-lookup + cost-stamp.** Callers don't know about pricing or the persistence ports — they hand the recorder a built record and read a typed result.
3. **Typed result returned to callers.** `UsageRecordingResult` carries `cost_micro_usd`, `pricing_id`, `pricing_version` (each None when pricing was unavailable). The handler threads `result.cost_micro_usd` into the budget charger — no implicit local-scope passing.
4. **Substitution.** Three impls: `PostgresUsageRecorder` (prod), `InMemoryUsageRecorder` (tests — captures records), `NullUsageRecorder` (dev / replay — accepts and discards).
5. **Fail-soft preserved.** The recorder's public methods do not propagate exceptions. Run lifecycle must not break because a usage row couldn't be persisted.
6. **Same pricing snapshot per run.** Both `record_call` and `record_run` calls for a given run share the same `at=completed_at` so a price change crossing the minute boundary doesn't stamp two different versions in one run.
7. **Architectural scaffold for summarization.** A `SummarizationUsageRecorder` helper (thin wrapper) documents the contract for future summarization wiring. No production summarization is enabled in 01c — the helper exists so the feature, when enabled, is forced through the recorder.

## 3. Non-goals

- **Extending `CostCalculator` for new token kinds.** Today's signature stays. New kinds (reasoning/cache_creation/audio) contribute 0 cost until P12 adds rate columns to `ModelPricingRecord` + a LiteLLM-sourced catalog.
- **Adding rate columns to `runtime_model_pricing`.** That's a P12 migration paired with the LiteLLM seed.
- **Wiring a production summarization path.** Summarization is dead code today; lighting it up is a product decision, not a refactor.
- **Changing the `MODEL_CALL_COMPLETED` wire payload.** The recorder is server-side; FE sees the same SSE shape.
- **Removing the four port methods.** The recorder uses them. They stay on the Protocol as the persistence-side primitives the recorder calls. (Future direction: consolidate them into a single `persist_usage(record, cost_stamp=...)` port method. That's a P12-or-later cleanup.)

## 4. Architecture

### 4.1 The contract

```python
# agent_runtime/observability/usage_recorder.py

@dataclass(frozen=True)
class UsageRecordingResult:
    """Outcome of one recorder write.

    - ``cost_micro_usd`` is the cost stamped on the row, or ``None``
      when pricing was unavailable (catalog miss) or the row write
      itself failed.
    - ``pricing_id`` / ``pricing_version`` mirror the row's snapshot
      columns; ``None`` whenever ``cost_micro_usd`` is ``None``.
    """
    cost_micro_usd: int | None = None
    pricing_id: str | None = None
    pricing_version: str | None = None


@runtime_checkable
class UsageRecorder(Protocol):
    """Single boundary for persisting LLM token usage + cost.

    All methods are fail-soft: failures log and return a result with
    ``cost_micro_usd is None``. The run lifecycle never breaks because
    a usage row couldn't persist.
    """

    async def record_call(
        self,
        record: RuntimeModelCallUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult: ...

    async def record_run(
        self,
        record: RuntimeRunUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult: ...
```

`pricing_at` is explicit — the caller controls "which point-in-time pricing snapshot do we want." `handlers/run.py` passes `completed_at` for both, so a run is priced against one snapshot regardless of minute-boundary crossings.

### 4.2 Production impl

```python
class PostgresUsageRecorder:
    """Writes to PersistencePort + stamps cost from ModelPricingCatalog.

    Dependencies are injected; the recorder doesn't know how persistence
    or pricing are sourced. Substitution-friendly: a test can hand in
    fakes for either or both.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        pricing_catalog: ModelPricingCatalog,
        logger: logging.Logger | None = None,
    ) -> None: ...

    async def record_call(self, record, *, pricing_at):
        if not await self._safe_insert_call(record):
            return UsageRecordingResult()
        return await self._safe_stamp_call_cost(record, pricing_at=pricing_at)

    async def record_run(self, record, *, pricing_at):
        if not await self._safe_insert_run(record):
            return UsageRecordingResult()
        return await self._safe_stamp_run_cost(record, pricing_at=pricing_at)

    # _safe_* helpers do try/except, log, return False / empty result on failure
```

The insert and the cost-stamp are separate operations because the existing port surface keeps them apart. The recorder owns the orchestration so handlers don't.

### 4.3 Test fake

```python
class InMemoryUsageRecorder:
    """Captures every record for assertion. Computes cost only if a
    pricing catalog was injected; otherwise leaves cost_micro_usd None.
    """

    calls: list[RuntimeModelCallUsageRecord] = field(default_factory=list)
    runs: list[RuntimeRunUsageRecord] = field(default_factory=list)
    results: list[UsageRecordingResult] = field(default_factory=list)
```

Tests assert against `recorder.calls` / `recorder.runs` directly. Today's tests read back from the in-memory persistence store; the fake recorder is a cleaner unit-test surface for paths that just want to verify "the recorder was called with X."

### 4.4 Null impl

```python
class NullUsageRecorder:
    """Accepts and discards. Used in replay paths and dev modes where
    cost stamping is not desired."""

    async def record_call(self, record, *, pricing_at):
        return UsageRecordingResult()

    async def record_run(self, record, *, pricing_at):
        return UsageRecordingResult()
```

### 4.5 Handler integration

`RuntimeRunHandler.__init__` accepts `usage_recorder: UsageRecorder | None = None`. When `None`, the constructor builds a `PostgresUsageRecorder` from the existing `self.persistence` + `self.pricing_catalog` (mirroring the pattern for `BudgetCharger`). Production deploys get the default; tests can swap.

`_record_run_usage` becomes a small coordinator:

```python
async def _record_run_usage(
    self,
    run,
    *,
    metrics,
    completed_at,
    status,
    budget_reservations=(),
):
    usage_record = metrics.to_usage_record(run, completed_at=completed_at, status=status)
    run_result = await self.usage_recorder.record_run(
        usage_record, pricing_at=completed_at,
    )
    call_records = metrics.model_call_usage_records(run, trace_id=run.trace_id)
    for call_record in call_records:
        await self.usage_recorder.record_call(call_record, pricing_at=completed_at)
    await self._charge_budgets(
        run,
        observed_micro_usd=run_result.cost_micro_usd,
        observed_tokens=usage_record.total_tokens,
        reservations=budget_reservations,
    )
```

That's it. No more pricing lookup, no more try/except, no more cost UPDATE — those moved into the recorder.

`_record_per_call_usage` is deleted entirely (its body moved to the recorder's per-call path).

### 4.6 Summarization scaffold

`agent_runtime/observability/usage_recorder.py` exposes a small helper:

```python
class SummarizationUsageRecorder:
    """Architectural boundary for future summarization wiring.

    When ``ContextSummarizationManager.summarize_or_fallback`` is
    enabled in production, the summarizer closure MUST route its LLM
    response through this helper so the recorder gets the row.

    Today: dead code. Documented contract. No production caller.
    """

    def __init__(
        self,
        *,
        recorder: UsageRecorder,
        run: RunRecord,
    ) -> None: ...

    async def record_summarization_call(
        self,
        *,
        provider_response: object,
        message_id: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> UsageRecordingResult:
        """Extract usage from the provider's response, build a
        ``UsageAttributionContext`` with ``purpose=CONTEXT_COMPRESSION``,
        and call ``recorder.record_call``.
        """
```

Once a real summarization caller exists, it imports this helper. The architectural boundary is in place; future enabling the feature is a one-line wire-up.

### 4.7 What stays the same

- The four PersistencePort methods (`record_run_usage`, `record_model_call_usage`, `update_run_usage_cost`, `update_model_call_usage_cost`). The recorder is layered ABOVE them, not in place of them.
- `CostCalculator` — same signature, same math.
- `ModelPricingCatalog` — unchanged.
- `RuntimeRunUsageRecord` / `RuntimeModelCallUsageRecord` Pydantic shapes — untouched (01a's columns stay; 01b's stay).
- `BudgetCharger` — recorder result feeds it; semantics unchanged.
- Fail-soft contract — recorder absorbs every exception.
- Per-run pricing snapshot — `pricing_at=completed_at` for both record_call and record_run.

---

## 5. Files touched (inventory)

### Added

- `agent_runtime/observability/usage_recorder.py` — `UsageRecordingResult`, `UsageRecorder` Protocol, `PostgresUsageRecorder`, `InMemoryUsageRecorder`, `NullUsageRecorder`, `SummarizationUsageRecorder`.
- `tests/unit/agent_runtime/observability/test_usage_recorder.py` — Protocol contracts, fail-soft assertions, fake recorder behavior, summarization scaffold.

### Modified

- `runtime_worker/handlers/run.py` — `__init__` accepts `usage_recorder` (default-built); `_record_run_usage` body collapses to a coordinator; `_record_per_call_usage` is **deleted**.
- `runtime_worker/loop.py` — `RuntimeWorker.__init__` plumbs a `usage_recorder` parameter so worker construction sites can pass an alternative (used by tests).
- `tests/unit/runtime_worker/test_runtime_worker.py` — no direct changes expected (recorder default builds from persistence + pricing_catalog), but verify the handler still reads back through the in-memory store.

### Not modified

- Persistence ports — same four methods.
- `CostCalculator` — same signature.
- `ModelPricingCatalog` — same shape.

### Deleted

- `_record_per_call_usage` method on `RuntimeRunHandler` (body absorbed into recorder).
- The duplicate pricing-lookup blocks inside `_record_run_usage` (absorbed into recorder).

---

## 6. Behaviors preserved

| Behavior                                                                             | How                                                                                    |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| Per-call row written once per AIMessage with usage                                   | `metrics.model_call_usage_records(...)` unchanged; recorder is the new writer.         |
| Run-level row written once on RUN_COMPLETED with `ON CONFLICT (run_id) DO NOTHING`   | Recorder calls the same persistence method.                                            |
| Cost stamped via banker's rounding, integer micro-USD                                | `CostCalculator` unchanged.                                                            |
| Pricing miss → row stays `cost_micro_usd IS NULL`                                    | Recorder returns `UsageRecordingResult(cost_micro_usd=None, ...)`.                     |
| Budget charge uses observed `cost_micro_usd`                                         | Handler reads `run_result.cost_micro_usd` from the recorder return.                    |
| Per-call cost stamped against the same pricing snapshot as the run                   | Both `record_run` and `record_call` use `pricing_at=completed_at` (handler passes it). |
| Fail-soft on every persistence error                                                 | Recorder absorbs, logs `runtime_usage_*_failed`, returns `UsageRecordingResult()`.     |
| Idempotency on worker retry — `run_id` UPSERT no-ops, per-call dedupes by message_id | Unchanged at the port layer; recorder is a pass-through.                               |
| `subagent_id`, `connector_slug`, `purpose`, `originating_tool_*` from 01b            | Stamped onto the record before it reaches the recorder; recorder doesn't touch them.   |

---

## 7. Tests

### 7.1 New unit tests

`test_usage_recorder.py`:

- `PostgresUsageRecorder.record_call` calls `persistence.record_model_call_usage(...)` then `update_model_call_usage_cost(...)` when pricing is found.
- `PostgresUsageRecorder.record_call` returns `UsageRecordingResult(cost=None)` when pricing catalog returns None (no cost UPDATE issued).
- `PostgresUsageRecorder.record_call` returns `UsageRecordingResult()` when the insert raises (logs; no cost UPDATE attempted).
- `PostgresUsageRecorder.record_call` returns `UsageRecordingResult()` when the cost-stamp UPDATE raises (insert already happened; the row stays NULL-cost).
- Same four cases for `record_run`.
- Same `pricing_at` value drives both per-call and run-level lookups within a single run (verified by spying on the pricing catalog).
- `InMemoryUsageRecorder` captures records in insertion order; counts match the production write count.
- `NullUsageRecorder` returns empty results without touching persistence.

`SummarizationUsageRecorder` (scaffold-only test):

- Constructing it with a fake `UsageRecorder` and calling `record_summarization_call` with a fake provider response produces a record with `purpose=CONTEXT_COMPRESSION` and routes it through `recorder.record_call`.

### 7.2 Handler integration

`test_run_handler_budgets.py` and `test_runtime_worker.py` continue to pass unchanged — the recorder default-builds from persistence + pricing_catalog so the in-memory store still observes the same writes.

One new test: a handler constructed with an injected `InMemoryUsageRecorder` runs a turn and the recorder captures `(call_records, run_record)` in the expected order.

### 7.3 Regression

All existing usage-related tests pass unchanged. The `runtime_run_usage` table and `runtime_model_call_usage` table see the same writes from the handler's perspective.

---

## 8. Risks

| Risk                                                                                                                                                     | Likelihood | Impact | Mitigation                                                                                                                                           |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Recorder default-build inside `RuntimeRunHandler.__init__` constructs a `PostgresUsageRecorder` that imports from a circular module path                 | Low        | Medium | The recorder lives in `agent_runtime/observability/`; handlers already import from there (`attribution`, `token_usage`). Verified no cycles.         |
| A test that stubbed `persistence.record_run_usage` directly stops working because the recorder owns it now                                               | Low        | Low    | The audit confirmed no test stubs that port method directly; all go through the in-memory adapter, which the recorder also uses.                     |
| `_charge_budgets` reads `cost_micro_usd_observed=None` when pricing fails — same as today, but the path is now through a typed result vs a mutable local | Low        | Low    | The behavior is identical; the test for "no pricing → charger sees None" continues to pass.                                                          |
| `summarization.py` is dead code today; the helper for it could rot                                                                                       | Medium     | Low    | The helper is small (~20 lines) and the parent PRD §3 documents the architectural boundary intent. A future enable-summarization PR re-validates it. |
| Recorder swallows an error that should have surfaced (e.g., a Pydantic record mismatch)                                                                  | Low        | Medium | Today's writers already swallow; recorder preserves contract. If the contract is wrong, that's a separate concern documented in handler's docstring. |

---

## 9. Rollout / rollback

### 9.1 Rollout

One PR, direct cutover. No flags.

1. Add `agent_runtime/observability/usage_recorder.py` with the four classes.
2. Wire `RuntimeRunHandler.__init__` to default-build a `PostgresUsageRecorder`.
3. Rewrite `_record_run_usage` to delegate to the recorder.
4. Delete `_record_per_call_usage`.
5. Add tests.

### 9.2 Rollback

`git revert`. No schema change in this PR — only code. Recorder vanishes; handlers go back to direct port calls. No data state to clean up.

---

## 10. Done definition

- `UsageRecorder` Protocol + three impls + summarization scaffold landed.
- `_record_per_call_usage` deleted.
- `_record_run_usage` is a coordinator that calls the recorder.
- Recorder tests green.
- Full ai-backend suite green.
- This sub-PRD `Status: Shipped`; parent PRD §4 row ticked.
