# PR 28 — C11: pg_stat_statements + Slow Query Observability

**Spec ID:** C11 | **Track:** Deployment & DB | **Wave:** 7 (Operations) | **Estimated effort:** S/M
**Depends on:** C4 (pool tuning sets `application_name`)
**Required for:** none (operational maturity)

---

## 1. Functional Specification

### 1.1 Goal

Surface per-statement performance and per-tenant query distribution. Today we have pool-level metrics (C4) but no statement-level visibility. On-call engineers can't tell which query is slow without manual `EXPLAIN`.

### 1.2 User-visible behavior

- **On-call:** sees slow queries in OTel/Prometheus with statement digest, latency histogram, per-tenant breakdown.
- **Operator:** dashboards show top-N slow queries by service.

### 1.3 Out of scope

- Query-rewriting suggestions.
- Auto-indexing.

---

## 2. Technical Specification

### 2.1 Architecture

- Assumes `pg_stat_statements` extension is preinstalled by operator (most managed Postgres has it; documented as a deploy prereq).
- Migration grants `enterprise_app` SELECT on `pg_stat_statements`.
- Background scraper polls `pg_stat_statements` every minute; exports to Prometheus tagged by query digest (NOT raw text).
- Per-tenant tagging via `application_name` containing `sha256(org_id)[:8]` — full org_id never leaks to `pg_stat_activity`.
- Connection-level slow-query hook emits OTel span when `query_duration > RUNTIME_DB_SLOW_QUERY_MS` (default 500).

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0012_pg_stat_statements_grant.sql`:

```sql
GRANT SELECT ON pg_stat_statements TO enterprise_app;
```

(If extension not installed, migration logs a warning and continues — feature is opt-in.)

### 2.3 Endpoints

None.

### 2.4 Code changes

**Modify** [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) `_build_pool_kwargs` (from C4):

- Update `application_name` to `f"ai-backend:{role}:{org_hash[:8]}"` when org_id is known at query time. (For pool-level hot-swap, this means we set `app.app_name` via session var per checkout, similar to RLS in C5.)

Actually simpler approach: stays as `f"ai-backend:{role}"` at the pool level; per-query tenant tag is added via SQL comment that pg_stat_statements normalizes out. Skip the hot-swap.

**New** `services/ai-backend/src/agent_runtime/observability/db_metrics.py`:

- `DbStatementMetricsCollector` — scheduled task; SELECTs from `pg_stat_statements`; aggregates by query digest; exports to Prometheus.
- Metrics: `db_statement_calls_total{digest}`, `db_statement_total_time_seconds{digest}`, `db_statement_rows_total{digest}`.

**New** psycopg connection-level hook for slow query OTel:

```python
class SlowQueryTracer:
    def on_query_complete(self, query, duration_ms):
        if duration_ms > self.threshold_ms:
            with tracer.start_as_current_span("db.slow_query") as span:
                span.set_attribute("db.statement.digest", _digest(query))
                span.set_attribute("db.statement.duration_ms", duration_ms)
                # NEVER log query text — may contain PII via literals
```

Wired via psycopg's `Connection.log_messages` or a custom `Cursor` wrapper.

### 2.5 Trust model & failure semantics

- If `pg_stat_statements` not installed: scraper logs once, exits; no metrics produced.
- Query text NEVER exported as a metric label or OTel attribute (only digest).
- Slow-query span has NO query text; only digest + duration + service/role.

### 2.6 Tenant isolation

- Tenant tag uses `sha256(org_id)[:8]` — irreversible without rainbow table; even then, only correlates buckets.
- Full org_id NEVER appears in pg_stat_activity, OTel, or Prometheus.

### 2.7 Observability

This is the observability PR. All metrics described above.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Inject a slow query → OTel span emitted with right attributes (digest only).
- [ ] Query text never appears in metrics or spans.
- [ ] `pg_stat_statements` data exported to Prometheus.
- [ ] Plaintext PII never appears in any exported metric.

### 3.2 Test plan

**Unit:**

- Digest function deterministic and one-way.
- Slow-query hook fires only when above threshold.
- Span has no query text attribute.

**Integration:**

- Run a slow query against test DB; assert OTel span emitted.
- Scraper runs against test DB with pg_stat_statements; metrics populated.

**Privacy:**

- Inject a query with PII literal; assert it appears nowhere in metrics/spans.

### 3.3 Compliance evidence produced

- Statement-level observability with no PII leakage.

### 3.4 Rollout plan

Behind env var. Default off; enable per environment.

### 3.5 Backout plan

Disable scraper task; remove the hook.

### 3.6 Definition of done

- [ ] Migration 0012 applied (or no-op'd on non-pg_stat_statements DBs).
- [ ] Scraper + hook live.
- [ ] Privacy test passes.
- [ ] Sample dashboard checked into `infra/`.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0012_pg_stat_statements_grant.sql` (+ rollback no-op)
- New: `services/ai-backend/src/agent_runtime/observability/db_metrics.py`
- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — slow-query hook.
- New: `infra/dashboards/db-statement-perf.json`
