# PR 04 — C4: Connection pool tuning, timeouts, and pool metrics

**Spec ID:** C4 | **Track:** Deployment & DB | **Wave:** 1 (Atomicity) | **Estimated effort:** S
**Depends on:** none (parallel to C3)
**Required for:** every later DB-touching PR (defines the pool baseline)

---

## 1. Functional Specification

### 1.1 Goal

Promote pool sizing and statement-level timeouts from hardcoded values (or _no_ values, in `services/backend`) to env-var-driven config with safe production defaults. Add Prometheus pool-health metrics. Set per-service+role `application_name` so `pg_stat_activity` is greppable.

### 1.2 User-visible behavior

- **Operators** can tune pool size and timeouts per environment without code changes.
- **On-call engineers** can grep `pg_stat_activity` by `application_name` to see which service+role holds long-running statements.
- **Dashboards** show pool saturation in real time.

### 1.3 Out of scope

- Read-replica routing (C10).
- Connection multiplexing (PgBouncer).
- Automatic pool resize.

---

## 2. Technical Specification

### 2.1 Architecture

Each service exposes the same env-var-driven knobs with service-prefixed names. Sane defaults preserve current test behavior.

Today:

- [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) hardcodes `statement_timeout=10000` and `lock_timeout=3000` in `_DEFAULT_POOL_KWARGS` (~line 142–149).
- [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) `PostgresConnectionPool` has **no timeouts at all**.

After:

- Both read from env. Both add `idle_in_transaction_session_timeout`. Both set `application_name`.

### 2.2 Schema changes

None.

### 2.3 Endpoints

- `GET /metrics` (existing; extend) — exposes `db_pool_size`, `db_pool_in_use`, `db_pool_waiting`, `db_pool_acquire_seconds_p50`, `db_pool_acquire_seconds_p99` per service.

### 2.4 Code changes

**Env vars** (all per-service; mirrored as `BACKEND_DB_*` and `RUNTIME_DB_*`):

| Var (RUNTIME prefix shown)                | Default | Purpose                                     |
| ----------------------------------------- | ------- | ------------------------------------------- |
| `RUNTIME_DB_POOL_MIN_SIZE`                | 5       | warm connections                            |
| `RUNTIME_DB_POOL_MAX_SIZE`                | 50      | hard cap                                    |
| `RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS` | 5       | block-and-fail if pool saturated            |
| `RUNTIME_DB_STATEMENT_TIMEOUT_MS`         | 10000   | server-side per-statement cap               |
| `RUNTIME_DB_LOCK_TIMEOUT_MS`              | 3000    | server-side per-lock-wait cap               |
| `RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS`       | 30000   | server-side abort on long-idle transactions |

**Pool-init signatures:**

```python
# services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py
def _build_pool_kwargs(role: str) -> dict[str, Any]:
    return {
        "min_size": _env_int("RUNTIME_DB_POOL_MIN_SIZE", 5),
        "max_size": _env_int("RUNTIME_DB_POOL_MAX_SIZE", 50),
        "timeout": _env_int("RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS", 5),
        "kwargs": {
            "options": (
                f"-c statement_timeout={_env_int('RUNTIME_DB_STATEMENT_TIMEOUT_MS', 10000)} "
                f"-c lock_timeout={_env_int('RUNTIME_DB_LOCK_TIMEOUT_MS', 3000)} "
                f"-c idle_in_transaction_session_timeout={_env_int('RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS', 30000)} "
                f"-c application_name=ai-backend:{role}"
            ),
        },
    }
```

`role` is `"api"` for the FastAPI process and `"worker"` for the worker process. Backend service has only `"api"` for now.

**New module** `services/<svc>/src/.../persistence/pool_metrics.py`:

```python
class PoolMetricsCollector:
    """Prometheus collector wrapping psycopg(_pool) stats."""
    def collect(self) -> Iterable[Metric]:
        ...
```

Wired into the existing Prometheus surface.

**Acquire-latency tracking:** wrap `pool.connection()` in a context manager that records the wait time histogram before yielding.

### 2.5 Trust model & failure semantics

- Pool exhaustion → `RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS` then raise `psycopg.errors.OperationalError` → caller maps to a 503 with a safe error message.
- Statement timeout fires → `query_canceled` SQLSTATE → caller's existing exception handlers map to 5xx with `safe_error_code="db_statement_timeout"`.
- Lock timeout fires → `lock_not_available` → same path.
- Idle-in-transaction timeout fires → connection killed; caller's transaction context manager surfaces a typed error; retry via existing retry helpers (where available) or fail the request.

### 2.6 Tenant isolation

N/A directly. The `application_name` does not include tenant info.

### 2.7 Observability

Prometheus:

- `db_pool_size{service,role}`
- `db_pool_in_use{service,role}`
- `db_pool_waiting{service,role}`
- `db_pool_acquire_seconds{service,role,quantile=0.5|0.99}`

`pg_stat_activity` queryable by `application_name LIKE 'ai-backend:%'`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] `SELECT pg_sleep(15)` raises within ~10s under default config.
- [ ] Holding a `BEGIN` then sleeping > 30s causes server-side abort (idle-in-txn timeout).
- [ ] `application_name` in `pg_stat_activity` matches `<service>:<role>` for every active connection.
- [ ] Pool-saturation Prometheus metrics increment under load.
- [ ] Test suite passes with default values (no regression).

### 3.2 Test plan

**Unit:**

- `test_env_var_overrides_defaults` — set env vars; assert pool kwargs match.

**Integration:**

- `test_statement_timeout_fires` — `SELECT pg_sleep(N)` where N > timeout raises `query_canceled`.
- `test_lock_timeout_fires` — open competing transaction holding a lock; second statement times out.
- `test_idle_in_transaction_timeout_fires` — `BEGIN; SELECT pg_sleep(N)` where N > timeout → server kills.
- `test_application_name_set` — query `pg_stat_activity` from the pool itself; assert `application_name`.
- `test_pool_metrics_increment_under_load` — saturate pool; assert `db_pool_waiting > 0`.

### 3.3 Compliance evidence produced

- Production-grade DB tuning is now visible config, not magic constants.
- `docs/deployment/db-tuning.md` documents recommended values per profile.

### 3.4 Rollout plan

Defaults preserve current behavior. Roll out per service. Production deploy YAML/Helm sets explicit values.

### 3.5 Backout plan

Revert.

### 3.6 Definition of done

- [ ] Both services read env-driven pool config.
- [ ] Both services set `application_name`.
- [ ] Prometheus metrics exposed and dashboarded.
- [ ] All integration tests pass.
- [ ] `docs/deployment/db-tuning.md` written with recommended values per profile.

---

## 4. Critical files

- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — `_DEFAULT_POOL_KWARGS` becomes `_build_pool_kwargs`; pool init reads env.
- Modify: [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) — `PostgresConnectionPool.__init__` reads env, sets timeouts.
- New: `services/ai-backend/src/agent_runtime/persistence/pool_metrics.py`
- New: `services/backend/src/backend_app/db/pool_metrics.py`
- New: `docs/deployment/db-tuning.md`
