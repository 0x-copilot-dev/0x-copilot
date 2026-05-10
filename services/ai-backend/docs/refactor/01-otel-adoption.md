# Refactor PRD — OpenTelemetry adoption + thin observability/ (Phase 3 / P13)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §3](../architecture/refactor-audit.md#3-library-replacements) (rows on `db_statement_metrics` and the broader observability surface), [roadmap P13](00-roadmap.md#phase-3--library-replacements-independent)

---

## 1. Problem

The [`agent_runtime/observability/`](../../src/agent_runtime/observability/) package contains ten files. Several reinvent capabilities that OpenTelemetry's SDK and auto-instrumentation provide for free:

| File                                                                                       | Status after this PRD                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`tracing.py`](../../src/agent_runtime/observability/tracing.py)                           | **Shrink.** `RuntimeTracer` becomes a thin shim over `opentelemetry.trace.get_tracer()`. `TraceContext`, `TraceNames`, `identity_hash`, `event_id` stay (domain).                                         |
| [`logging.py`](../../src/agent_runtime/observability/logging.py)                           | **Shrink.** Structured-JSON logging stays; trace-id injection delegated to OTel context.                                                                                                                  |
| [`http_logging.py`](../../src/agent_runtime/observability/http_logging.py)                 | **Shrink.** `RequestContextMiddleware` keeps domain fields (org_id, user_id, persona); request/response timing comes from OTel FastAPI auto-instrumentation.                                              |
| [`otel.py`](../../src/agent_runtime/observability/otel.py)                                 | **Extend.** Already does partial OTel bootstrap (`TelemetryBootstrap`, `instrument_fastapi`, `instrument_httpx_clients`). Add asyncpg / sqlalchemy auto-instrumentation, OTel logs SDK, OTel metrics SDK. |
| [`db_statement_metrics.py`](../../src/agent_runtime/observability/db_statement_metrics.py) | **Delete.** OTel asyncpg / SQLAlchemy auto-instrumentation publishes statement timing, query categorization, slow-query flagging.                                                                         |
| [`usage_attribution.py`](../../src/agent_runtime/observability/usage_attribution.py)       | **Keep + adapt.** Per-user / per-org / per-connector token tagging is domain knowledge. Becomes an OTel attributes shim that sets `org_id`, `user_id`, `connector_id` on the active span.                 |
| [`approval_metrics.py`](../../src/agent_runtime/observability/approval_metrics.py)         | **Keep + thin.** Becomes a shim over OTel metrics SDK (counter, histogram). Removes the bespoke metric-buffering glue.                                                                                    |
| [`audit_chain.py`](../../src/agent_runtime/observability/audit_chain.py)                   | **Untouched (out of scope).** Resolved in [`01-audit-chain.md`](01-audit-chain.md): the chain is now in the shared `packages/audit-chain/` and is import-only here.                                       |
| [`redaction.py`](../../src/agent_runtime/observability/redaction.py)                       | **Untouched (out of scope).** Replaced separately in [`01-redaction-subsystem.md`](01-redaction-subsystem.md).                                                                                            |
| [`constants.py`](../../src/agent_runtime/observability/constants.py)                       | **Untouched.** `Keys`, `UserContentKeys`, `Patterns.SENSITIVE_KEY/VALUE` stay where consumers find them.                                                                                                  |

The bespoke surface that goes:

- **`db_statement_metrics.py`** — OTel ships statement-level instrumentation for asyncpg (via `opentelemetry-instrumentation-asyncpg`) and SQLAlchemy (via `opentelemetry-instrumentation-sqlalchemy`). Bespoke timing/categorization is duplicated work.
- **Most of `tracing.py`** — `RuntimeTracer.start(...)` should become `tracer.start_as_current_span(...)`; `TraceContext` is mostly OTel context propagation, which the SDK does natively (W3C trace-context headers, baggage).
- **The bespoke trace-id field-injection** in logging.py / http_logging.py — OTel ships log-correlation processors that attach `trace_id` / `span_id` to log records automatically.

