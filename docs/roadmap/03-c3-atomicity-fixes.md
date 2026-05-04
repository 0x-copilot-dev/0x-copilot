# PR 03 — C3: Atomic upserts + transaction boundaries + optimistic locking

**Spec ID:** C3 | **Track:** Deployment & DB | **Wave:** 1 (Atomicity) | **Estimated effort:** M
**Depends on:** C2 (migration tooling)
**Required for:** B7 (budget enforcement reuses optimistic-lock pattern)

---

## 1. Functional Specification

### 1.1 Goal

Three confirmed correctness bugs ship as one PR because they share the same review surface (store + service layer in `services/backend`) and individually they are too small to be worth separate review:

1. **`PostgresMcpStore.put_token` does DELETE-then-INSERT** instead of an atomic upsert. There is a window where a process kill leaves the user's MCP server with no usable token.
2. **`services/backend` audit writes are not in the same transaction as the primary write.** A successful `create_skill` followed by a crashed `append_skill_audit` leaves an audit gap. Confirmed sites: `create_skill` (L673–702), `update_skill` (L722–763), `delete_skill`, `_ensure_preloaded_skills` (L880+), all MCP CRUD.
3. **Dead optimistic-lock columns.** `agent_runs.row_version` defaults to 1 but is never compared on UPDATE. `runtime_memory_items.version` is read but never enforced as compare-and-swap. We either use them or remove them — this PR uses them.

### 1.2 User-visible behavior

- **End user:** no observable change in the happy path. Under failure, the system is more correct (no orphaned tokens, no missing audit rows).
- **Operator:** new `ConcurrentRunUpdateError` log lines (rare; means worker retried successfully).
- **Auditor:** "every state-changing op has a paired audit row" becomes a verifiable invariant.

### 1.3 Out of scope

- Sync→async migration of `services/backend` (deferred; documented separately).
- Wrapping ai-backend service-layer composites — those already happen inside route-level transactions; only a CI static check is added here to keep them that way.

---

## 2. Technical Specification

### 2.1 Architecture

Three changes, three migrations, one combined PR.

### 2.2 Schema changes

Migration `services/backend/migrations/0003_mcp_auth_connections_unique.sql`:

```sql
-- Required by ON CONFLICT target. Today there is no unique constraint.
CREATE UNIQUE INDEX idx_mcp_auth_connections_server
    ON mcp_auth_connections (server_id);
```

Rollback: `DROP INDEX idx_mcp_auth_connections_server`.

No other schema changes — `agent_runs.row_version` and `runtime_memory_items.version` already exist.

### 2.3 Endpoints

None.

### 2.4 Code changes

**Change 1 — Atomic upsert in `put_token`:**
[services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) `PostgresMcpStore.put_token`:

```sql
INSERT INTO mcp_auth_connections (
    connection_id, server_id, org_id, user_id,
    encrypted_access_token, encrypted_refresh_token, expires_at,
    created_at, updated_at
)
VALUES (%(connection_id)s, %(server_id)s, %(org_id)s, %(user_id)s,
        %(encrypted_access_token)s, %(encrypted_refresh_token)s, %(expires_at)s,
        %(created_at)s, %(updated_at)s)
ON CONFLICT (server_id) DO UPDATE SET
    encrypted_access_token = EXCLUDED.encrypted_access_token,
    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
    expires_at = EXCLUDED.expires_at,
    updated_at = EXCLUDED.updated_at,
    user_id = EXCLUDED.user_id
WHERE mcp_auth_connections.org_id = EXCLUDED.org_id  -- cross-tenant guard
```

If WHERE clause rejects the conflict resolution (org mismatch), the statement returns 0 rows; we raise `CrossTenantWriteError` with a safe error message.

**Change 2 — Service-layer transactions:**
Refactor [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) store methods to accept an optional `conn` parameter; when omitted, the method opens its own connection (current behavior). Service layer composes inside one transaction:

```python
# services/backend/src/backend_app/service.py — example
def create_skill(self, request: SkillCreateRequest) -> SkillRecord:
    audit = ...  # build audit record
    record = ...  # build skill record
    with self._pool.connection() as conn:
        with conn.transaction():
            self._store.create_skill(record, conn=conn)
            self._store.append_skill_audit(audit, conn=conn)
    return record
```

Sites that need wrapping (line numbers approximate, audit before merge):

- `create_skill` (L673–702)
- `update_skill` (L722–763)
- `delete_skill`
- `_ensure_preloaded_skills` (L880+)
- `create_server`, `update_server`, `delete_server` (all paired with `append_audit`)

**CI static check** — new `tools/check_audit_in_transaction.py`. Walks the service module AST; flags any call sequence `<store>.<write_method>(...)` followed by `append_audit(...)` outside a `with conn.transaction():` block. Fails build on violation.

