# Refactor PRD — OpenTelemetry coverage hardening (Phase 3 / P13)

**Status:** Draft (revised 2026-05-10 after code-level verification)
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §3](../architecture/refactor-audit.md#3-library-replacements), [roadmap P13](00-roadmap.md#phase-3--library-replacements-independent)

> **Revision note.** The original draft framed this as "adopt OpenTelemetry." Reading the code on 2026-05-10 shows OTel is already adopted: [`agent_runtime/observability/otel.py`](../../src/agent_runtime/observability/otel.py) configures tracer + meter providers with OTLP exporters, FastAPI / httpx / **psycopg** auto-instrumentation, and a `SafeAttributeSpanProcessor` that strips sensitive span attributes before export. The bespoke surface that survives is much smaller than the audit assumed. This PRD is rescoped accordingly.

---

## 1. Problem (revised against code)

### 1.1 What the code actually does today

| File                                                                                       | LOC | What it actually is                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------ | --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`otel.py`](../../src/agent_runtime/observability/otel.py)                                 | 187 | Full OTel bootstrap. Tracer + meter providers, OTLP exporters, **psycopg** auto-instrumentation (not asyncpg as the audit said), FastAPI + httpx instrumentation, `SafeAttributeSpanProcessor` denylist. Production fails closed without `OTEL_EXPORTER_OTLP_ENDPOINT`.                                                                                                                               |
| [`tracing.py`](../../src/agent_runtime/observability/tracing.py)                           | 123 | **LangSmith** adapter. `RuntimeTracer.traceable(...)` wraps a function for LangSmith tracing; no-op when `LANGSMITH_TRACING` is unset. NOT a competing OTel tracer. `TraceContext.event_id()` and `.identity_hash(value)` are utility helpers.                                                                                                                                                        |
| [`db_statement_metrics.py`](../../src/agent_runtime/observability/db_statement_metrics.py) | 310 | Already OTel. `DbStatementMetricsCollector` scrapes `pg_stat_statements` every 60s and exports per-digest counters via `opentelemetry.metrics.get_meter()`. `SlowQueryTracer` emits an OTel span when a query crosses `RUNTIME_DB_SLOW_QUERY_MS` (default 500). Query text is never exported — only SHA-256 digest. Worker-only, opt-in via `RUNTIME_DB_STATEMENT_SCRAPE_ENABLED=true` (default off). |
| [`approval_metrics.py`](../../src/agent_runtime/observability/approval_metrics.py)         | 144 | Already a thin OTel meter shim over `opentelemetry.metrics.get_meter()`. Three signals: `approval_forward_total` (counter), `approval_forward_invalid_total` (counter), `approval_chain_resolution_seconds` (histogram). Defensive no-op when OTel SDK isn't importable.                                                                                                                              |
| [`usage_attribution.py`](../../src/agent_runtime/observability/usage_attribution.py)       | 52  | **Not an OTel attribute shim.** A `PersistencePort` wrapper that resolves "which connector should this LLM call attribute to" by querying the most recent completed `runtime_tool_invocations` row with `completed_at` strictly before the call. Returns a `connector_slug` or `None`. Fail-soft: any exception → `None`.                                                                             |
| [`logging.py`](../../src/agent_runtime/observability/logging.py)                           | 189 | Pydantic-validated structured-log model (`RuntimeLogEvent`) with denylist redactor on `metadata`. Caller passes in `request_id` / `run_id` / `trace_id` from a context object; record is emitted via stdlib `logging` with `extra={"runtime": payload}`.                                                                                                                                              |
| [`http_logging.py`](../../src/agent_runtime/observability/http_logging.py)                 | 448 | Pydantic-validated `HttpLogEvent` + ASGI middleware that binds `request_id`/`org_id`/`user_id` to a `ContextVar`. **Already OTel-correlated**: `_active_otel_ids()` reads `trace_id`/`span_id` from the active OTel span and stamps them onto log records.                                                                                                                                            |
| [`redaction.py`](../../src/agent_runtime/observability/redaction.py)                       | 164 | Out of scope — covered by [`01-redaction-subsystem.md`](01-redaction-subsystem.md).                                                                                                                                                                                                                                                                                                                   |
| [`constants.py`](../../src/agent_runtime/observability/constants.py)                       | 74  | `Patterns.SENSITIVE_KEY` regex used by both log-record redactors and `SafeAttributeSpanProcessor`'s deny set complements the regex. Untouched.                                                                                                                                                                                                                                                        |

