# PR 27 — C10: Read-replica Routing for Analytics

**Spec ID:** C10 | **Track:** Deployment & DB | **Wave:** 7 (Operations) | **Estimated effort:** M
**Depends on:** C4 (pool tuning), B4 (analytics endpoints)
**Required for:** none (operational maturity)

---

## 1. Functional Specification

### 1.1 Goal

Route declared analytics endpoints to a read replica when configured. Today every query — including expensive `/v1/usage/me?period=month` rollup queries — hits the primary. With B4 shipping at scale, analytics traffic will compete with write traffic.

### 1.2 User-visible behavior

- **Operator:** sets `RUNTIME_DB_READ_REPLICA_URL` → analytics endpoints route to replica. Without it, everything stays on primary.
- **End user:** no observable change other than slightly stale data (bounded by replica lag, default 30s tolerance).

### 1.3 Out of scope

- Multi-replica routing.
- Application-level connection multiplexing.
- Per-tenant read replica.

---

## 2. Technical Specification

### 2.1 Architecture

- Two pools per service: `_pool_primary` (existing) and `_pool_replica` (None unless URL set).
- New `_read_only_connection()` context manager picks replica when present; falls back to primary if replica unhealthy.
- New `@reader` decorator marks methods that go to replica. CI static check asserts no `INSERT|UPDATE|DELETE` in `@reader` methods.
- Health-checker monitors replica lag via `pg_stat_wal_receiver` or `SELECT extract(epoch from now() - pg_last_xact_replay_timestamp())`; failover to primary if lag > `RUNTIME_DB_READ_REPLICA_MAX_LAG_SECONDS`.

### 2.2 Schema changes

None.

### 2.3 Endpoints

None new. Affected endpoints (now `@reader`):

- `GET /v1/usage/me`
- `GET /v1/usage/me/conversations`
- `GET /v1/usage/conversations/{id}`
- `GET /v1/usage/org`
- (Run-status, conversation-list, message-history STAY on primary — real-time accuracy.)

### 2.4 Code changes

**Modify** [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py):

- Factor `_pool` into `_pool_primary` and `_pool_replica: AsyncConnectionPool | None`.
- New `@asynccontextmanager async def _read_only_connection(self, org_id: str)` — picks replica if healthy, else primary; sets `app.current_org_id` (RLS from C5).
- Health: poll every N seconds; track `replica_healthy: bool`; failover automatic.

**New decorator** in `services/ai-backend/src/agent_runtime/persistence/_reader.py`:

```python
def reader(method):
    method.__reader__ = True
    return method
```

**Annotate methods:** `query_user_daily`, `query_org_daily`, `query_run_breakdown`, `query_top_conversations` get `@reader`.

**CI static check** `tools/check_reader_methods.py` — walks the AST; for any method with `__reader__`, asserts SQL contains no INSERT/UPDATE/DELETE keywords.

### 2.5 Trust model & failure semantics

- Replica unhealthy → silent failover to primary; metric increments.
- Replica DOWN entirely → all reads on primary; degraded performance but no 5xx.
- RLS still enforced on replica (policies replicate by default).

### 2.6 Tenant isolation

Per C5 — tenant scoping on read replica is identical to primary.

### 2.7 Observability

- Metrics: `db_query_route{target=primary|replica, outcome}`, `db_replica_lag_seconds`, `db_replica_failover_total`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] `RUNTIME_DB_READ_REPLICA_URL` unset → all queries hit primary (verify via `pg_stat_activity.application_name`).
- [ ] Set → `@reader` queries hit replica; non-reader queries hit primary.
- [ ] Replica down → reads transparently fall back to primary within `RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS`.
- [ ] Replica lag exceeds threshold → automatic failover to primary; recovers when lag drops.
- [ ] CI static check fails when a `@reader` method contains a write statement.

### 3.2 Test plan

**Unit:**

- Decorator marks methods.
- CI check identifies write keywords in fixture method.

**Integration:**

- Two-Postgres test fixture (primary + replica).
- `@reader` method verified to land on replica via `application_name`.
- Stop replica → next call falls back to primary.
- High-lag fixture → failover.

### 3.3 Compliance evidence produced

- Read isolation for analytics; primary protected from analytics load.
- CI prevents accidental writes on read-only path.

### 3.4 Rollout plan

- Optional. Set env var per environment.

### 3.5 Backout plan

Unset env var. All reads return to primary.

### 3.6 Definition of done

- [ ] Pool factored.
- [ ] All B4 query methods annotated `@reader`.
- [ ] Health checker + failover wired.
- [ ] CI static check landed.
- [ ] Metrics dashboarded.

---

## 4. Critical files

- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py)
- New: `services/ai-backend/src/agent_runtime/persistence/_reader.py`
- New: `tools/check_reader_methods.py`
- Modify: [services/ai-backend/src/runtime_api/http/routes.py](../../services/ai-backend/src/runtime_api/http/routes.py) — usage endpoints use reader path.
