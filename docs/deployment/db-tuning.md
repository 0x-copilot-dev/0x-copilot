# Database Pool Tuning

This document describes the env-var-driven Postgres pool configuration shared by
`services/backend` and `services/ai-backend` (C4 — Wave 1). Defaults are
production-safe; operators tune per profile via deploy YAML.

## Knobs

| Var (RUNTIME prefix shown)                | Default | Purpose                                                  |
| ----------------------------------------- | ------- | -------------------------------------------------------- |
| `RUNTIME_DB_POOL_MIN_SIZE`                | 5       | Warm connections kept open across requests.              |
| `RUNTIME_DB_POOL_MAX_SIZE`                | 50      | Hard cap before `db_pool_waiting` starts incrementing.   |
| `RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS` | 5       | Block-and-fail if the pool is saturated.                 |
| `RUNTIME_DB_STATEMENT_TIMEOUT_MS`         | 10000   | Server-side per-statement cap. Prevents runaway queries. |
| `RUNTIME_DB_LOCK_TIMEOUT_MS`              | 3000    | Server-side per-lock-wait cap. Fails fast on contention. |
| `RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS`       | 30000   | Server kills connections holding `BEGIN` past this.      |

`services/backend` mirrors all six with `BACKEND_DB_*` prefix. Identical
semantics, identical defaults — pick the prefix that matches the service.

## Profile-specific recommended values

Production deploy YAML (Helm or Compose) should set explicit values per
deployment profile:

### `saas_multi_tenant`

```yaml
RUNTIME_DB_POOL_MIN_SIZE: "10"
RUNTIME_DB_POOL_MAX_SIZE: "100"
RUNTIME_DB_STATEMENT_TIMEOUT_MS: "10000"
RUNTIME_DB_LOCK_TIMEOUT_MS: "3000"
RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS: "30000"
```

The worker process gets the same caps but typically runs hotter on long
transactions; bump `RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS` only with reason.

### `single_tenant_managed`

```yaml
RUNTIME_DB_POOL_MIN_SIZE: "5"
RUNTIME_DB_POOL_MAX_SIZE: "50"
```

Defaults work for typical single-tenant load. Lower caps if running on a
small managed Postgres tier (RDS db.t4g.medium and below).

### `single_tenant_self_hosted`

Same as managed unless the operator has DBA telemetry showing pool
saturation; then double `MAX_SIZE` and watch `db_pool_waiting`.

## Application name and pg_stat_activity

Every connection sets `application_name=<service>:<role>` so on-call
engineers can grep `pg_stat_activity` per process:

```sql
SELECT application_name, count(*)
  FROM pg_stat_activity
 GROUP BY 1
 ORDER BY 2 DESC;
```

Expected rows in production: `ai-backend:api`, `ai-backend:worker`,
`backend:api`. Anything else is a misconfigured service.

## Metrics

OTel meters exported per pool:

- `db_pool_size{service,role}` — observable gauge.
- `db_pool_in_use{service,role}` — observable gauge.
- `db_pool_waiting{service,role}` — observable gauge.
- `db_pool_acquire_seconds{service,role}` — histogram.
- `db_optimistic_retry_total{table,outcome}` — counter
  (`outcome ∈ {success, exhausted}`).
- `db_atomic_upsert_total{table,outcome}` — counter
  (`outcome ∈ {insert, update, conflict_rejected}`).

## Troubleshooting pool saturation

1. **`db_pool_waiting > 0` for sustained periods** → increase
   `_POOL_MAX_SIZE`, but only after confirming the DB has the headroom
   (`pg_stat_activity` count is below the server's `max_connections`).
2. **`db_pool_acquire_seconds_p99` > 100ms** → either you're saturated
   _and_ the DB can serve more, _or_ a slow query is holding connections.
   Check `pg_stat_activity` filtered by `application_name` and look for
   queries with elapsed time > 1s.
3. **Frequent `query_canceled` errors** → `_STATEMENT_TIMEOUT_MS` is too
   tight for legitimate workloads, OR you have an O(N) scan that needs an
   index. Don't blanket-bump the timeout — fix the query first.
4. **Frequent `lock_not_available` errors** → contention on a row or
   table. Check whether a long-running write txn is holding a lock
   (`pg_stat_activity.wait_event_type='Lock'`).
5. **Idle-in-transaction kills** → app code has a `BEGIN` followed by an
   `await` that yielded longer than `_IDLE_IN_TXN_TIMEOUT_MS`. Audit
   transaction scopes — never hold a txn across an external HTTP call.
