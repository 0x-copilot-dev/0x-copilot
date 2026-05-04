# Read-replica Routing (C10)

Analytics endpoints (B4 usage queries) can target a Postgres read replica
when one is configured. Real-time correctness paths (run status,
conversation list, message history) stay on primary.

## Configuration

```bash
RUNTIME_DB_READ_REPLICA_URL=postgres://reader@replica:5432/runtime
RUNTIME_DB_READ_REPLICA_MAX_LAG_SECONDS=30  # reserved for the lag-watch follow-up
```

Without `RUNTIME_DB_READ_REPLICA_URL`, every query hits primary —
behavior is unchanged from C4.

## What's routed to the replica

Methods marked `@reader` in
[services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py):

- `query_user_daily_usage`
- `query_org_daily_usage`
- `query_top_conversations`
- `query_model_call_usage_for_run`

These power `/v1/usage/me`, `/v1/usage/me/conversations`,
`/v1/usage/conversations/{id}`, `/v1/usage/org`, and the run breakdown.

## What stays on primary

- Run status (`/v1/agent/runs/{id}`) — must reflect the latest write.
- Conversation list / message history — same reason.
- Every write path. The CI check
  [tools/check_reader_methods.py](../../tools/check_reader_methods.py)
  rejects any `@reader` method containing an `INSERT|UPDATE|DELETE|TRUNCATE|MERGE`
  keyword in a string literal.

## Failover semantics

- Replica unhealthy at boot → silent degrade to primary; `_replica_healthy`
  flag stays False until restart.
- Replica errors mid-query → caught at the connection boundary, retried
  on primary inside the same handler; the second attempt re-runs the SQL.
- Operator restart re-tries the replica.

A more sophisticated lag watcher (poll
`pg_last_xact_replay_timestamp()` / `pg_stat_wal_receiver`) and explicit
`RUNTIME_DB_REPLICA_FAILOVER_TOTAL` metric ship as a follow-up.

## RLS on the replica

Postgres replicates policies by default, so the C5 tenant-isolation
policies apply to reads against the replica. The store still calls
`set_config('app.current_org_id', ..., true)` per checkout.

## Rollout

Set the env var per environment after verifying replica connectivity.
No schema changes needed.

## Backout

Unset `RUNTIME_DB_READ_REPLICA_URL` and restart. All reads return to
primary; no migration needed.