What stays bespoke is the **domain layer**: which user / org / connector this span belongs to (`usage_attribution.py`); approval-state metrics (`approval_metrics.py`); the trace-name vocabulary (`TraceNames.RUNTIME_INVOKE`, etc.) used as span names.

### Symptoms (today)

- Two trace systems coexist: `RuntimeTracer` (in-house) and `opentelemetry.trace` (via `otel.py` for FastAPI / httpx). Span trees from one don't always nest under the other cleanly.
- DB statement timing requires opting in to `db_statement_metrics`; OTel auto-instrumentation would cover every `asyncpg` call uniformly.
- Logs carry a `trace_id` field that is set by hand in `http_logging.py`; for any code path outside the FastAPI request lifespan, `trace_id` is missing or stale.
- New developers ask "do I use `RuntimeTracer.start` or `tracer.start_as_current_span`?" and the answer depends on which package they're in.

### What this is NOT

- Not a change to **redaction** behavior — that's [`01-redaction-subsystem.md`](01-redaction-subsystem.md).
- Not a change to the **audit chain** — that landed in [`01-audit-chain.md`](01-audit-chain.md) as `packages/audit-chain/`.
- Not a change to **what gets traced**. The set of span boundaries and span names is preserved exactly; only the implementation switches from in-house to OTel.
- Not a switch in log destination. Whatever ingester/SIEM consumes logs today continues to consume them; the only change is the structure-of-record (still JSON; still has `trace_id`).
- Not a switch in metric destination. Existing dashboards must keep working — see [§5 / metric naming](#52-metric-name-mapping).

---

## 2. Goal and non-goals

### Goal

The runtime emits traces, logs, and metrics through OpenTelemetry SDK + auto-instrumentation. The bespoke `RuntimeTracer` shrinks to a thin shim. `db_statement_metrics.py` is deleted. Domain attribution (`usage_attribution`, `approval_metrics`) keeps its public API but is implemented over OTel primitives.

### Non-goals

- Change which spans exist, what they are named, or how they nest.
- Change log structure beyond delegating `trace_id` injection to OTel.
- Change metric meaning or the dashboards / alerts that consume them.
- Replace any other observability subsystem (redaction, audit, deployment profile).
- Switch the Python observability vendor (Datadog tracer, NewRelic agent) — OTel is provider-neutral; whichever exporter ships the data stays a deploy-time decision.

### Success criteria

- `agent_runtime/observability/db_statement_metrics.py` is deleted; equivalent metrics published by OTel asyncpg/SQLAlchemy auto-instrumentation.
- `RuntimeTracer.start(name, **attrs)` ⇒ thin wrapper around `tracer.start_as_current_span(name, attributes=attrs)`. Public API of `RuntimeTracer` is unchanged for callers.
- `TraceContext` either deleted or thinned to a Pydantic boundary type that wraps OTel `Context`.
- `usage_attribution` continues to expose its current API (`attribute_usage`, `with_user`, etc.); internally it sets OTel span attributes (`org_id`, `user_id`, `connector_id`).
- `approval_metrics` continues to expose its current API; internally uses `opentelemetry.metrics.get_meter()`.
- Logs carry `trace_id` and `span_id` via OTel's logging processor; in-house injection in `http_logging.py` is removed.
- All existing observability tests pass; a new test confirms span names match the pre-migration set.
- Deploy config (k8s manifests / docker-compose) gains an OTLP collector endpoint env var and documents the resource attributes (service.name, service.namespace, deployment.environment).

---

## 3. Systems touched

Inventory derived from [C8b cross-cutting](../architecture/11-cross-cutting.puml) and the architecture index.

### 3.1 Files added

| File                                                         | Purpose                                                                                                              |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `agent_runtime/observability/otel_config.py`                 | Resource attributes (service.name, version, environment), exporter config (OTLP endpoint, headers), sampling config. |
| `agent_runtime/observability/log_processor.py`               | OTel `LoggingHandler` configuration; attaches `trace_id`, `span_id`, baggage to every log record.                    |
| `tests/unit/observability/test_otel_bootstrap.py`            | Bootstrap composes correctly; idempotent; respects feature-flag.                                                     |
| `tests/unit/observability/test_span_names_snapshot.py`       | Snapshot test: every `RuntimeTracer.start(name=...)` call site produces the same span name as before migration.      |
| `tests/integration/observability/test_db_instrumentation.py` | OTel asyncpg auto-instrumentation produces statement-level spans for a representative query.                         |
| `tests/integration/observability/test_log_correlation.py`    | A request handler logs inside a span; the log record contains the same `trace_id` as the active span.                |

### 3.2 Files removed

| File                                                                                                                                 | Reason                                                                              |
| ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| [`agent_runtime/observability/db_statement_metrics.py`](../../src/agent_runtime/observability/db_statement_metrics.py)               | Replaced by `opentelemetry-instrumentation-asyncpg` / `-sqlalchemy`.                |
| Bespoke trace-id injection in [`agent_runtime/observability/http_logging.py`](../../src/agent_runtime/observability/http_logging.py) | Replaced by OTel logging processor; `RequestContextMiddleware` keeps domain fields. |

### 3.3 Files changed

| File                                                                                                             | Change                                                                                                                                                                                                                     |
| ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/observability/tracing.py`](../../src/agent_runtime/observability/tracing.py)                     | `RuntimeTracer` shrinks to a shim over `opentelemetry.trace.get_tracer(__name__)`. `TraceContext` either deleted or kept as a Pydantic envelope around OTel `Context` for crossing typed boundaries (e.g. queue commands). |
| [`agent_runtime/observability/otel.py`](../../src/agent_runtime/observability/otel.py)                           | `TelemetryBootstrap` extended to set up: tracer provider with OTLP exporter; meter provider; logger provider; FastAPI / httpx / asyncpg / sqlalchemy auto-instrumentations; resource attributes from `otel_config.py`.     |
| [`agent_runtime/observability/usage_attribution.py`](../../src/agent_runtime/observability/usage_attribution.py) | Internal switch: instead of writing to a bespoke buffer, sets OTel span attributes via `trace.get_current_span().set_attributes({...})` and emits OTel metric counters where it currently emits internal counters.         |
| [`agent_runtime/observability/approval_metrics.py`](../../src/agent_runtime/observability/approval_metrics.py)   | Internal switch: bespoke metric buffer replaced with `opentelemetry.metrics.get_meter().create_counter(...)` + `create_histogram(...)`. Metric names preserved per [§5.2](#52-metric-name-mapping).                        |
| [`agent_runtime/observability/logging.py`](../../src/agent_runtime/observability/logging.py)                     | Structured-JSON formatter stays; the `trace_id` / `span_id` fields are populated by `LoggingHandler` from OTel context, not by `http_logging.py`.                                                                          |
| [`agent_runtime/observability/http_logging.py`](../../src/agent_runtime/observability/http_logging.py)           | `RequestContextMiddleware` keeps domain context (`org_id`, `user_id`, `persona`); `trace_id` injection deleted.                                                                                                            |
| [`runtime_api/app.py`](../../src/runtime_api/app.py)                                                             | Lifespan calls extended `TelemetryBootstrap` (already does this — only the bootstrap is fatter).                                                                                                                           |
| [`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py)                                             | Worker also calls `TelemetryBootstrap` so its spans show up in the same trace.                                                                                                                                             |
| `requirements.txt` (per service)                                                                                 | Add: `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi`, `-httpx`, `-asyncpg`, `-sqlalchemy` (if used), `-logging`.                                                               |