`audit_chain.py` is **not** in this directory anymore. It moved to the shared [`packages/audit-chain/`](../../../../packages/audit-chain/) per [`01-audit-chain.md`](01-audit-chain.md).

### 1.2 The real bespoke surface

Most of `observability/` is either already-OTel or domain logic. The actual residual smells are smaller:

- **LangSmith vs. OTel naming confusion.** `RuntimeTracer` (LangSmith) and `opentelemetry.trace` (OTel) are separate systems with overlapping vocabulary; new developers ask which to use. The answer depends on what's being traced (LLM call vs. distributed boundary). Worth documenting; possibly worth retiring LangSmith if it's not actively consumed.
- **Cross-process trace propagation across the queue.** `RuntimeWorker` claims a queued command; today the worker likely starts a fresh trace tree rather than continuing the API's. A run's spans split across two unrelated traces. **Verify in code, then decide.**
- **`DbStatementMetricsCollector` is opt-in (default off).** If no environment has it enabled, the file ships unused; if some do, it's load-bearing. Decide whether to flip the default or document it as a per-deploy diagnostic.
- **Two log schemas.** `RuntimeLogEvent` and `HttpLogEvent` carry overlapping fields (`event`, `level`, `request_id`, `trace_id`, `metadata`) and overlapping redactors. The HTTP one was added because the runtime one requires `run_id`, which an HTTP-ingress event doesn't have. The split is defensible but the redactor duplication is not.

### 1.3 What was wrong in the original audit

For the record, so the next reviewer doesn't re-litigate:

- "Adopt OTel SDK" — **already adopted.**
- "Delete `db_statement_metrics.py`" — **wrong.** It's pg_stat_statements aggregation, which OTel auto-instrumentation cannot replace (auto-instrumentation gives per-call spans, not normalized cumulative aggregates). Plus it ships with privacy-preserving SHA-256 digest naming that would be lost on a naive auto-instrumentation switch.
- "Migrate `RuntimeTracer` to OTel" — **wrong.** It's a LangSmith adapter, not an OTel competitor.
- "Migrate `usage_attribution` to OTel attributes" — **wrong.** It's a DB-backed connector resolver, not a span-attributes shim.
- "Auto-instrumentation for asyncpg" — **wrong driver name.** The codebase uses `psycopg`, instrumented via `opentelemetry.instrumentation.psycopg.PsycopgInstrumentor`.

### 1.4 What this is NOT

- Not a migration to OTel from a non-OTel system.
- Not a deletion of `db_statement_metrics.py`, `tracing.py`, `usage_attribution.py`, or `approval_metrics.py`.
- Not a change to the redaction pipeline (separately scoped in [`01-redaction-subsystem.md`](01-redaction-subsystem.md)).
- Not a change to the audit chain (already shipped as [`packages/audit-chain/`](../../../../packages/audit-chain/)).
- Not a switch in vendor exporter — OTel is provider-neutral.

---

## 2. Goal and non-goals

### Goal

Close concrete gaps in the existing OTel pipeline:

1. Cross-process trace continuity across the queue boundary (API ingress → worker run-handler under one trace).
2. Decide LangSmith's status (active or retire) and document.
3. Audit `SafeAttributeSpanProcessor`'s denylist against current span emission paths; tighten if anything sensitive can sneak past.
4. Decide `DbStatementMetricsCollector` default (keep opt-in or flip to opt-out in production with `pg_stat_statements` installed).
5. Consolidate the two log-event redactor implementations into one.

### Non-goals

- Rewrite anything in the OTel-already files. The existing setup is correct.
- Touch the redaction subsystem.
- Change LangSmith's API for callers if we keep it.
- Add new span boundaries — the existing trace shape is fine.

### Success criteria

- **Trace propagation:** a single `trace_id` covers the API request that enqueued a run + the worker spans for that run. A representative `f1`–`f4` flow integration test asserts the parent/child relationship.
- **LangSmith decision:** either documented as "actively used; here's how it integrates" with a runbook entry, or retired (file deleted, callers updated, langsmith dependency removed).
- **`SafeAttributeSpanProcessor`:** denylist audited; covers every sensitive attribute key that could be set by `psycopg`, `httpx`, FastAPI, and ad-hoc `span.set_attribute(...)` calls in our code. Test pins the deny set.
- **`DbStatementMetricsCollector` default:** explicit decision logged in this PRD's §9 → default landed; ops docs updated.
- **Log schema consolidation:** `_MetadataRedactor` defined once and shared by both `RuntimeLogEvent` and `HttpLogEvent`. Either one Pydantic base class or one helper module — pick one.

