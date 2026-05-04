# ADR 0002: Versioned Migration Tooling (yoyo-migrations)

## Status

Accepted as of PR C2.

## Context

Until C2, schema for both backend services lived as embedded Python string
constants:

- `services/backend/src/backend_app/migrations.py` →
  `POSTGRES_BACKEND_MIGRATION_SQL` (concatenation of MCP / skills /
  audit-hardening blocks).
- `services/ai-backend/src/agent_runtime/persistence/schema/postgres.py` →
  `POSTGRES_AGENT_RUNTIME_MIGRATION_SQL` plus a small in-line `ALTER TABLE`
  added inside the adapter's `migrate()` method.

This was unacceptable for the bank/government deployment targets in the
roadmap because:

- There is no version table — production has no record of _which_ schema
  state it is in.
- There is no way to roll back a bad change.
- Reviewing a migration means reviewing a Python string diff with no
  enforcement that an applied migration cannot later be edited in place.
- Each later PR (A1..A8, B1..B8, C5..C8) adds a migration; without tooling
  every one would need to grow the embedded string further.

## Decision

Adopt **yoyo-migrations** as the schema migration tool for both backend
services.

- Each service owns its own `migrations/` directory.
- Each migration is a pair of files: `NNNN_<topic>.sql` and
  `NNNN_<topic>.rollback.sql`.
- Each service has a `MANIFEST.lock` (sha256 per migration id) checked in
  and validated by `tools/check_migration_manifest.py` in CI.
- Migrations are applied via the per-service runner
  (`backend_app.db.migrate.MigrationRunner` /
  `agent_runtime.persistence.schema.migrate.MigrationRunner`) or the
  `scripts/migrate.py` operator CLI.
- Production runs migrations as a separate deploy step
  (`*_MIGRATIONS_AUTO_APPLY=false`); dev / docker-compose / tests keep the
  current zero-config behavior (auto-apply on app boot).

The legacy SQL constants
(`POSTGRES_BACKEND_MIGRATION_SQL`,
`POSTGRES_AGENT_RUNTIME_MIGRATION_SQL`,
plus their service-internal sub-constants) are kept as thin shims that read
the canonical `.sql` files at module import. This preserves any existing
caller (notably `tests/unit/agent_runtime/persistence/test_postgres_schema.py`)
and removes the risk of drift between the inline string and the on-disk
migration file.

## Considered alternatives

**Alembic.** The canonical Python migration tool. Rejected because both
services author migrations as raw SQL — neither uses SQLAlchemy ORM, and
neither plans to. Adding alembic would force pulling in SQLAlchemy _or_
carrying alembic's custom DDL helpers without using the rest of its
abstractions. yoyo gives us the same versioning + reversibility on plain
`.sql` files.

**Django-style migrations.** Out of scope — no Django.

**Hand-rolled runner.** Considered briefly. yoyo already implements the
version-table + advisory-lock + apply/rollback semantics correctly; we'd
re-invent it.

**A single tool shared between services.** Rejected to honor the hard
service-boundary rule. Each service owns its own runner module and
`migrations/` dir; only the SQL semantics are conceptually shared.

## Consequences

**Benefits**

- Versioned, reversible, audit-loggable schema history per service.
- CI catches missing manifest entries and post-apply edits to migrations.
- Production deploys can run migrations as a discrete Job (e.g. Helm
  pre-install hook) instead of on app boot.
- Subsequent PRs (A1..A10, B1..B8, C5..C8) drop their migrations into the
  per-service dir without growing a Python string.

**Costs**

- New runtime dep (`yoyo-migrations==9.0.0`) on both services. Small
  surface; no transitive dep into the request path.
- Operators have one more thing to know about: the `scripts/migrate.py`
  CLI.

**Migration plan**

1. Land the runner + `migrations/` content (this PR).
2. Production CI/CD pipelines start invoking `scripts/migrate.py apply` as
   a discrete deploy step; flip `*_MIGRATIONS_AUTO_APPLY=false` per
   environment.
3. After one release cycle, a follow-up PR removes the legacy `.sql`
   string shims if no remaining caller imports them.

## Verification

- `tools/check_migration_manifest.py` passes for both services.
- `services/backend/tests/test_migration_runner.py` and
  `services/ai-backend/tests/unit/agent_runtime/persistence/test_migration_runner.py`
  exercise apply / rollback / idempotency against sqlite fixtures.
- All pre-existing service test suites remain green
  (`make test` plus per-service pytest).