### 3.4 Files **not** touched (out of scope)

- [`agent_runtime/observability/audit_chain.py`](../../src/agent_runtime/observability/audit_chain.py) — covered by [`01-audit-chain.md`](01-audit-chain.md).
- [`agent_runtime/observability/redaction.py`](../../src/agent_runtime/observability/redaction.py) — covered by [`01-redaction-subsystem.md`](01-redaction-subsystem.md).
- [`agent_runtime/observability/constants.py`](../../src/agent_runtime/observability/constants.py) — public symbols stay where consumers expect.
- Anything outside `agent_runtime/observability/` and the two app entrypoints. Tracer call sites inside business logic still call `RuntimeTracer.start(...)` — the API is preserved.

---

## 4. Approach

### 4.1 Phasing

Land in three steps. Each step is one PR. Don't bundle.

**Step 1 — Bootstrap parity.**

- Extend `TelemetryBootstrap` to configure tracer provider + meter provider + logger provider + auto-instrumentations.
- New `otel_config.py` carries resource attributes and exporter config.
- New `log_processor.py` attaches `trace_id` / `span_id` to log records.
- **Existing `RuntimeTracer` and `db_statement_metrics` continue to run side-by-side.** This step adds OTel without removing anything.
- Deploy to staging, confirm spans/logs/metrics flow through the OTLP collector, dashboards still render.