**Change 3 — Optimistic locking:**
[services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) updates:

`update_run_status`, `set_run_latest_sequence`, and any other `UPDATE agent_runs ... WHERE id = ?` adds:

```sql
UPDATE agent_runs SET
    status = %s,
    row_version = row_version + 1,
    ...
WHERE id = %s AND row_version = %s
RETURNING row_version
```

On rowcount=0, raise typed `ConcurrentRunUpdateError(run_id, expected_version)`. Caller (worker handler) refetches and retries with bounded backoff (max 3 retries; new helper `with_optimistic_retry()`).

Same pattern for `runtime_memory_items` writes: WHERE includes the current `version`; on miss, raise `ConcurrentMemoryItemUpdateError`.

New typed errors land in `services/ai-backend/src/agent_runtime/persistence/records/errors.py`.

### 2.5 Trust model & failure semantics

- **`put_token`:** atomic; either both rows exist or only the prior row.
- **Service composites:** atomic via Postgres transaction; rollback on any participant failure.
- **Optimistic CAS:** detects concurrent writes; caller retries from fresh read. Bounded retries prevent livelock.

### 2.6 Tenant isolation

Cross-tenant guard in `put_token` ON CONFLICT prevents one org's token write from accidentally overwriting another org's row even if the unique key collides. Test: insert org_a's token, then attempt `put_token` with same `server_id` from org_b → `CrossTenantWriteError` raised; org_a's token unchanged.

### 2.7 Observability

- New metric: `db_optimistic_retry_total{table,outcome=success|exhausted}`.
- New metric: `db_atomic_upsert_total{table,outcome=insert|update|conflict_rejected}`.
- Audit-in-transaction static check runs in CI on every PR.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] `put_token` is a single-statement atomic upsert; no observable window with no token.
- [ ] Every (write + audit) site in `services/backend/src/backend_app/service.py` is inside one transaction.
- [ ] CI fails when a new audit-write site is added outside a transaction.
- [ ] Concurrent `update_run_status` from two writers: one succeeds, one retries with fresh state.

### 3.2 Test plan

**Unit:**

- `test_put_token_atomic` — concurrent puts; one row remains; never zero rows visible.
- `test_put_token_cross_tenant_rejected` — org_b cannot overwrite org_a's row.
- `test_audit_rolled_back_on_primary_failure` — monkey-patch primary write to raise; assert no audit row written.
- `test_audit_rolled_back_on_audit_failure` — monkey-patch audit write to raise; assert no primary row written.
- `test_optimistic_cas_detects_concurrent_write` — two writers, one stale row_version → raises.
- `test_optimistic_retry_succeeds_after_backoff` — first attempt fails with stale; helper retries with fresh; second attempt succeeds.

**Integration:**

- Crash injection during `create_skill` (kill -9 between primary and audit) → no skill row, no audit row.
- 50 concurrent `put_token` calls for same server_id → exactly one row, no flap.

**CI:**

- Static check `tools/check_audit_in_transaction.py` integrated into `pre-commit` and CI.

### 3.3 Compliance evidence produced

- Audit-completeness invariant becomes verifiable: every state-changing op produces an audit row, atomically.
- Token availability under crash satisfies "no observable window with missing credential."

### 3.4 Rollout plan

- Backward compatible. Atomic upsert works with or without prior data; transactions are tighter than before but functionally equivalent in the happy path.
- Optimistic CAS is enabled immediately; the worker retry helper hides retries from callers.

### 3.5 Backout plan

Revert. Note: the unique index from the migration can stay; nothing depends on its absence.

### 3.6 Definition of done

- [ ] Migration 0003 applied.
- [ ] All three changes implemented + tests passing.
- [ ] CI static check runs and is green.
- [ ] No new dead `row_version` / `version` columns; both are now load-bearing.
- [ ] Docs updated: `docs/architecture/persistence.md` notes the optimistic-lock pattern as the standard for run/memory writes.

---

## 4. Critical files

- Modify: [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) — `put_token` (Change 1), all store methods take optional `conn` (Change 2).
- Modify: [services/backend/src/backend_app/service.py](../../services/backend/src/backend_app/service.py) — wrap (write+audit) sites in transactions.
- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — CAS on `update_run_status`, `set_run_latest_sequence`, memory item writes.
- New: `services/ai-backend/src/agent_runtime/persistence/records/errors.py` — typed concurrent-update errors.
- New: `services/ai-backend/src/agent_runtime/persistence/optimistic.py` — `with_optimistic_retry()` helper.
- New: `services/backend/migrations/0003_mcp_auth_connections_unique.sql` (+ rollback)
- New: `tools/check_audit_in_transaction.py`
