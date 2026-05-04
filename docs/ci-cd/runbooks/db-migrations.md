# DB migrations runbook

Operational guide for applying / rolling back schema changes in
`services/backend` and `services/ai-backend`. The tool is **yoyo-migrations**;
the decision is documented in
[ADR 0002](../../decisions/0002-migration-tooling.md).

## File layout

```
services/<svc>/migrations/
  0001_<topic>.sql            # apply (up)
  0001_<topic>.rollback.sql   # rollback (down)
  0002_<topic>.sql
  0002_<topic>.rollback.sql
  MANIFEST.lock               # sha256 per migration id
```

Per-service runner: `backend_app.db.migrate.MigrationRunner` /
`agent_runtime.persistence.schema.migrate.MigrationRunner`.
Per-service CLI: `services/<svc>/scripts/migrate.py`.

## Common operations

### Apply pending migrations

```bash
cd services/backend
BACKEND_DATABASE_URL=postgresql://... \
  .venv/bin/python scripts/migrate.py apply
```

```bash
cd services/ai-backend
RUNTIME_DATABASE_URL=postgresql://... \
  .venv/bin/python scripts/migrate.py apply
```

### Show status

```bash
.venv/bin/python scripts/migrate.py status
# applied: ['0001_initial_mcp_skills', '0002_audit_hardening']
# pending: []
```

### Roll back one or more migrations

```bash
# Roll back to 0001 (drop everything strictly newer)
.venv/bin/python scripts/migrate.py rollback --to 0001_initial_mcp_skills

# Roll back everything
.venv/bin/python scripts/migrate.py rollback
```

### Override the database URL (e.g. against a staging DSN)

```bash
.venv/bin/python scripts/migrate.py --db-url postgresql://staging/... apply
```

## Authoring a new migration

1. Pick the next sequential id: `NNNN_<topic>` (zero-padded, snake_case).
2. Create both files in the service's `migrations/` directory:
   - `NNNN_<topic>.sql` — DDL applied on `apply`.
   - `NNNN_<topic>.rollback.sql` — DDL applied on `rollback`.
3. Wrap each file in a single transaction-friendly batch (yoyo wraps
   automatically). Use `IF NOT EXISTS` / `IF EXISTS` so a partial apply on
   a manually-modified DB does not crash.
4. Regenerate the manifest:
   ```bash
   python tools/check_migration_manifest.py --write
   ```
5. Verify CI passes:
   ```bash
   python tools/check_migration_manifest.py
   ```

## Zero-downtime rules

- **Additive only.** A single PR may add columns / tables / indexes but
  must not drop or rename anything still referenced by the running code.
- **Two-phase for renames / removals.** PR 1: add the new column; ship
  code that writes both old and new. PR 2 (after one full release): stop
  reading the old column; drop it.
- **Deploy code first, then migration.** Migrations are run as a discrete
  step in CI/CD. The new code must be backward compatible with the
  _previous_ schema as well.
- **Index creation on large tables: use `CREATE INDEX CONCURRENTLY`** in
  a stand-alone migration (yoyo's per-migration transaction interferes
  with `CONCURRENTLY`; mark such migrations with the
  `__transactional__ = False` directive on the `.sql` file's first line:
  `-- __transactional__ = False`).

## Production deploy step

```yaml
# Example Helm pre-install / pre-upgrade hook
apiVersion: batch/v1
kind: Job
metadata:
  name: backend-migrate
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
spec:
  template:
    spec:
      containers:
        - name: migrate
          image: ghcr.io/.../backend:{{ .Values.image.tag }}
          command: ["python", "scripts/migrate.py", "apply"]
          env:
            - name: BACKEND_DATABASE_URL
              valueFrom:
                secretKeyRef: { name: backend-secrets, key: database_url }
            - name: BACKEND_MIGRATIONS_AUTO_APPLY
              value: "false"
      restartPolicy: Never
```

App container env in production: `BACKEND_MIGRATIONS_AUTO_APPLY=false` /
`RUNTIME_MIGRATIONS_AUTO_APPLY=false`.

## Restore drill (covered in C12)

PR C12 introduces a restore-drill CI job that verifies a known dump can be
restored, the runner re-applies cleanly, and a smoke test passes against
the restored DB.

## Common failures

| Symptom                                                         | Fix                                                                                                  |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `MANIFEST.lock matches 0 migration(s)`                          | Migrations dir is empty.                                                                             |
| `FAIL: ... drifts from migrations dir`                          | Run `python tools/check_migration_manifest.py --write` and commit.                                   |
| `Malformed manifest line`                                       | Hand-edited the manifest. Regenerate.                                                                |
| `psycopg.OperationalError: another instance is already running` | Two operators are racing the migration. yoyo holds an advisory lock; wait or kill the other process. |
| `relation "_yoyo_migration" does not exist`                     | Should never happen on yoyo 9. If it does, ensure the runner has `CREATE TABLE` privilege.           |

## Yoyo-specific notes

- `_yoyo_migration` table is created automatically on first `apply`.
- Each migration runs inside an implicit transaction; for DDL that cannot
  run in a transaction (`CREATE INDEX CONCURRENTLY`), set the directive
  documented above.
- The advisory lock prevents two concurrent runners from applying the
  same migration twice.