---

## 3. Systems touched

### 3.1 Files added

| File                                                              | Purpose                                                                                                                                             |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent_runtime/observability/queue_propagation.py`                | Inject + extract W3C `traceparent` / `tracestate` on queue command payloads. Used by `RuntimeApiService` (producer) and `RuntimeWorker` (consumer). |
| `tests/unit/observability/test_queue_propagation.py`              | Round-trip propagation, missing-headers tolerance, malformed-headers tolerance.                                                                     |
| `tests/integration/observability/test_cross_process_trace.py`     | API enqueues a run; worker claims it; the worker run-handler span has the API request span as an ancestor.                                          |
| `tests/unit/observability/test_safe_attribute_processor_audit.py` | Pinned audit: a representative span carrying every known-emitted attribute is processed; assert the deny rules match the spec table in §5.          |

### 3.2 Files removed (only if LangSmith is retired in §9)

| File                                                                                         | Reason                                                                                                                                                                                   |
| -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/observability/tracing.py`](../../src/agent_runtime/observability/tracing.py) | Only consumer is the `@RuntimeTracer.traceable(...)` decorator if LangSmith stays unused. `TraceContext.event_id()` and `.identity_hash(value)` migrate to a small `identity.py` helper. |

### 3.3 Files changed

| File                                                                                                   | Change                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py)                                   | Worker entrypoint already calls `TelemetryBootstrap.configure()` (verify in [`runtime_worker/__main__.py:40-60`](../../src/runtime_worker/__main__.py)). Add: extract `traceparent` from the claimed command before starting the run-handler span. |
| [`agent_runtime/api/service.py`](../../src/agent_runtime/api/service.py)                               | When enqueuing a `RuntimeRunCommand` / `RuntimeCancelCommand` / `RuntimeApprovalResolvedCommand`, attach the current span's `traceparent` to the command's metadata.                                                                               |
| [`agent_runtime/observability/logging.py`](../../src/agent_runtime/observability/logging.py)           | Replace local `_MetadataRedactor` with a shared helper. Pydantic model unchanged.                                                                                                                                                                  |
| [`agent_runtime/observability/http_logging.py`](../../src/agent_runtime/observability/http_logging.py) | Replace local `_MetadataRedactor` with the same shared helper.                                                                                                                                                                                     |
| `agent_runtime/observability/_redactor.py` (new) **or** add helper to `constants.py`                   | One canonical `MetadataRedactor` used by both log models.                                                                                                                                                                                          |
| [`agent_runtime/observability/otel.py`](../../src/agent_runtime/observability/otel.py)                 | Possibly extend `_DENY_ATTR_KEYS` and the regex deny pattern based on the audit in §3.1 test. No structural change.                                                                                                                                |
| `docs/architecture/observability.md` (new or extend existing)                                          | Document: which tracer to use when (LangSmith for LLM-internal traces if kept, OTel for everything else); cross-process trace propagation; `SafeAttributeSpanProcessor` deny contract; `DbStatementMetricsCollector` enablement guidance.          |

### 3.4 Files **not** touched

- [`agent_runtime/observability/db_statement_metrics.py`](../../src/agent_runtime/observability/db_statement_metrics.py) — keep as-is.
- [`agent_runtime/observability/approval_metrics.py`](../../src/agent_runtime/observability/approval_metrics.py) — already an OTel meter shim.
- [`agent_runtime/observability/usage_attribution.py`](../../src/agent_runtime/observability/usage_attribution.py) — domain logic.
- [`agent_runtime/observability/redaction.py`](../../src/agent_runtime/observability/redaction.py) — covered separately.

---

## 4. Approach

### 4.1 Phasing

Three small PRs, each independently shippable.

**Step 1 — Cross-process trace propagation.**

