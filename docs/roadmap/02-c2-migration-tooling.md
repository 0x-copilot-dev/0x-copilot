# PR 02 — C2: Adopt yoyo-migrations for backend and ai-backend

**Spec ID:** C2 | **Track:** Deployment & DB | **Wave:** 0 (Foundation) | **Estimated effort:** L
**Depends on:** none (parallel to C1)
**Required for:** A1, A2, A3, A4, A5, A6, A7, A8, B1, B2, B3, B4, B7, B8, C5, C6, C7, C8, C9 (every PR adding a migration)

---

## 1. Functional Specification

### 1.1 Goal

Replace the ad-hoc SQL strings in [services/backend/src/backend_app/migrations.py](../../services/backend/src/backend_app/migrations.py) and [services/ai-backend/src/agent_runtime/persistence/schema/postgres.py](../../services/ai-backend/src/agent_runtime/persistence/schema/postgres.py) with a real, versioned, reversible migration tool. Without this, every later PR keeps growing untracked SQL strings that are not reviewable, not rollbackable, and not auditable — unacceptable for bank/gov deploys.

### 1.2 User-visible behavior

- **Developers** add migrations as `.sql` files in a numbered `migrations/` dir per service. CI fails if a migration file is added without an entry in `MANIFEST.lock`.
- **Operators** run migrations as a separate deploy step (e.g. a Kubernetes Job) instead of on app startup. `BACKEND_MIGRATIONS_AUTO_APPLY=false` in production; `true` in dev.
- **Auditors** can read a deterministic ordered list of every schema change ever applied to production, with checksums.

### 1.3 Out of scope

- Schema changes themselves (this PR ports the existing schema verbatim).
- Cross-service schema (each service still owns its own schema; `_yoyo_migration` table is service-local).
- Online schema-change tooling (gh-ost / pt-online-schema-change) — postponed.

---

## 2. Technical Specification

### 2.1 Architecture

**Why yoyo-migrations over alembic:** ai-backend uses raw SQL, not SQLAlchemy ORM. yoyo accepts plain `.sql` files with paired `.rollback.sql`, has a thin runner, and works directly over psycopg. Alembic would force pulling in SQLAlchemy or carrying its DDL helpers. Decision is documented in a new ADR.

Each service gets:

```
services/<svc>/
  migrations/
    0001_initial.sql
    0001_initial.rollback.sql
    0002_<topic>.sql
    0002_<topic>.rollback.sql
    MANIFEST.lock           # checksums
```

### 2.2 Schema changes

- **`_yoyo_migration` table** auto-created in each service's schema by yoyo. No app schema change.
- The existing `POSTGRES_BACKEND_MIGRATION_SQL` becomes `services/backend/migrations/0001_initial_mcp_skills.sql` (verbatim).
- The existing `POSTGRES_AGENT_RUNTIME_MIGRATION_SQL` becomes `services/ai-backend/migrations/0001_initial_runtime_persistence.sql` (verbatim).
- The existing `ALTER TABLE runtime_events ADD COLUMN IF NOT EXISTS activity_kind/presentation_json` patch becomes `0002_runtime_events_presentation.sql`.

### 2.3 Endpoints

None.

### 2.4 Code changes

**New per-service runner** — `services/backend/src/backend_app/db/migrate.py`:

```python
def run_migrations(database_url: str, migrations_dir: Path) -> list[str]:
    """Apply pending migrations. Returns the list of migration ids applied."""
    backend = yoyo.get_backend(database_url)
    migrations = yoyo.read_migrations(str(migrations_dir))
    with backend.lock():
        to_apply = backend.to_apply(migrations)
        backend.apply_migrations(to_apply)
        return [m.id for m in to_apply]
```

Same shape in `services/ai-backend/src/agent_runtime/persistence/schema/migrate.py`.

**New CLI entry point** — `services/<svc>/scripts/migrate.py` (callable in CI/CD pipelines):

```bash
python -m backend_app.db.migrate apply
python -m backend_app.db.migrate rollback --to 0003
python -m backend_app.db.migrate status
```

**Existing `migrations.py` / `postgres.py` schema strings**: kept as deprecated thin shims that import from the new runner for one release (so tests using `migrate()` continue to work). Removed in a follow-up PR after all callers migrated.

**MANIFEST.lock format** — one line per migration, sha256 of the up+rollback content:

```
0001_initial_mcp_skills.sql sha256=abcd1234...
0002_skills_metadata.sql sha256=efgh5678...
```

A CI script (`tools/check_migration_manifest.py`) compares the dir to the manifest; fails build on mismatch (catches accidental edits to applied migrations and missing manifest entries).

**Adapter wiring updates:**

