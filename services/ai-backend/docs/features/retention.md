# Data Retention

How retention policies are resolved, how the sweeper job expires data, and how
the backfill job populates `retention_until` on existing rows.

See also:

- [architecture/02-contracts.md](../architecture/02-contracts.md) — `PersistencePort.sweep_retention_kind()`
- [reference/env-vars.md](../reference/env-vars.md) — `RUNTIME_RETENTION_*` vars

---

## What it does

Each conversation and its associated data (messages, runs, events, approvals, citations,
drafts, subagent records) has a `retention_until` timestamp. The retention sweeper job
periodically deletes rows past their deadline. A separate backfill job populates
`retention_until` on rows created before retention was added.

---

## Key modules

| File                                             | Role                                                                       |
| ------------------------------------------------ | -------------------------------------------------------------------------- |
| `agent_runtime/retention/policy_resolver.py`     | `RetentionPolicyResolver` — maps workspace config to a concrete `datetime` |
| `runtime_worker/jobs/retention_sweeper.py`       | Background job: sweep rows past `retention_until`                          |
| `runtime_worker/jobs/retention_backfill.py`      | One-time job: populate `retention_until` on pre-existing rows              |
| `agent_runtime/persistence/records/retention.py` | `RetentionPolicyRecord` — persisted policy config                          |
| `runtime_api/http/retention_routes.py`           | Admin endpoints for viewing/managing retention policies                    |
| `runtime_api/schemas/retention.py`               | `RetentionPolicyConfig`, `RetentionKind`                                   |

---

## Policy resolution

`RetentionPolicyResolver.resolve(org_id, workspace_config)`:

1. Reads `RetentionPolicyRecord` for the org from `PersistencePort.list_retention_policies()`.
2. Returns a `datetime` (`now() + retention_days`) that becomes the `retention_until`
   timestamp on new rows.
3. Falls back to the global default (`RUNTIME_RETENTION_DEFAULT_DAYS`) if no org-level
   policy exists.

The `retention_until` is set at conversation creation time and stamped on every
child row (messages, runs, events, approvals).

---

## Retention kinds (`RetentionKind`)

| Kind           | What it sweeps                           |
| -------------- | ---------------------------------------- |
| `CONVERSATION` | Conversation rows + all child messages   |
| `RUN`          | Run rows + all child events              |
| `APPROVAL`     | Approval rows past deadline              |
| `CITATION`     | Citation and source rows                 |
| `DRAFT`        | Draft rows                               |
| `SUBAGENT`     | Subagent run records                     |
| `AUDIT`        | Audit event rows (longer default period) |

Each kind has its own sweep pass. `PersistencePort.sweep_retention_kind(kind, cutoff_at)`
is the single delete call per kind.

---

## Retention sweeper job

`runtime_worker/jobs/retention_sweeper.py`

Runs on a configurable interval (`RUNTIME_RETENTION_SWEEP_INTERVAL_SECONDS`):

1. Computes `cutoff_at = now()`.
2. For each `RetentionKind`, calls `persistence.sweep_retention_kind(kind, cutoff_at)`.
3. Records the row count deleted per kind as an observability metric.
4. Emits a `RETENTION_SWEEP_COMPLETED` internal event (not user-visible).

The sweep is batched to avoid long-running transactions. Each kind runs in its own
transaction.

---

## Retention backfill job

`runtime_worker/jobs/retention_backfill.py`

One-time job for rows created before `retention_until` columns were added:

1. Finds rows with `retention_until IS NULL`.
2. Applies the default retention policy to compute a `retention_until` value.
3. Updates rows in batches.

Run via `RUNTIME_ENABLE_RETENTION_BACKFILL=true` on worker startup (idempotent —
rows already populated are skipped).

---

## Legal hold

Rows with `legal_hold=true` are excluded from retention sweeps regardless of
`retention_until`. Legal hold is set via the admin retention routes. This is a
deployment control — audit of hold/release actions is logged.

---

## Observability

`agent_runtime/observability/retention_metrics.py` — exposes:

- `retention_sweep_rows_deleted_total` (counter, per kind)
- `retention_sweep_duration_seconds` (histogram)
- `retention_backfill_rows_updated_total` (counter)