- New `queue_propagation.py` carries two methods: `attach(traceparent_carrier)` and `extract(carrier) -> Context`. Uses `opentelemetry.propagate.get_global_textmap()` so future propagator changes are one config flip.
- `RuntimeApiService.enqueue_run/cancel/approval` writes the W3C `traceparent` / `tracestate` headers into the command's metadata dict.
- `RuntimeWorker._dispatch` extracts the context from the claimed command before starting the run-handler span.
- Default ON in dev/staging via `RUNTIME_PROPAGATE_QUEUE_TRACE=true`. Off in prod for the first release; flip after dashboard / alert owners confirm trace consolidation doesn't break their queries.

**Step 2 — Log redactor consolidation.**

- One `MetadataRedactor` class in `agent_runtime.observability` used by `RuntimeLogEvent` and `HttpLogEvent`.
- Behavior identical to current — pinned by snapshot tests of representative metadata blobs.
- LOC reduction is minor (~30 lines); the win is one place for sensitive-key rules to evolve.

**Step 3 — LangSmith decision + SafeAttributeSpanProcessor audit.**

- Grep call sites of `RuntimeTracer.traceable` and `RuntimeTracer.traced`. Count active uses; check whether `LANGSMITH_TRACING` is set in any deploy environment.
  - **Active:** keep, document the runbook, add a test.
  - **Inactive:** delete `tracing.py`, remove `langsmith` from requirements, migrate `TraceContext.event_id` / `.identity_hash` to a small helper.
- Audit `SafeAttributeSpanProcessor`. Build a representative span (one call to each instrumentation: FastAPI request, httpx call, psycopg query, an ad-hoc `tracer.start_as_current_span` inside the run-handler). Enumerate every attribute key the SDK + our code emit. Compare to `_DENY_ATTR_KEYS` and `_DENY_ATTR_PATTERN`. Anything sensitive missing → add to the deny set.
- Update ops docs.

### 4.2 Cross-process trace propagation contract

Producer (the API):

```python
from opentelemetry import trace
from opentelemetry.propagate import inject

current_span = trace.get_current_span()
trace_headers: dict[str, str] = {}
if current_span and current_span.get_span_context().is_valid:
    inject(trace_headers)  # writes "traceparent" / "tracestate"

command_metadata["trace_propagation"] = trace_headers
```

Consumer (the worker), on `_dispatch`:

```python
from opentelemetry.propagate import extract

ctx = extract(claim.command.metadata.get("trace_propagation") or {})
with tracer.start_as_current_span("runtime.run_handler", context=ctx) as span:
    ...
```

The `trace_propagation` field is allowed-but-untrusted: malformed headers fall back to a fresh trace (already the default behavior of `extract`).

### 4.3 SafeAttributeSpanProcessor deny audit

The current code in [`otel.py:41-63`](../../src/agent_runtime/observability/otel.py) has:

```python
_DENY_ATTR_KEYS = frozenset({
    "http.url", "http.target", "url.full", "url.query", "url.path",
    "db.statement", "db.statement.parameters", "db.user",
    "http.request.body", "http.response.body",
    "exception.message", "exception.stacktrace",
    "code.filepath", "code.namespace",
})
_DENY_ATTR_PATTERN = re.compile(
    r"(body|payload|content|query|prompt|completion|messages|secret|token|password|authorization|credential|api[_-]?key|cookie|session)",
    re.I,
)
```

Audit tasks:

- `psycopg` instrumentation in current OTel can also emit `db.name`, `db.system`, `db.connection_string` — confirm `db.connection_string` falls under the deny pattern (it doesn't via the regex; the substring "credential" is the closest hit but `connection_string` doesn't match).
- `FastAPI` may emit `http.host` and `http.user_agent` — currently allowed; confirm they're acceptable.
- Our own `RuntimeTracer.start_as_current_span(...)` call sites — enumerate which attributes we set; verify none match the pattern or hit the deny set.
- httpx may attach `url.full` (denied) and `http.request.method` (allowed) — sanity-check both.

Output of the audit lands in `tests/unit/observability/test_safe_attribute_processor_audit.py` as a pinned set; future regressions are caught at test time.

---

## 5. Behaviors preserved

