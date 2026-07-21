# Desktop file-store migration (Postgres → file)

Offline, one-way migration that copies an existing runtime store (Postgres, or
in-memory) into the desktop **file** store **before** the file backend is turned
on. This is the AC2 "Migration from the legacy desktop AI-runtime store" step
(`docs/plan/desktop/agent-capabilities/02-ac2-file-session-store.md` §702).

> **The file store is the desktop default (AC2b).** The desktop supervisor maps
> the default to `RUNTIME_STORE_BACKEND=file` +
> `RUNTIME_FILE_STORE_ROOT=<userData>/agent-data/v1`;
> `COPILOT_DESKTOP_FILE_STORE_V1=0` (or `false`/`off`) pins the legacy Postgres
> store as an escape hatch. On an existing install with Postgres history, the
> first file boot **carries that history across automatically** — the supervisor
> runs this migration (`--on-boot`) before the ai-backend starts (see "Automatic
> first-boot migration" below). Existing Postgres conversations are preserved on
> disk throughout; on any migration failure the boot falls back to serving the
> Postgres store, never an empty app. The manual flows below remain available for
> operators who want to stage or verify the carry-over out of band. This is the
> AC2 "Migration from the legacy desktop AI-runtime store" step.

## What it does

- Reads every conversation and its full record set — messages, runs, events
  (with exact `sequence_no`), main **and** subagent event streams, and any
  offloaded large-tool-result object blobs — from the **source** store _through
  the shared runtime store port only_. Because the in-memory and Postgres
  adapters implement the same port, `postgres → file` and `in_memory → file` are
  the same code path.
- Writes them into the destination file store through the file store's own
  write path (append + object-put + index-rebuild), **preserving every id,
  `sequence_no`, timestamp, payload, and object content-address exactly** —
  nothing is re-keyed.
- **Idempotent**: a conversation already present in the destination is skipped,
  so re-running is a safe no-op. Stable record ids keep re-runs duplicate-free.
- **Dry-run**: reports what would migrate and writes nothing.
- **Verify**: re-reads both stores through the port and asserts per-conversation
  record counts + content equality (including object bytes). Any mismatch is
  reported and the command exits non-zero, leaving the source authoritative.

Objects: the offload seam is file-store-only, so a Postgres/in-memory source
carries tool payloads inline and has no separate object blobs to copy. Objects
only travel when the source is itself a file store (backout / re-forward).

## Operator flow

Stop the facade, AI API, and worker first so the source store is quiescent. Run
from `services/ai-backend` with the service `.venv` and the shared-package
`PYTHONPATH`.

```bash
cd services/ai-backend
export PYTHONPATH="$PWD/../../packages/service-contracts/src:$PWD/../../packages/audit-chain/src"

# 1. Dry-run — reports what would migrate, writes nothing.
.venv/bin/python -m runtime_adapters.migrate \
  --source postgres --source-database-url "$AI_BACKEND_DATABASE_URL" \
  --dest-root "$HOME/Library/Application Support/<app>/agent-data/v1" \
  --org-id "$ORG_ID" --user-id "$USER_ID" --dry-run

# 2. Real migration (add --verify to run the equality pass inline).
.venv/bin/python -m runtime_adapters.migrate \
  --source postgres --source-database-url "$AI_BACKEND_DATABASE_URL" \
  --dest-root "$HOME/Library/Application Support/<app>/agent-data/v1" \
  --org-id "$ORG_ID" --user-id "$USER_ID" --verify

# 3. Verify a previously-migrated destination on its own.
.venv/bin/python -m runtime_adapters.migrate \
  --source postgres --source-database-url "$AI_BACKEND_DATABASE_URL" \
  --dest-root "$HOME/Library/Application Support/<app>/agent-data/v1" \
  --org-id "$ORG_ID" --user-id "$USER_ID" --verify-only
```

The file backend is already the desktop default, so after a **clean verify** the
migrated history is simply picked up on the next boot — no flag change is needed.
(If you had pinned Postgres with `COPILOT_DESKTOP_FILE_STORE_V1=0` to run the
migration against a live source, unset it — or set it truthy — to return to the
file default:)

```bash
# Return to the file-native default (unset also works — file is the default).
export COPILOT_DESKTOP_FILE_STORE_V1=1
```

`--org-id` / `--user-id` are paired positionally and repeatable to migrate
several tenants. On the single_user_desktop profile there is exactly one scope.
The scope flags are **optional for every source**: omit them and the migrator
auto-discovers scopes — a Postgres source via
`SELECT DISTINCT org_id, user_id FROM agent_conversations`
(`PostgresRuntimeApiStore.list_conversation_scopes`), an in-memory/file source
from its loaded conversations. Pass explicit scopes only to migrate a subset.

## Automatic first-boot migration (`--on-boot`)

For an unattended desktop first-file-boot, the migration runs itself. The
`--on-boot` mode forces a Postgres source, auto-discovers **every** tenant scope
(no `--org-id`/`--user-id`), migrates + verifies, and treats a fresh install with
no AI schema as a clean no-op:

```bash
.venv/bin/python -m runtime_adapters.migrate \
  --on-boot --source postgres --source-database-url "$AI_BACKEND_DATABASE_URL" \
  --dest-root "$HOME/Library/Application Support/<app>/agent-data/v1"
```

Its exit code is a **fail-safe contract** for the desktop supervisor:

| Exit | Meaning                                        | Supervisor action               |
| ---- | ---------------------------------------------- | ------------------------------- |
| `0`  | Migrated, or nothing to migrate (empty source) | Serve the **file** store        |
| `2`  | Verify mismatch — import is not trustworthy    | Fall back to the Postgres store |
| `1`  | Any other failure (unreachable source, disk)   | Fall back to the Postgres store |

The supervisor only invokes `--on-boot` when it is safe to do so — the app is
booting on the file store, the file store root is empty/new, and no prior boot
has already migrated (a marker file inside the store root records completion).
That gate is a pure decision (`apps/desktop/main/services/migration-policy.ts`
`resolveMigrationDecision`); the impure detection (fs-empty probe in
`file-store-facts.ts`, Postgres row-count in `pg-facts.ts`, marker read) and the
boot brain (`boot-store-backend.ts` `resolveBootStoreBackend`) that ties them to
the `--on-boot` runner (`migration-runner.ts`) live in the desktop supervisor.
**Status:** shipped end to end. The supervisor resolves the effective store
backend during the migrations phase (Postgres is up, ai-backend has not started):
it probes the facts, runs `--on-boot` when the gate allows, writes the completion
marker only on a clean import, and forces the Postgres store for the boot on any
failure (via the `storeBackendOverride` seam in `service-env.ts`). Running
`--on-boot` (or the scoped flow above) by hand remains available but is no longer
required for an ordinary first-file boot.

## Guarantees & limits

- **No live dual-write.** Migration is offline and one-way per attempt; keep the
  source read-only until a clean verify.
- **Re-runnable.** Interrupt and re-run with the same arguments — already-migrated
  conversations are skipped and a partially-written conversation from an aborted
  run is re-migrated cleanly.
- **Source cleanup is out of scope for this tool.** Deleting the legacy Postgres
  database is a separate, receipt-gated step (PRD §722) and is not performed here.
