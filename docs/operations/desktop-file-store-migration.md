# Desktop file-store migration (Postgres → file)

Offline, one-way migration that copies an existing runtime store (Postgres, or
in-memory) into the desktop **file** store **before** the file backend is turned
on. This is the AC2 "Migration from the legacy desktop AI-runtime store" step
(`docs/plan/desktop/agent-capabilities/02-ac2-file-session-store.md` §702).

> **This tool changes no default.** The file store stays opt-in behind
> `COPILOT_DESKTOP_FILE_STORE_V1` (which the desktop supervisor maps to
> `RUNTIME_STORE_BACKEND=file` + `RUNTIME_FILE_STORE_ROOT=<userData>/agent-data/v1`).
> Run the migration first so the file store is non-empty when you later flip the
> flag. Flipping the flag without migrating still starts a **fresh, empty** file
> store — the migration is what carries history across.

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

Only a **clean verify** authorises turning the backend on. After a clean verify:

```bash
# Enable the opt-in file backend (desktop supervisor reads this at boot).
export COPILOT_DESKTOP_FILE_STORE_V1=1
```

`--org-id` / `--user-id` are paired positionally and repeatable to migrate
several tenants. On the single_user_desktop profile there is exactly one scope.
(The scope flags are required for a Postgres source; an in-memory/file source can
auto-discover scopes from its loaded conversations.)

## Guarantees & limits

- **No live dual-write.** Migration is offline and one-way per attempt; keep the
  source read-only until a clean verify.
- **Re-runnable.** Interrupt and re-run with the same arguments — already-migrated
  conversations are skipped and a partially-written conversation from an aborted
  run is re-migrated cleanly.
- **Source cleanup is out of scope for this tool.** Deleting the legacy Postgres
  database is a separate, receipt-gated step (PRD §722) and is not performed here.
