# RLS Incident Response

This runbook covers the Postgres Row-Level Security (C5) controls in the
`backend` and `ai-backend` services.

## What RLS does for us

Every tenant-scoped table is owned by `enterprise_admin` (BYPASSRLS) but
read/written by the application as `enterprise_app` (NO BYPASSRLS). Each
table has a `tenant_isolation` policy of the form:

```
USING (org_id = current_setting('app.current_org_id', true))
WITH CHECK (org_id = current_setting('app.current_org_id', true))
```

The store's connection-checkout helper sets `app.current_org_id` from the
verified request context on every checkout. Without the var set, RLS
returns zero rows — even for an authenticated app session — which converts
"forgot a `WHERE org_id = …`" bugs into a noisy failure instead of a silent
cross-tenant leak.

## Rollout state

The PR that introduced the policies (PR 15, C5) ships in three stages.
Inspect the rollout state on a given environment by checking which of the
following is present:

| Stage | Artifact applied                                                         | Expected state                                           |
| ----- | ------------------------------------------------------------------------ | -------------------------------------------------------- |
| 1     | `services/<svc>/migrations/0008_rls_tenant_isolation.sql` (yoyo)         | Roles + policies + grants exist; RLS NOT enabled.        |
| 2     | adapter checkout helpers stamp `app.current_org_id` on every connection. | `pg_stat_activity` shows the var on app connections.     |
| 3     | `services/<svc>/migrations/staged/do_rls.sql` applied via psql.          | `pg_class.relrowsecurity = TRUE` for every listed table. |

```sql
-- Stage 3 verification
SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class
 WHERE relname IN ('agent_conversations', 'mcp_servers', 'sessions')
 ORDER BY relname;
```

## Symptom: legitimate traffic returning zero rows / 404

Most likely a Stage-3 deploy reached a query path that did not flow through
`_tenant_connection` (or the backend equivalent). Confirm before rolling
back:

1. `SELECT current_setting('app.current_org_id', true)` from the app's
   active session (capture via `pg_stat_activity` + a follow-up query). If
   it is empty, the offending checkout is unstamped.
2. Tail JSON logs for the request id and locate the store call. Check that
   the helper was invoked with an `org_id` argument.
3. If a single endpoint is affected, patch the call site to plumb `org_id`
   through the helper. If multiple endpoints are affected and the data
   loss is high-severity, fall back to the backout below.

## Backout (Stage 5)

Apply `staged/undo_rls.sql` to disable RLS on every table:

```bash
PGAPPNAME=ai-backend:rls-stage5 \
psql "$DATABASE_URL" \
  -v ON_ERROR_STOP=1 \
  -f services/ai-backend/migrations/staged/undo_rls.sql

PGAPPNAME=backend:rls-stage5 \
psql "$DATABASE_URL" \
  -v ON_ERROR_STOP=1 \
  -f services/backend/migrations/staged/undo_rls.sql
```

The policies remain in place but dormant; once the missing checkout is
fixed, re-apply `staged/do_rls.sql` to re-enable.

## Operational maintenance

- **New tenant-scoped table**: add the table to (1) the GRANT list in the
  next migration that introduces it, (2) a `CREATE POLICY tenant_isolation`
  block, and (3) `staged/do_rls.sql` and `staged/undo_rls.sql`. The CI
  test in `services/<svc>/tests/integration/persistence/test_rls_isolation.py`
  enumerates tables and will fail if a new one is missing.
- **Cross-tenant operator queries** (worker outbox claim, backfill jobs):
  use the store's `_role_connection('worker')` (ai-backend) or pass
  `role='worker'` through the connection helper (backend). The
  `runtime_outbox_events.tenant_or_worker` policy grants access on
  `app.role='worker'`.
- **OAuth callback** (`pop_auth_session`): the row is keyed by a random
  `state` token, not by `org_id`. The backend's `_connect()` helper stamps
  only `app.role='api'` for this path. If Stage 3 surfaces this as a
  blocker, a follow-up PR will (a) embed a signed `org_id` in the state
  token, or (b) move the lookup behind a SECURITY DEFINER function
  registered against the BYPASSRLS owner.

## Known Stage-2b follow-ups

The following call sites do not yet stamp `app.current_org_id`. They are
safe in Stage 1+2 (RLS dormant) but will return zero rows once Stage 3
enables RLS:

- `ai-backend.update_run_usage_cost(run_id, …)` — keys on `run_id`; needs
  `org_id` plumbed from the worker.
- `ai-backend.update_model_call_usage_cost(usage_id, …)` — same shape.
- `ai-backend.lookup_pricing` / `upsert_pricing` — `model_pricing` is a
  global catalog, no policy applies; safe to leave unstamped.
- `ai-backend.list_runs_missing_cost` — backfill scan across tenants;
  intended to run via an operator role.
- `ai-backend.get_latest_sequence(run_id)` — keys on `run_id`; would need
  an org-aware refactor before Stage 3.
- `backend.pop_auth_session(state)` and `backend.get_token(server_id)` —
  see "OAuth callback" note above.
- All `services/backend/src/backend_app/service.py` `store.transaction()`
  callers — Stage 2b will plumb `org_id` through the keyword arg added to
  `transaction()` here.

The list above is the working ledger; update this section when each item
ships.

## Audit verification

```sql
-- Policies in place
SELECT schemaname, tablename, policyname, qual
  FROM pg_policies
 ORDER BY tablename, policyname;

-- App role lacks BYPASSRLS
SELECT rolname, rolbypassrls
  FROM pg_roles
 WHERE rolname IN ('enterprise_app', 'enterprise_admin');
```

Expected output: `enterprise_app` has `rolbypassrls = f`,
`enterprise_admin` has `rolbypassrls = t`.
