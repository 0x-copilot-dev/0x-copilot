# Refactor PRD — Cleanup Wave (Phase 2)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §1.7](../architecture/refactor-audit.md#17-custom-migration-runner), [§1.8](../architecture/refactor-audit.md#18-encryptexistingcolumns-running-as-a-perpetual-job), [§5.6](../architecture/refactor-audit.md#56-6-empty-legacy-directories-under-agent_runtime), [§5.7](../architecture/refactor-audit.md#57-dev_auth_bypass_allowed-toggle-on-deploymentprofile)
**Roadmap:** [00-roadmap.md](00-roadmap.md) → P6

---

## 1. Problem

Four hygiene items the audit flagged as low-risk single-PR work. Each is too small to ship in its own PR; bundled they clear a measurable amount of visible cruft and reduce long-term maintenance load.

### 1.1 Six legacy empty directories under `agent_runtime/`

The architecture index Appendix A documents six top-level directories that contain only `__pycache__` (no `.py` files). They are remnants from before `agent_runtime/` was reorganized into `capabilities/`, `delegation/`, `context/memory/`, `execution/`. Verified empty by:

```bash
find services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools} -name "*.py"
```

returning no hits. Live equivalents:

| Empty directory            | Live equivalent                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `agent_runtime/agent/`     | `agent_runtime/execution/` (graph + builder) and `agent_runtime/capabilities/` (middleware) |
| `agent_runtime/mcp/`       | `agent_runtime/capabilities/mcp/`                                                           |
| `agent_runtime/memory/`    | `agent_runtime/context/memory/`                                                             |
| `agent_runtime/skills/`    | `agent_runtime/capabilities/skills/`                                                        |
| `agent_runtime/subagents/` | `agent_runtime/delegation/subagents/`                                                       |
| `agent_runtime/tools/`     | `agent_runtime/capabilities/tools/`                                                         |

**Why it's a problem:** search hits land in legacy paths first; new contributors get confused. Empty `__init__.py`-less directories are silently importable as namespace packages, which can mask broken imports in test runs.

### 1.2 `EncryptExistingColumns` running as a perpetual worker job

[`runtime_worker/jobs/encrypt_existing_columns.py`](../../src/runtime_worker/jobs/encrypt_existing_columns.py) is registered as a worker background job. Naming and intent both indicate this is a one-shot data migration that calcified into a forever-running daemon: it scans for unencrypted rows on every wake, encrypts any it finds, then sleeps.

**Why it's a problem:**

- Daemons that idempotently scan empty work add database load and complicate worker shutdown.
- The encryption logic is mixed with scheduler / loop logic. The encryption should be a stable transform; the scheduling should be one-shot.
- A successful one-shot migration leaves a clean state (every row encrypted) that the daemon by definition cannot reach — it has no terminal condition.

### 1.3 Bespoke schema migration runner

[`agent_runtime/persistence/schema/migrate.py`](../../src/agent_runtime/persistence/schema/migrate.py) is a custom migration script. The migration story has unbounded growth from here:

- No autogenerate from SQLAlchemy models.
- No standard downgrade path.
- No standard data-migration story (custom migrations live in the script).
- Other Python services in the monorepo will likely converge on Alembic; one-off here is friction.

### 1.4 Stale `dev_auth_bypass_allowed` toggle on `DeploymentProfile`

[`agent_runtime/deployment/profile.py`](../../src/agent_runtime/deployment/profile.py) lists `dev_auth_bypass_allowed` as a feature toggle. The root [`CLAUDE.md`](../../../../CLAUDE.md) explicitly says "DEV_AUTH_BYPASS no longer exists. Dev sessions go through a real signed bearer minted by `POST /v1/dev/identity/mint`."

**Why it's a problem:** stale toggles confuse readers about what code paths still exist. If the toggle is checked anywhere, that's dead code that nominally implies a bypass exists.

### What this is NOT

