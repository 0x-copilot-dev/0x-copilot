# Database Observability (C11)

Two surfaces:

1. **`pg_stat_statements` scraper** — periodic `SELECT` from the
   extension's view, exporting per-digest counters to OTel. Query _text_
   never leaves the process.
2. **Slow-query OTel hook** — emits a `db.slow_query` span when a
   single statement exceeds `RUNTIME_DB_SLOW_QUERY_MS` (default 500 ms).
   Spans carry digest + duration only.

Both ship behind opt-in env vars — existing deploys don't suddenly start
scraping an extension they may not have installed.

## Configuration

```bash
# Scraper (worker process; default off)
RUNTIME_DB_STATEMENT_SCRAPE_ENABLED=true
RUNTIME_DB_STATEMENT_SCRAPE_INTERVAL_SECONDS=60

# Slow-query tracer threshold (default 500 ms)
RUNTIME_DB_SLOW_QUERY_MS=500
```

## Migration `0013_pg_stat_statements_grant.sql`

Grants `enterprise_app` SELECT on `pg_stat_statements` if the extension
is installed AND already created. The migration is **silently no-op**
when the extension isn't present, so it's safe to apply on managed
Postgres flavors that don't support it.

If your operator hasn't installed the extension, set
`RUNTIME_DB_STATEMENT_SCRAPE_ENABLED=false` (or leave it unset). The
scraper logs `db_statement_scrape_disabled` once per process and exits
the loop until restart.

## Privacy invariants

The two privacy invariants are enforced by tests:

1. **Query text never appears in metric labels.** The scraper SHA-256
   hashes the normalized statement (parameters → `$N`, whitespace
   collapsed, lowercased) and uses the first 16 hex chars as the
   `digest` label. Even if a literal sneaks into a SQL string, it gets
   hashed and discarded.
2. **Slow-query spans never carry query text.** The span has exactly
   two attributes: `db.statement.digest` and `db.statement.duration_ms`.
   No `db.statement` text attribute, no parameters, no SQL fragment.

If you add a new attribute to the slow-query span, update the privacy
test in
[services/ai-backend/tests/unit/agent_runtime/observability/test_db_statement_metrics.py](../../services/ai-backend/tests/unit/agent_runtime/observability/test_db_statement_metrics.py)
to assert the new attribute also doesn't leak literal data.

## Metrics emitted

| Metric                            | Type    | Labels   |
| --------------------------------- | ------- | -------- |
| `db_statement_calls_total`        | counter | `digest` |
| `db_statement_total_time_seconds` | counter | `digest` |
| `db_statement_rows_total`         | counter | `digest` |

## Sample dashboard

A starter Grafana JSON panel ships at
[infra/dashboards/db-statement-perf.json](../../infra/dashboards/db-statement-perf.json)
with three rows: top-10 slowest digests by `total_time_seconds`,
top-10 hottest by `calls_total`, and rows-per-call ratio.

## Backout

```bash
RUNTIME_DB_STATEMENT_SCRAPE_ENABLED=false
RUNTIME_DB_SLOW_QUERY_MS=999999          # effectively disable the hook
```

The scraper's loop exits on the next tick; the slow-query tracer
becomes a no-op for the next request.