| Behavior                                                                                                                         | How preserved                                                                                                                                            |
| -------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Production fails closed without `OTEL_EXPORTER_OTLP_ENDPOINT`                                                                    | Bootstrap unchanged.                                                                                                                                     |
| `OTEL_INSTRUMENTATION_HTTP_CAPTURE_BODY=false` set as a default env var                                                          | Bootstrap unchanged.                                                                                                                                     |
| `SafeAttributeSpanProcessor` strips deny-listed attributes before export                                                         | Set unchanged or expanded; never reduced.                                                                                                                |
| FastAPI `/healthz` and `/readyz` excluded from instrumentation                                                                   | Bootstrap unchanged.                                                                                                                                     |
| `RuntimeLogEvent` Pydantic schema (event, level, request_id, run_id, trace_id, …)                                                | Schema unchanged. Only the redactor implementation moves.                                                                                                |
| `HttpLogEvent` Pydantic schema                                                                                                   | Schema unchanged. `_active_otel_ids()` for trace correlation unchanged.                                                                                  |
| `Patterns.SENSITIVE_KEY` from `constants.py` is the source of metadata redaction                                                 | Unchanged.                                                                                                                                               |
| `RuntimeTracer.traceable(...)` decorator behavior (no-op when LangSmith disabled, decorator when enabled)                        | If LangSmith is kept: unchanged. If retired: callers updated to remove the decorator.                                                                    |
| `TraceContext.event_id()` and `.identity_hash(value)` semantics                                                                  | Identity-hash is a 16-char SHA-256 truncation; event_id is a uuid4 hex. Both functions move to `identity.py` if `tracing.py` is retired; semantics same. |
| `DbStatementMetricsCollector` opt-in via `RUNTIME_DB_STATEMENT_SCRAPE_ENABLED=true`                                              | Unchanged unless §9 explicitly flips the default.                                                                                                        |
| `SlowQueryTracer` threshold via `RUNTIME_DB_SLOW_QUERY_MS` (default 500)                                                         | Unchanged.                                                                                                                                               |
| Query digest is SHA-256, never raw text                                                                                          | Unchanged.                                                                                                                                               |
| `ApprovalMetrics` signal names (`approval_forward_total`, `approval_forward_invalid_total`, `approval_chain_resolution_seconds`) | Unchanged.                                                                                                                                               |
| `UsageAttributionResolver.resolve(...)` returns `connector_slug` or `None`; fail-soft on errors                                  | Unchanged.                                                                                                                                               |

---

## 6. Risks and mitigations

| Risk                                                                                                     | Likelihood | Impact | Mitigation                                                                                                                                                             |
| -------------------------------------------------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cross-process trace propagation breaks dashboards keyed on "fresh trace per worker run"                  | Medium     | Medium | Step 1 ships behind a flag (`RUNTIME_PROPAGATE_QUEUE_TRACE=false` default in prod). Burn in dev/staging first; flip after dashboard owners sign off.                   |
| LangSmith retirement deletes a system someone is actively using                                          | Medium     | High   | Step 3 starts with a grep audit. Delete only if `LANGSMITH_TRACING` is not set in any environment manifest AND the call sites have minimal value.                      |
| SafeAttributeSpanProcessor audit misses an attribute that gets set conditionally                         | Medium     | High   | Audit covers the static set; add a runtime alarm: `SafeAttributeSpanProcessor` logs at WARNING (rate-limited) when it drops an attribute, so unexpected drops surface. |
| Log redactor consolidation accidentally narrows or widens redaction                                      | Low        | Medium | Pinned snapshot tests on representative metadata blobs from both event types.                                                                                          |
| W3C propagation header malformed by an upstream system                                                   | Low        | Low    | `extract` already returns an empty context on parse failure; behavior is "fresh trace," same as today.                                                                 |
| Worker bootstrap order — instrumenting before `TelemetryBootstrap.configure()` runs                      | Medium     | Medium | Add a startup assert: any span created before `_CONFIGURED=True` goes to the no-op tracer; log a startup warning if any span is observed before bootstrap.             |
| Deciding to flip `DbStatementMetricsCollector` default to ON breaks deploys without `pg_stat_statements` | Medium     | Medium | Keep opt-in by default. Document that ops can enable in a deploy with the extension installed. (See §9 — confirm this is the right call.)                              |

---

## 7. Test requirements

Per [`docs/CLAUDE.md`](../CLAUDE.md), unit testing is explicit.

### 7.1 New unit tests

- `test_queue_propagation.py`
  - `attach({})` writes valid `traceparent` / `tracestate` headers when an OTel span is active.
  - `extract({})` returns a default Context.
  - `extract({"traceparent": "<garbage>"})` returns a default Context (no exception).
  - Round-trip: `extract(attach({}))` produces a Context that yields the same trace_id / span_id when used to start a child span.

