# PR 15 — C5: Postgres Row-Level Security for Tenant Isolation

**Spec ID:** C5 | **Track:** Deployment & DB | **Wave:** 3 (Parallel) | **Estimated effort:** L
**Depends on:** C2 (migrations), C3 (atomicity — needed for connection-checkout helper)
**Required for:** all bank/gov deploys

---

## 1. Functional Specification

### 1.1 Goal

Defense-in-depth tenant isolation. Today, every query manually filters by `org_id` — if any code path forgets, cross-tenant data leaks. Postgres RLS makes this a property of the database, not the application: even a `SELECT * FROM agent_messages` with no WHERE clause returns 0 rows for the wrong tenant.

### 1.2 User-visible behavior

- **End user:** none.
- **Operator:** can prove tenant isolation via a DB-level test, not just app-level.
- **Auditor:** can verify the policies via `SELECT * FROM pg_policies`.

### 1.3 Out of scope

- Changing application query patterns (app still does `WHERE org_id = …`).
- Per-row encryption (C7).
- Read-replica routing (C10).

---

## 2. Technical Specification

### 2.1 Architecture

- Two DB roles: `enterprise_app` (RLS enforced — used by app pools), `enterprise_admin` (BYPASSRLS — used by yoyo runner only with `app.is_migration='on'`).
- Every tenant-scoped table gets `ENABLE ROW LEVEL SECURITY` and a `tenant_isolation` policy: `org_id = current_setting('app.current_org_id', true)`.
- Connection checkout helper sets `app.current_org_id` once per checkout from the verified request context.
- Worker uses a separate session-var `app.role='worker'` for outbox claims (separate `worker_can_read_all` policy).

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0007_rls_tenant_isolation.sql` (analogous file in backend service for its 6 tables):

```sql
-- 1. Roles
CREATE ROLE enterprise_app NOINHERIT;
CREATE ROLE enterprise_admin BYPASSRLS NOINHERIT;
GRANT enterprise_app TO <connection_user>;  -- the user the app actually authenticates as
GRANT enterprise_admin TO <migration_user>;

-- 2. Per-table policies (one section per table; this excerpt covers agent_conversations)
ALTER TABLE agent_conversations ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agent_conversations
    USING (org_id = current_setting('app.current_org_id', true));
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_conversations TO enterprise_app;

-- ... repeated for all 19 ai-backend tenant-scoped tables ...

-- 3. Worker access to outbox (cross-tenant by design)
CREATE POLICY worker_can_read_all ON runtime_outbox_events
    USING (current_setting('app.role', true) = 'worker' OR org_id = current_setting('app.current_org_id', true));
```

Rollback: `ALTER TABLE … DISABLE ROW LEVEL SECURITY` for each table; `DROP POLICY` for each policy.

### 2.3 Endpoints

None.

### 2.4 Code changes

**New connection-checkout helper** in [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py):

```python
@asynccontextmanager
async def _tenant_connection(self, org_id: str) -> AsyncIterator[AsyncConnection]:
    async with self._pool.connection() as conn:
        await conn.execute(
            "SELECT set_config('app.current_org_id', %s, true)",
            (org_id,)
        )
        yield conn
```

Same pattern in [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) using sync psycopg.

Worker uses `_role_connection('worker')` for outbox claims:

```python
@asynccontextmanager
async def _role_connection(self, role: str) -> AsyncIterator[AsyncConnection]:
    async with self._pool.connection() as conn:
        await conn.execute("SELECT set_config('app.role', %s, true)", (role,))
        yield conn
```

**Refactor every store method that takes `org_id`** to use `_tenant_connection(org_id)` instead of `_pool.connection()`. The org_id always comes from the verified request context (not request body).

**Migration runner update (from C2):** The runner connects as `enterprise_admin` and sets `app.is_migration='on'`. Yoyo handles the connection lifecycle.

### 2.5 Trust model & failure semantics

- Forgetting to set `app.current_org_id` → reads return 0 rows (catches the bug at test time).
- App role has no DDL grants — accidental migrations through the app pool fail.
- BYPASSRLS available only to the migration role.
- Tampering with `app.current_org_id` would require a session that's already authenticated as `enterprise_app` — the gate is the network/auth layer, not RLS itself. RLS is a backstop.

### 2.6 Tenant isolation

**This PR is the tenant-isolation hardening.** Critical tests below.

### 2.7 Observability

- Metric: `db_rls_violation_total{table}` — incremented when an attempted query returns 0 rows due to RLS (caught via a test wrapper, not in production hot path).
- Audit row written when migration role used in production.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] All 19 ai-backend tenant-scoped tables have RLS enabled and a `tenant_isolation` policy.
- [ ] All 6 backend tenant-scoped tables have RLS enabled and a policy.
- [ ] App role `enterprise_app` has CRUD grants but NOT BYPASSRLS.
- [ ] Migration role `enterprise_admin` has BYPASSRLS.
- [ ] Connection checkout helper sets `app.current_org_id` for every tenant-scoped query.
- [ ] Worker outbox loop sets `app.role='worker'` once per checkout.

### 3.2 Test plan

**Critical integration test** `tests/integration/persistence/test_rls_isolation.py`:

1. Connect as `enterprise_app`.
2. `set_config('app.current_org_id', 'org_a', true)`; insert `agent_conversations` row.
3. `set_config('app.current_org_id', 'org_b', true)`; SELECT → empty.
4. UPDATE `WHERE id = <org_a's id>` → 0 rows updated.
5. DELETE → 0 rows.
6. Repeat for every tenant-scoped table.

**Negative test:**

- Open connection without setting `app.current_org_id` → SELECT returns empty for every tenant-scoped table.

**Worker test:**

- Outbox claim with `app.role='worker'` succeeds across tenants.
- Outbox claim without `app.role` set → empty.

**Regression:**

- Full ai-backend test suite passes (every test fixture sets the org_id correctly).

### 3.3 Compliance evidence produced

- DB-enforced tenant isolation, demonstrated by integration test against the `enterprise_app` role.
- Migration auditability via separate role.
- Runbook `docs/security/rls-incident-response.md` covers how to bypass for incident response.

### 3.4 Rollout plan (3 stages — load-bearing)

1. **Stage 1 (this PR):** add RLS policies to migrations but `ENABLE ROW LEVEL SECURITY` is gated by a separate `do_rls.sql` patch checked in but not applied.
2. **Stage 2:** app code starts setting `app.current_org_id` on every checkout. RLS still disabled. Verify in production logs that no query path reaches DB without the var set (instrumented).
3. **Stage 3 (separate small PR):** apply `do_rls.sql` to enable RLS. Monitor for query failures.

### 3.5 Backout plan

`ALTER TABLE … DISABLE ROW LEVEL SECURITY` per table in a hot patch.

### 3.6 Definition of done

- [ ] Migration 0007 (ai-backend) and equivalent (backend) applied.
- [ ] Both connection-checkout helpers wired into all store methods.
- [ ] All adapter methods refactored to take `org_id` and use the helper.
- [ ] RLS isolation integration test passes against `enterprise_app` role.
- [ ] All existing test suites continue to pass.
- [ ] Stage 3 rollout tracked in operational runbook.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0007_rls_tenant_isolation.sql` (+ rollback)
- New: equivalent migration in backend service.
- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — `_tenant_connection`, `_role_connection`, refactor every method.
- Modify: [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) — sync equivalent.
- Modify: every worker handler that touches DB to use the right helper.
- New: `tests/integration/persistence/test_rls_isolation.py` per service.
- New: `docs/security/rls-incident-response.md`