- [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — `migrate()` method delegates to the new runner instead of executing the embedded SQL string.
- [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) — same pattern.

**Auto-apply gating:**

- `BACKEND_MIGRATIONS_AUTO_APPLY` / `RUNTIME_MIGRATIONS_AUTO_APPLY` env vars (default `true` in dev, `false` in prod). Profile loader sets the production default after C1 lands.
- When `false`, app boot logs "Migrations not applied automatically; run `python -m … migrate apply` as a deploy step."

### 2.5 Trust model & failure semantics

- Migration runner uses a separate DB role `enterprise_admin` with `BYPASSRLS` (introduced fully in C5; in this PR, the role exists with broad GRANTs). App role `enterprise_app` (created in C5) has no DDL grants.
- Failed migration: yoyo wraps each migration in a transaction by default. On failure, the migration is not recorded as applied. Process exits non-zero.
- Concurrent migration: `backend.lock()` uses Postgres advisory lock — two concurrent runners block instead of stepping on each other.

### 2.6 Tenant isolation

N/A.

### 2.7 Observability

- Each migration apply emits a structured log: `migration_applied id=0042 duration_ms=123 service=backend`.
- A `runtime_migration_audit` table is **not** added — yoyo's `_yoyo_migration` is the source of truth and includes timestamps.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Both services boot with empty DB → `python -m … migrate apply` produces a schema identical to today's `pg_dump --schema-only`.
- [ ] Adding a new migration file without updating `MANIFEST.lock` fails CI.
- [ ] Modifying an applied migration's SQL fails CI (checksum mismatch).
- [ ] `python -m … migrate rollback --to <id>` reverses migrations cleanly.
- [ ] Production env (`*_AUTO_APPLY=false`) boots without applying migrations and logs the deploy-step instruction.

### 3.2 Test plan

**Unit:**

- `test_apply_then_rollback_roundtrip` — apply 0001+0002, rollback to 0000, schema is empty.
- `test_manifest_checksum_mismatch_detected` — modify a migration file in fixture; `check_migration_manifest.py` exits non-zero.
- `test_concurrent_apply_serializes` — two concurrent runners; second waits on advisory lock.

**Integration:**

- Boot full ai-backend test suite against schema produced by yoyo runner — must pass identically to current schema.
- Same for backend test suite.
- `pg_dump --schema-only` of yoyo-applied DB equals `pg_dump --schema-only` of current `migrate()`-applied DB (snapshot test).

**CI:**

- New job runs `tools/check_migration_manifest.py` for every service.

### 3.3 Compliance evidence produced

- Versioned, checksummed, reversible migration history → satisfies CLAUDE.md §Compliance "audit logging completeness" prerequisite for schema changes.
- Runbook at `docs/ci-cd/runbooks/db-migrations.md` documenting the zero-downtime rules: additive-only, deploy code first then migration, drop columns in a separate later release.

### 3.4 Rollout plan

1. PR lands with both old and new code paths working.
2. CI/CD pipelines updated to run `python -m … migrate apply` as a separate Job before app rollout.
3. App env flips to `*_AUTO_APPLY=false`.
4. Follow-up small PR removes the deprecated string-based shim.

### 3.5 Backout plan

Set `*_AUTO_APPLY=true`. App falls back to legacy migrate(). Revert the PR if needed; the `_yoyo_migration` table is harmless if abandoned.

### 3.6 Definition of done

- [ ] `migrations/` directory exists per service with verbatim port of current schema.
- [ ] `MANIFEST.lock` checked in.
- [ ] CI job for manifest check is green.
- [ ] Roundtrip tests pass.
- [ ] Production deploy pipeline updated to run migrate as a Job.
- [ ] ADR `docs/decisions/0003-migration-tooling.md` written.
- [ ] Runbook `docs/ci-cd/runbooks/db-migrations.md` written.

---

## 4. Critical files

- Modify: [services/backend/src/backend_app/migrations.py](../../services/backend/src/backend_app/migrations.py) — becomes shim.
- Modify: [services/ai-backend/src/agent_runtime/persistence/schema/postgres.py](../../services/ai-backend/src/agent_runtime/persistence/schema/postgres.py) — becomes shim.
- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — `migrate()` delegates.
- Modify: [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py) — `migrate()` delegates.
- New: `services/backend/migrations/0001_initial_mcp_skills.sql` (+ `.rollback.sql`)
- New: `services/ai-backend/migrations/0001_initial_runtime_persistence.sql` (+ `.rollback.sql`)
- New: `services/ai-backend/migrations/0002_runtime_events_presentation.sql` (+ `.rollback.sql`)
- New: `services/backend/migrations/MANIFEST.lock`, `services/ai-backend/migrations/MANIFEST.lock`
- New: `services/<svc>/src/.../db/migrate.py` runner per service
- New: `services/<svc>/scripts/migrate.py` CLI per service
- New: `tools/check_migration_manifest.py`
- New: `docs/decisions/0003-migration-tooling.md`
- New: `docs/ci-cd/runbooks/db-migrations.md`
- Modify: `services/backend/requirements.txt`, `services/ai-backend/requirements.txt` — add `yoyo-migrations`.