- `test_safe_attribute_processor_audit.py`
  - Snapshot a span with every known attribute key; assert the post-processor view excludes exactly the deny set.
  - Adding a new attribute to the snapshot but not the deny set fails the test.

- `test_log_redactor_consolidation.py`
  - The same input metadata blob produces the same output through `RuntimeLogEvent.metadata` and `HttpLogEvent.metadata` validators.
  - `Patterns.SENSITIVE_KEY` matches drop the field; non-string keys drop the field; non-scalar values drop the field.

### 7.2 Integration tests

- `test_cross_process_trace.py`
  - Drive the `f1-single-turn` flow end-to-end; assert that the API ingress span and the worker run-handler span share the same `trace_id`.
  - With `RUNTIME_PROPAGATE_QUEUE_TRACE=false`, assert the worker span has its own trace (no parent).

### 7.3 Manual checks

- Open the OTLP backend after a representative run; confirm the trace tree spans both the API and the worker process.
- Verify FastAPI `/healthz` and `/readyz` produce no spans.
- Verify slow-query spans fire when an artificially slow query is issued (e.g. `SELECT pg_sleep(1.0)`).

### 7.4 Regression tests

All existing tests under `tests/unit/observability/` must pass unchanged. Snapshot tests on log metadata, attribute redaction, and the LangSmith decorator behavior continue to pass.

---

## 8. Rollout / rollback

### 8.1 Rollout

1. **Step 1 PR — propagation.** Land with flag default OFF in prod. Burn in dev/staging for one week. Flip in prod after dashboard owners confirm.
2. **Step 2 PR — redactor consolidation.** Land in any environment; behavior is byte-identical so no staged rollout needed.
3. **Step 3 PR — LangSmith + audit.** Two sub-PRs: (a) audit + grep + decision documented in §9; (b) the implementation that follows from the decision.

### 8.2 Rollback

- Step 1: flip `RUNTIME_PROPAGATE_QUEUE_TRACE=false`.
- Step 2: revert; tests prove parity.
- Step 3: if LangSmith retirement was wrong, restore `tracing.py` from git history; the decision was reversible during the audit window.

---

## 9. Open questions

These must be answered before the corresponding step ships.

- **`DbStatementMetricsCollector` default.** Keep opt-in (current) or flip to opt-out in production environments where `pg_stat_statements` is installed? Suggested call: keep opt-in; document the enablement clearly. Confirm with ops.
- **LangSmith status.** Is `LANGSMITH_TRACING` set anywhere? If yes, who consumes the LangSmith UI? Determines whether `tracing.py` survives Step 3.
- **`SlowQueryTracer` callers.** The class exposes `observe(query, duration_ms)` and `time_block(query)`. Verify call sites in code (psycopg cursor wrapper? per-query manual instrumentation?). If unused, retire alongside the LangSmith decision.
- **`approval_chain_resolution_seconds` bucket layout.** Current buckets are `(30, 60, 300, 1800, 3600, 86400)` seconds — chosen for "30s / 1min / 5min / 30min / 1h / 1d." Confirm with the dashboard owner before this PRD locks behavior.
- **Cross-process trace flag default.** Suggested: `RUNTIME_PROPAGATE_QUEUE_TRACE=false` in prod for the first release, `true` in dev/staging. Confirm with ops.
- **`Patterns.SENSITIVE_KEY` ownership.** This regex is consumed by `RuntimeLogEvent.metadata`, `HttpLogEvent.metadata`, AND `SafeAttributeSpanProcessor` (indirectly — the latter has its own regex but the categories should match). Confirm whether one source-of-truth regex is feasible. Coordinated with [`01-redaction-subsystem.md`](01-redaction-subsystem.md).

---

## 10. Done definition

- All tests in §7 added and green.
- Step 1 has shipped to prod; cross-process traces verified in the OTLP backend.
- Step 2 has shipped; metadata redaction has one source-of-truth helper.
- Step 3 LangSmith decision is documented and executed (kept-with-runbook OR retired-with-PR).
- `SafeAttributeSpanProcessor` deny-set audit is committed as a pinned test.
- Observability ops docs updated.
- This PRD is moved to `Status: Shipped` and the [roadmap](00-roadmap.md) status checkbox flipped.