- Not a behavior change in production. Encryption stays. Migration history stays. Auth model stays. Empty directories obviously add nothing.
- Not a switch of database, ORM, or test framework.
- Not a change to the encryption algorithm or key management.

---

## 2. Goal and non-goals

### Goal

Remove four pieces of cruft in one PR with no production behavior change. Each sub-item ships an additive change first (where applicable) and a removal step last so revert is a single git revert.

### Non-goals

- Reduce the schema migration surface (table count, column types). Migration runner change only.
- Move from `pgcrypto` / current encryption strategy to anything else. Encryption transform is preserved verbatim.
- Reorganize `agent_runtime/` further. The 6 empty dirs go; the rest of the layout is left alone.

### Success criteria

- All 6 empty `agent_runtime/{agent,mcp,memory,skills,subagents,tools}/` directories removed in git history. `find services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools} -type d` returns no hits.
- `runtime_worker/jobs/encrypt_existing_columns.py` removed from the worker's job registration. The encryption transform exists as an Alembic data migration that ran once on every environment.
- Alembic adopted: `alembic.ini` + `alembic/env.py` + `alembic/versions/` exist, `alembic upgrade head` from a clean DB produces the same schema as `agent_runtime/persistence/schema/migrate.py` did. Old `migrate.py` is removed (or kept as a no-op shim for one release if anything in CI calls it directly).
- `dev_auth_bypass_allowed` field removed from `DeploymentProfile` and from any settings shapes. No runtime check on the field anywhere in the codebase. (Verify before deletion: see [§3.4](#34-stale-toggle-removal-1-line-grep-then-delete).)
- Full test suite green (`make test`, plus per-service `pytest`).
- No new public API surface; no contract changes; no migration that requires downtime.

---

## 3. Systems touched

### 3.1 Empty-directory deletion (mechanical)

```bash
git rm -r services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools}
```

Verification before commit:

```bash
# Should return nothing
find services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools} -name "*.py" 2>/dev/null

# Should return nothing — confirms no live import resolves to these paths
grep -rn "from agent_runtime\.\(agent\|mcp\|memory\|skills\|subagents\|tools\)" services/ai-backend/src services/ai-backend/tests
grep -rn "import agent_runtime\.\(agent\|mcp\|memory\|skills\|subagents\|tools\)" services/ai-backend/src services/ai-backend/tests
```

If either grep returns a match, _do not delete_ — investigate first.

### 3.2 `EncryptExistingColumns` → Alembic data migration

**Files removed:**

| File                                                                                                           | Why                                 |
| -------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| [`runtime_worker/jobs/encrypt_existing_columns.py`](../../src/runtime_worker/jobs/encrypt_existing_columns.py) | Loop replaced by one-shot migration |

**Files modified:**

- Worker entrypoint ([`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py) or wherever the job loop is registered): drop the `EncryptExistingColumns` registration.
- Worker dependencies module: drop the import.

**Files added:**

- `services/ai-backend/alembic/versions/<rev>_encrypt_existing_columns.py` — Alembic data migration that performs the same row scan + encryption transform as the daemon, in one batch. Idempotent: SELECT WHERE column is unencrypted, batch UPDATE. Use server-side cursor for large tables.

**Migration shape (sketch):**

```python
def upgrade() -> None:
    bind = op.get_bind()
    # Use the same FieldCodec the runtime uses; import from
    # services/ai-backend/src/runtime_adapters/postgres/codec.py (or wherever it lives)
    codec = FieldCodec.from_env()
    batch_size = 1000
    while True:
        rows = bind.execute(text(
            "SELECT id, sensitive_col FROM <table> "
            "WHERE encryption_marker IS NULL "
            "ORDER BY id LIMIT :n FOR UPDATE SKIP LOCKED"
        ), {"n": batch_size}).fetchall()
        if not rows:
            break
        for row in rows:
            encrypted = codec.encrypt(row.sensitive_col)
            bind.execute(text(
                "UPDATE <table> SET sensitive_col = :v, encryption_marker = 'v1' "
                "WHERE id = :id"
            ), {"v": encrypted, "id": row.id})

def downgrade() -> None:
    raise NotImplementedError("Encryption is not reversible without the key")
```

**Operational note:** The migration must run before this PR ships to any environment that still has unencrypted rows. Confirm by counting `WHERE encryption_marker IS NULL` on staging + prod beforehand. If both are zero, the migration is a no-op and the daemon was already idle — safe to delete with no migration.

### 3.3 Alembic adoption

**Files removed:**

| File                                                                                                   | Why                 |
| ------------------------------------------------------------------------------------------------------ | ------------------- |
| [`agent_runtime/persistence/schema/migrate.py`](../../src/agent_runtime/persistence/schema/migrate.py) | Replaced by Alembic |

(Or kept as a shim that prints "deprecated, use `alembic upgrade head`" for one release.)

**Files added:**

- `services/ai-backend/alembic.ini` — Alembic config, `script_location = alembic`, `sqlalchemy.url = ${DATABASE_URL}`.
- `services/ai-backend/alembic/env.py` — standard Alembic env, configured to use the same SQLAlchemy MetaData object the schema currently uses.
- `services/ai-backend/alembic/versions/0001_baseline.py` — baseline migration capturing the current schema as a single CREATE TABLE per table. Use `alembic stamp head` on existing environments to mark them as already-baselined (no-op).

**Files modified:**

- [`services/ai-backend/Makefile`](../../Makefile): `make migrate` becomes `alembic upgrade head` (was: invoke `migrate.py`).
- [`services/ai-backend/pyproject.toml`](../../pyproject.toml) / `requirements.txt`: add `alembic`.
- Anything in CI / Docker entrypoint that called the old runner: switch to `alembic upgrade head`.

**Baseline cutover plan:**

1. Generate Alembic baseline `0001_baseline.py` from current schema (autogenerate against an empty DB).
2. On every existing environment (dev, staging, prod): `alembic stamp head` to mark as baselined without running.
3. New environments: `alembic upgrade head` from empty.
4. Remove `migrate.py` after one release where both invocations existed (Make targets warn).

### 3.4 Stale toggle removal (1-line grep, then delete)

```bash
grep -rn "dev_auth_bypass_allowed" services/ai-backend/src services/ai-backend/tests
```

Three possible outcomes:

1. **Field defined but never read:** safe delete. Remove from `DeploymentProfile` field list, remove from `DeploymentFeatureToggles` if separate, remove from `toggles_hash()` input set.
2. **Field read in dead code (e.g. a guard that's also unreachable):** delete the field and the guard.
3. **Field still gating live behavior:** stop. Open a separate issue, do not include in this PR.

Per the root [`CLAUDE.md`](../../../../CLAUDE.md) ("DEV_AUTH_BYPASS no longer exists"), outcome 1 or 2 is expected.

---

## 4. Behaviors to preserve

| Behavior                                                                                                 | How preserved                                                                       |
| -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Encryption transform on existing unencrypted rows                                                        | Alembic data migration runs once; key + algorithm unchanged                         |
| Schema parity between `migrate.py` and Alembic baseline                                                  | Diff `pg_dump --schema-only` before/after; baseline migration matches               |
| Deployment profile resolution at startup ([`profile.py`](../../src/agent_runtime/deployment/profile.py)) | Untouched apart from one removed field                                              |
| `toggles_hash()` stability                                                                               | Field removal changes the hash; this is the intended behavior — bump a version note |
| All current Alembic conventions in other services in the monorepo (if any)                               | Match their `alembic.ini` style, env.py shape, naming                               |

---

## 5. Risks

| Risk                                                                                   | Likelihood | Mitigation                                                                                  |
| -------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------- |
| An empty-dir delete reveals a hidden namespace-package import we missed in grep        | Low        | Pre-delete grep covers `from`, `import`, and re-export forms; CI catches anything missed    |
| Alembic baseline diverges from current schema by one column                            | Medium     | `pg_dump --schema-only` diff in the PR description; baseline is generated, not hand-written |
| `dev_auth_bypass_allowed` is referenced in a settings-loading code path we didn't grep | Low        | Use `ripgrep -uu` to include hidden / ignored files; check `.env.example` and Helm charts   |
| Encryption migration is run before being smoke-tested on staging with realistic data   | Medium     | Mandatory staging dry-run with row counts logged before merge                               |
| `alembic stamp head` is forgotten on one environment → next migration fails            | Medium     | PR description includes a per-environment runbook; ops checks off each environment          |

---

## 6. Unit testing requirements

### 6.1 Empty-directory removal

- No new tests; CI test discovery proves nothing imported them.

### 6.2 Encryption migration

- New test: `tests/unit/migrations/test_encrypt_existing_columns.py` — uses `alembic-utils` or a temporary in-memory SQLite to:
  1. Insert N rows with unencrypted values matching the prior daemon's input shape.
  2. Run the migration.
  3. Assert all rows are encrypted (round-trip via `FieldCodec.decrypt` returns the original values).
  4. Assert the migration is idempotent — running it twice does not re-encrypt or corrupt.
  5. Assert empty-table case is a no-op (no errors, no writes).

### 6.3 Alembic adoption

- New test: `tests/unit/migrations/test_baseline.py` — programmatically run `alembic upgrade head` against a temporary Postgres (testcontainer or transactional fixture), then `pg_dump --schema-only` and assert it matches a checked-in golden snapshot.
- Update CI to run `alembic upgrade head` against an empty DB on every PR (catches malformed migrations).

### 6.4 Stale toggle

- Negative test: `grep -rn "dev_auth_bypass_allowed" services/ai-backend/src services/ai-backend/tests` returns zero hits, asserted via a CI step.

---

## 7. Rollback plan

| Sub-item             | Rollback                                                                                                                                                                                           |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Empty dir deletion   | `git revert` the deletion commit. Directories reappear empty.                                                                                                                                      |
| Encryption migration | If migration fails mid-run: it's idempotent (`SELECT WHERE encryption_marker IS NULL`), retry. If logic is broken: revert the Alembic migration file _and_ re-add the daemon (separate revert PR). |
| Alembic adoption     | Revert PR. Old `migrate.py` returns. `alembic stamp head` markers on environments are harmless leftovers (the `alembic_version` table can be dropped manually).                                    |
| Stale toggle         | `git revert`. Toggle field returns to the profile.                                                                                                                                                 |

---

## 8. Implementation order within the PR

Land in this order so each sub-item independently passes CI before the next builds on it:

1. **Stale toggle removal** (smallest, unrelated to anything else; lowest risk).
2. **Empty directory deletion** (mechanical; CI proves nothing imports them).
3. **Alembic adoption** (additive: add Alembic config + baseline, dual-track with old `migrate.py` for one CI cycle, then remove old runner).
4. **Encryption migration** (depends on Alembic being in place).

Each step has its own commit so revert is granular.

---

## 9. Open questions

- Does any tooling outside `services/ai-backend/` invoke `migrate.py` directly (Helm pre-hook, init container, CI job)? If yes, change those callers in the same PR.
- Are there other Python services in the monorepo with their own bespoke migration runners? If yes, this PR sets the convention; flag them for follow-up.
- Confirm `EncryptExistingColumns` row count on staging and prod before scheduling. If both are zero, skip the migration entirely.
- Verify `FieldCodec` is importable from a migration context (no circular import with the runtime's startup wiring).

---

_This PRD is in draft until the four pre-flight verifications above complete. After verification, mark as Ready and assign for implementation._