**Step 2 — Migrate `RuntimeTracer` and delete `db_statement_metrics`.**

- `RuntimeTracer` rewritten as the shim. All call sites unchanged.
- `db_statement_metrics.py` deleted; OTel asyncpg auto-instrumentation enabled in Step 1 takes over.
- `usage_attribution` and `approval_metrics` switched to OTel internals (public API preserved).
- Bespoke trace-id injection in `http_logging.py` removed; `LoggingHandler` from Step 1 covers it.
- Snapshot test confirms span names unchanged.

**Step 3 — Tighten and document.**

- Sampling configuration (head-based default; tail-based opt-in for error traces).
- Deploy docs updated: required env vars (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`), per-environment resource attributes, dashboard migration notes.
- Old metric names removed from emit (only the migrated names emit; see [§5.2](#52-metric-name-mapping)).

### 4.2 Auto-instrumentations

Adopt these from `opentelemetry-instrumentation-*`:

- **`fastapi`** — request spans, route attributes, status codes. Already partially set up in `otel.py`.
- **`httpx`** — outbound HTTP calls (backend RPCs, MCP server calls, OAuth flows). Already partially set up.
- **`asyncpg`** — PostgreSQL statement spans, statement-text attribute (subject to the redaction policy).
- **`sqlalchemy`** — only if the codebase uses it directly anywhere (verify in code; if all DB access is asyncpg-direct, skip).
- **`logging`** — attaches OTel context to standard library log records via `LoggingHandler`.

Each instrumentation is opt-in via the bootstrap; turning one off is a single line in `otel_config.py`.

### 4.3 Span name fidelity

`TraceNames.RUNTIME_INVOKE` and the rest of the vocabulary in `tracing.py` become OTel span names verbatim:

```python
# Before
with RuntimeTracer.start(name=TraceNames.RUNTIME_INVOKE, attributes={...}) as ctx:
    ...

# After
tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span(TraceNames.RUNTIME_INVOKE, attributes={...}) as span:
    ...
```

`RuntimeTracer.start(name=..., attributes=...)` keeps that signature so call sites never change. The body of `start` becomes `tracer.start_as_current_span(...)`.

### 4.4 Cross-process trace continuity

Currently the worker may not propagate trace context across queue boundaries. With OTel:

- Producer (API) attaches `traceparent` / `tracestate` to the queue command payload.
- Consumer (worker) extracts it before spawning the run-handler span.
- All worker spans for a run share the API's trace_id.

This is a behavior **improvement**, not a regression — confirm dashboards / alerts that key off "trace per run" don't double-count. If the team prefers the current isolation, add a config flag and default off.

---

## 5. Behaviors preserved

### 5.1 Span structure

Each must be a pinned test before merge.

| Behavior                                                                                                                                      | How preserved                                                                                                                              |
| --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Every existing call site to `RuntimeTracer.start(name=...)` produces a span with the same name                                                | Snapshot test (`test_span_names_snapshot.py`) enumerates expected names and asserts each starts at least once during a representative run. |
| `TraceNames.RUNTIME_INVOKE`, `RUNTIME_INVOKE_TURN`, `MODEL_CALL`, `TOOL_CALL`, `MCP_CALL`, `SUBAGENT_RUN`, `RETENTION_SWEEP`, `BUDGET_CHARGE` | Same constants, same call sites.                                                                                                           |
| `identity_hash` on a span                                                                                                                     | Continues to be set as a span attribute (`identity.hash`), now via OTel attributes.                                                        |
| `event_id` on a span                                                                                                                          | Continues to be set as a span attribute (`event.id`).                                                                                      |
| Cross-process trace continuity                                                                                                                | New behavior (improvement). Dashboards verified.                                                                                           |

### 5.2 Metric name mapping

Provide a one-to-one map from current bespoke metric names to OTel-emitted names. **All current names continue to emit during Step 2 + Step 3.** Old emission removed only at the very end of Step 3, with dashboard owners signed off.

| Current name (bespoke)                                | OTel name (after migration)                          | Type      |
| ----------------------------------------------------- | ---------------------------------------------------- | --------- |
| `runtime.db.statement_count`                          | `db.client.queries`                                  | counter   |
| `runtime.db.statement_duration_ms`                    | `db.client.duration`                                 | histogram |
| `runtime.approval.requested_total`                    | `runtime.approval.requested`                         | counter   |
| `runtime.approval.resolved_total`                     | `runtime.approval.resolved`                          | counter   |
| `runtime.approval.expired_total`                      | `runtime.approval.expired`                           | counter   |
| `runtime.approval.time_to_resolve_seconds`            | `runtime.approval.time_to_resolve`                   | histogram |
| `runtime.usage.tokens_input` (per user/org/connector) | `runtime.usage.tokens` with `direction=input`        | counter   |
| `runtime.usage.tokens_output`                         | `runtime.usage.tokens` with `direction=output`       | counter   |
| `runtime.usage.tokens_reasoning`                      | `runtime.usage.tokens` with `direction=reasoning`    | counter   |
| `runtime.usage.tokens_cached_input`                   | `runtime.usage.tokens` with `direction=cached_input` | counter   |

Verify the actual current name list in code before merging Step 2 — this table is illustrative.

### 5.3 Log structure

| Field                            | Source                                                                            |
| -------------------------------- | --------------------------------------------------------------------------------- |
| `timestamp`                      | unchanged                                                                         |
| `level`                          | unchanged                                                                         |
| `logger`                         | unchanged                                                                         |
| `message`                        | unchanged                                                                         |
| `trace_id`                       | now from OTel `LoggingHandler`, formatted to match the current 32-char hex string |
| `span_id`                        | now from OTel `LoggingHandler`, 16-char hex                                       |
| `org_id` / `user_id` / `persona` | from `RequestContextMiddleware` — unchanged                                       |
| Domain fields                    | unchanged                                                                         |

The format-of-record is identical; the producer changes.

### 5.4 Domain attribution

`usage_attribution.attribute_usage(user_id=..., org_id=..., connector_id=...)` continues to:

1. Set OTel span attributes on the active span: `org_id`, `user_id`, `connector_id`.
2. Emit a counter increment for the token-direction series.
3. Be a no-op if no active span (consistent with current behavior).

---

## 6. Risks and mitigations

| Risk                                                                            | Likelihood | Impact | Mitigation                                                                                                                                                                                           |
| ------------------------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auto-instrumentation adds latency to hot paths (every asyncpg query)            | Medium     | Medium | Benchmark before / after on a representative load test. Sampling is the lever — head-based sampling at < 1.0 in prod if overhead is real. Tail-based for error traces only.                          |
| Existing dashboards break when bespoke metric names disappear                   | High       | Medium | Step 2 + 3 keep the bespoke names emitting alongside the OTel names. Dashboard owners migrate at their own pace; we only stop emitting bespoke names after sign-off. Provide a migration map (§5.2). |
| OTLP collector misconfigured in some environment → silent loss of traces / logs | Medium     | High   | `TelemetryBootstrap` logs (to stderr) the resolved exporter endpoint at startup. Healthcheck endpoint reports otel exporter state. Staging burn-in for one week before prod cut over.                |
| Trace continuity across the queue boundary changes alert semantics              | Medium     | Medium | Feature-flagged. Default ON in dev / staging, OFF in prod for the first release; flip after dashboard owners confirm.                                                                                |
| `LoggingHandler` interacts poorly with structlog / current logger config        | Medium     | Medium | Smoke test in dev first. If the structured-JSON formatter has to coexist with `LoggingHandler`'s `OTLPLogExporter`, we run them in parallel (both processors on the same logger).                    |
| Existing call sites call `RuntimeTracer.start` without `with`                   | Low        | Medium | Audit call sites; OTel's `start_as_current_span` requires the context-manager form. Any non-context-manager use must convert.                                                                        |
| `TraceContext` is held across queue boundaries by current code                  | Medium     | High   | Inspect uses; convert to `traceparent` headers (W3C) on the queue command, with the consumer extracting them. If `TraceContext` is also stored in DB rows, decide schema migration in Step 2 review. |
| Span attribute size limits (OTLP defaults limit attribute string length)        | Low        | Low    | Configure attribute length limits explicitly. Redactor (separately scoped) ensures large payloads don't reach span attributes; here we set sane defaults (e.g. 4KB max per attribute).               |
| Deployment cost: OTLP collector now required in every environment               | Low        | Low    | Most teams already run a collector for the FastAPI / httpx instrumentation that exists today. If not, the no-op exporter (`OTEL_TRACES_EXPORTER=none`) is supported and documented.                  |
| Vendor migration: a team using DataDog tracer instead of OTel must re-validate  | Medium     | Medium | OTel exporters cover DataDog (`opentelemetry-exporter-datadog`) and most other vendors. Document the per-vendor exporter selection in Step 3.                                                        |

---

## 7. Test requirements

Per [`docs/CLAUDE.md`](../CLAUDE.md), unit testing requirements are explicit.

### 7.1 New unit tests

- `test_otel_bootstrap.py`
  - `TelemetryBootstrap.start()` is idempotent (calling twice does not double-instrument).
  - With `OTEL_TRACES_EXPORTER=none`, the tracer provider is the no-op provider; no spans flushed.
  - Resource attributes match `otel_config.OtelConfig.from_env()`.

- `test_runtime_tracer_shim.py`
  - `RuntimeTracer.start(name="x", attributes={...})` produces a span named `x` with the same attributes via OTel.
  - Nested calls preserve parent/child relationship.
  - `with` context manager exits with an "ok" status on normal exit; "error" on exception (mirrors current behavior).

- `test_usage_attribution_otel.py`
  - `attribute_usage(user_id="u", org_id="o", connector_id="c")` sets `user.id="u"`, `org.id="o"`, `connector.id="c"` on the active span.
  - With no active span, is a no-op (does not raise).

- `test_approval_metrics_otel.py`
  - `record_approval_requested(reason=...)` increments the `runtime.approval.requested` counter.
  - `record_approval_resolved(...)` increments `runtime.approval.resolved` and observes `runtime.approval.time_to_resolve`.

### 7.2 Snapshot / golden tests

- `test_span_names_snapshot.py`
  - Enumerates the set of `(span_name, attribute_keys)` produced by a representative run. Asserts equality with a checked-in golden file. Updates require explicit approval.

### 7.3 Integration tests

- `test_db_instrumentation.py`
  - Issue a real `asyncpg` query inside a span; assert the child span exists with `db.system="postgresql"` and a non-empty statement attribute (within configured limits).

- `test_log_correlation.py`
  - Inside a span, log a record at INFO level; the resulting log record's `trace_id` matches `trace.get_current_span().get_span_context().trace_id`.

- `test_cross_process_trace.py`
  - API enqueues a run command; worker claims it; the worker's run-handler span has the API request span as an ancestor via the W3C trace-context propagation.
  - Feature-flag the assertion: when `OTEL_PROPAGATE_QUEUE=false`, assert the worker span has no ancestor (current behavior).

### 7.4 Regression tests

- All tests under `tests/unit/observability/` pre-migration must still pass.
- `f1` through `f9` flow integration tests must produce the same set of `RuntimeTracer.start(name=...)` calls.

### 7.5 Manual verification (staging)

Documented as a checklist in the PR description, run before flipping default in prod:

1. Open the OTLP backend; confirm spans with `service.name=ai-backend` and `service.name=ai-backend-worker` are flowing.
2. Open the DB statement dashboard; confirm `db.client.duration` histogram has reasonable percentiles.
3. Pick a recent run; verify the trace tree has API → worker → run-handler → tool/MCP/subagent spans nested correctly.
4. Open a log line for that run; confirm `trace_id` matches the trace.
5. Diff the old `db_statement_metrics` dashboard against the new OTel-sourced one; both should agree on QPS / p99 within sampling noise.

---

## 8. Rollout / rollback

### 8.1 Rollout

1. **Step 1 PR — bootstrap parity.** OTel SDK + auto-instrumentations + log processor enabled. Bespoke systems untouched. Both emit. Burn-in in staging for one week.
2. **Step 2 PR — migrate RuntimeTracer + delete db_statement_metrics.** Behind `OTEL_TRACER_PRIMARY=true` in dev/staging; flip in prod after dashboard owners confirm parity (per checklist in [§7.5](#75-manual-verification-staging)).
3. **Step 3 PR — tighten and document.** Sampling config, deploy docs, retire bespoke metric emission once dashboard migration is signed off.

### 8.2 Rollback

- Step 1 — flip `OTEL_TRACES_EXPORTER=none`; the bespoke systems remain.
- Step 2 — revert: re-enable `db_statement_metrics.py` (kept as a deprecated module for the rollback window) and flip `OTEL_TRACER_PRIMARY=false`. Note: this requires not deleting the file in Step 2; instead deprecate-then-delete in Step 3 once Step 2 is stable.
- Step 3 — flip the bespoke names back on (the emit code is removed in Step 3 only after sign-off).

### 8.3 Observability for the rollout

- Daily diff between bespoke metric series and OTel-emitted equivalents during Step 2 burn-in. Threshold: < 1% drift, else investigate.
- Trace volume per environment, by sampler. Alert on a sudden drop (potential exporter failure).

---

## 9. Open questions

These should be resolved before Step 2 ships.

- **`TraceContext` storage outside spans.** Is `TraceContext` written to DB rows or queue command payloads anywhere? If yes, decide whether the persisted form is the OTel `traceparent` string (recommended) or stays as an envelope.
- **Sampling strategy.** Head-based at `OTEL_TRACES_SAMPLER=parentbased_traceidratio` with what ratio? Suggest 1.0 in dev, 0.1 in prod, 1.0 for error traces (tail-based — requires a collector with a tail sampler). Confirm with finance/ops.
- **DataDog / NewRelic tenants.** Which exporter ships in production today? `opentelemetry-exporter-datadog` and the OTLP-to-vendor bridge cover most cases. Confirm at Step 1.
- **Statement-text attribute redaction.** OTel asyncpg attaches the SQL statement as a span attribute. Sensitive parameters may show up in WHERE clauses. Confirm the redaction policy reaches span attributes too — coordinate with [`01-redaction-subsystem.md`](01-redaction-subsystem.md). Default safe choice: configure asyncpg instrumentation to omit statement parameters until the redactor PRD lands.
- **Worker auto-instrumentation parity.** Worker must call the same bootstrap. Verify the worker entrypoint (`runtime_worker/__main__.py`) imports `TelemetryBootstrap` before any work begins; otherwise spans before bootstrap go to the no-op tracer.
- **`approval_metrics` consumers.** Are any external systems pulling these metrics directly from a Prometheus exposition we control? If so, the Prometheus name mapping must match the OTel-to-Prometheus naming convention (dots → underscores). Verify in Step 1.

---

## 10. Done definition

- All tests in §7 added and green.
- Step 1 has shipped to prod; OTel pipeline is observed alongside the bespoke systems.
- Step 2 has shipped to prod; `db_statement_metrics.py` is deleted; `RuntimeTracer` is the shim; bespoke metric names continue to emit alongside OTel names.
- Dashboard owners have signed off on metric parity; bespoke emission removed in Step 3.
- Deploy docs (per environment) list required env vars and per-vendor exporter notes.
- This PRD is moved to `Status: Shipped` and the [roadmap](00-roadmap.md) status checkbox flipped.
