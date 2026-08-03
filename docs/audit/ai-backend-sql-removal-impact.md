# Impact analysis — Option 1: delete the Postgres arm from `ai-backend`, retire self-host

**Date:** 2026-08-03 · **Scope:** what breaks, what is gained, what it costs, and how checkpointing works today.
Companion to [desktop-sql-usage.md](desktop-sql-usage.md). Every claim carries `file:line`.

---

## 1. How checkpointing works right now

LangGraph checkpoints (in-flight graph state, paused approvals, interrupt resume points) are **separate
from the runtime store**. They are selected by their own three-way gate, in this order
(`services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:425`):

| #   | Builder                               | Saver                                                     | Gate                                                               | Durability                                                                                                                                                                                |
| --- | ------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `_file_store_checkpointer()` (`:441`) | **`AsyncSqliteSaver`** over `aiosqlite`                   | `RUNTIME_STORE_BACKEND=file` **and** `RUNTIME_FILE_STORE_ROOT` set | **Durable.** DB at `<root>/index/checkpoints.sqlite3` — deliberately _not_ the disposable catalog index, so wiping `index/catalog.sqlite3` never drops in-flight graph state (`:449-452`) |
| 2   | `_postgres_checkpointer()` (`:481`)   | `AsyncPostgresSaver` over a psycopg `AsyncConnectionPool` | `RUNTIME_STORE_BACKEND=postgres` **and** `DATABASE_URL` set        | Durable. Exists specifically so a **multi-process** server does not lose in-flight state to a process-local saver on worker restart (`:487-489`)                                          |
| 3   | fallback                              | `InMemorySaver` (`:430`)                                  | anything else                                                      | **Not durable** — lost on restart                                                                                                                                                         |

Two lifecycle seams exist only for arm 2, duck-typed on the class name so a desktop build never imports
the postgres checkpoint package:

- `setup_runtime_checkpointer()` (`:527`) — opens the pool, runs `AsyncPostgresSaver.setup()` DDL. Called from
  `runtime_api/app.py:1519` and `runtime_worker/__main__.py:119`.
- `teardown_runtime_checkpointer()` (`:553`) — closes the pool. Called from `app.py:1592`, `__main__.py:554`.

**The desktop already runs arm 1.** So on the checkpointer axis, option 1 deletes arm 2 and collapses
three branches to two (`AsyncSqliteSaver` when the file store is configured, `InMemorySaver` otherwise),
and both lifecycle seams become unconditional no-ops that can be deleted along with their four call sites.
**No desktop behaviour changes.**

---

## 2. What gets deleted

| Item                                                                                                                                                                            | Size            |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- |
| `src/runtime_adapters/postgres/`                                                                                                                                                | **16,576 LOC**  |
| `migrations/` (54 files)                                                                                                                                                        | **3,724 lines** |
| `src/runtime_api/sse/postgres_event_bus.py` (LISTEN/NOTIFY fan-out)                                                                                                             | 235 LOC         |
| `src/runtime_worker/jobs/encrypt_existing_columns.py` (one-shot backfill)                                                                                                       | 216 LOC         |
| PG checkpointer arm + `setup_/teardown_runtime_checkpointer` + 4 call sites                                                                                                     | ~80 LOC         |
| psycopg `AsyncConnection` factory in `runtime_api/app.py:1277-1303`                                                                                                             | ~30 LOC         |
| `otel.instrument_psycopg` (`observability/otel.py:218`)                                                                                                                         | ~10 LOC         |
| Tests: 50 files under `tests/**/postgres*`, 42 referencing the deleted symbols (of 765 total test files)                                                                        | —               |
| `scripts/migrate.py`, `scripts/restore_smoke.py`                                                                                                                                | —               |
| **Deps:** `psycopg[binary,pool]==3.3.4`, `langgraph-checkpoint-postgres==3.1.0`, `opentelemetry-instrumentation-psycopg`                                                        | —               |
| **Deployment:** `services/ai-backend/Dockerfile`, `services/ai-backend/docker-compose.yml`, `deploy/self-host/**`, `release-images.yml`, README self-host line (`README.md:62`) | —               |
| **CI:** `postgres-restore-drill.yml` (ai-backend leg), `ci-merge-live-gate.yml` (ai-backend leg)                                                                                | —               |

**≈ 21,000 LOC of source + 3,700 lines of migrations + ~50 test files.**

Retiring self-host also orphans the other three Dockerfiles (`backend`, `backend-facade`, `frontend`) —
`release-images.yml` is the only thing that builds them, so either it goes entirely or it keeps building
images for a deployment that no longer has a compose file to run them.

---

## 3. The headline: this does **not** get Postgres off the desktop

`services/backend` still requires it and fails closed without it:
`DesktopComposer.REQUIRED_ENV` lists `DATABASE_URL` first (`services/backend/src/backend_app/desktop_app.py:97`),
and the supervisor's postgres phase has no guard (`apps/desktop/main/services/supervisor.ts:137`).

So after option 1 the desktop still ships and boots the exact same postmaster:

| Cost                                                                        | Before        | After option 1                             |
| --------------------------------------------------------------------------- | ------------- | ------------------------------------------ |
| Staged Postgres binaries                                                    | 66 MB         | **66 MB**                                  |
| `pgdata` on user disk                                                       | 75 MB         | ~68 MB (the empty `atlas_ai` goes)         |
| Resident processes                                                          | 6             | **6**                                      |
| Boot: `initdb` + `pg_ctl` + migrations                                      | ~0.9 s serial | ~0.7 s (one fewer `CREATE DATABASE` spawn) |
| Supervisor/CLI failure modes (§5 of the audit)                              | 11            | **11**                                     |
| `postgres.ts`, `pg-facts.ts`, orphan logic in `doctor`/`repair`/`uninstall` | ~1,800 LOC    | **unchanged**                              |

The only desktop-visible wins are the 7.4 MB empty `atlas_ai` database and one 0.2 s process spawn —
**and both are available from a one-line guard on `supervisor.ts:144` without deleting anything.**
Everything else option 1 buys is _maintenance-surface_ reduction inside `ai-backend`, plus the
`boot-store-backend` / `migration-policy` / `migration-runner` carry-over subsystem in the desktop main
process, which exists solely to migrate PG→file and becomes dead once there is no PG arm to migrate from
(~600 LOC across `apps/desktop/main/services`).

**If the goal is "no Postgres on the desktop", option 1 is not the change that achieves it** — the
`services/backend` → SQLite port is (option (b) in the companion audit, 8–12 engineer-weeks).

---

## 4. What breaks

### 4.1 Self-host, deliberately

`deploy/self-host/docker-compose.prod.yml:159-164` runs `RUNTIME_STORE_BACKEND: postgres` +
`RUNTIME_EVENT_BUS_BACKEND: postgres` with API and worker as separate processes. There is no
drop-in replacement: the file store is single-writer/in-process by contract
(`src/runtime_adapters/file/runtime_api_store.py:9-15` — _"no cross-process flock, no WAL commit-markers"_),
and `_build_file_ports` hard-fails unless `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop`
(`factory.py:117`). Retiring self-host is therefore a **prerequisite**, not a side effect.

Images are still auto-published to GHCR on every `main` push touching `services/ai-backend/**`
(`.github/workflows/release-images.yml:9-26`), and README still advertises the one-line install
(`README.md:62`). Both must go in the same PR, or the repo advertises an install path that no longer works.

### 4.2 The `in_memory` adapter must stay

"Only filesystem" cannot mean deleting `in_memory` too. 765 test files depend on it, the root dev stack
pins `RUNTIME_STORE_BACKEND: in_memory` (`docker-compose.dev.yml:22`), and the file store refuses to
construct outside the desktop profile. Post-option-1 the service has **two** store backends
(`in_memory`, `file`), not one.

### 4.3 Account merge — already broken on desktop, so nothing is lost

Worth stating because it looks like a risk and isn't. The ai-backend leg of the account-linking saga
(`POST /internal/v1/admin/account-merge`) dispatches on the wired store and **fails closed with HTTP 501
on the file store** (`src/runtime_api/http/account_merge_routes.py:108-112`), with an explicit rationale:
_"reporting success without moving data would let the saga proceed to its destructive steps"_ (`:82-84`).
There is no `runtime_adapters/file/account_merge.py`. So `PostgresAccountMergeRekeyer` — the one genuine
multi-table ACID operation in ai-backend — is **unreachable on desktop today**. Deleting it changes the
error from 501-because-unsupported to 501-because-unsupported.

**This is a live product gap regardless of this decision:** linking a Google account on the desktop cannot
re-key runtime data. The desktop wires `AI_BACKEND_URL` for exactly this saga
(`apps/desktop/main/services/service-env.ts:488`). Worth its own ticket.

### 4.4 CI gates that lose their ai-backend leg

- `postgres-restore-drill.yml` — backup/restore drill over `services/ai-backend/migrations/**`. The desktop
  analogue `file-store-backup-drill.yml` already exists, so coverage does not go to zero.
- `ci-merge-live-gate.yml` — the live-Postgres account-merge + encryption-AAD re-wrap gate. Its ai-backend
  leg dies with §4.3; its `services/backend` leg stays.
- `tests/unit/runtime_adapters/test_store_conformance.py:44` parametrises `in_memory` / `file` / `postgres`,
  but the postgres param already `pytest.skip`s without a live DB, so CI signal is unchanged.

### 4.5 Irreversibility

This is the real cost. The Postgres arm is the **only multi-process-capable store** in the service. Deleting
it means any future hosted/cloud 0xCopilot offering starts from a rewrite, not a config flag — 16.5k LOC of
adapter, 54 migrations, the LISTEN/NOTIFY event bus and the multi-process checkpointer all have to be
re-derived. Git history preserves the code but not its currency; a year of drift makes a revert a rewrite
anyway.

Weigh that against: the arm is **provably unused on the desktop** (`factory.py:528` vs `:607` are mutually
exclusive), and 16.5k LOC of unexercised adapter is 16.5k LOC that every refactor must keep compiling.

---

## 5. What it buys

1. **~21k LOC + 3.7k lines of migrations deleted**, plus ~50 test files — all of it currently unexercised
   by the shipping product.
2. **Three heavyweight deps leave the ai-backend image and the staged desktop runtime:**
   `psycopg[binary]`, `langgraph-checkpoint-postgres`, `opentelemetry-instrumentation-psycopg`.
   (Measure the staged `services/` tree before/after — it is 401 MB today.)
3. **One storage story to reason about.** `factory.py` collapses from three arms to two; the checkpointer
   from three to two; `RUNTIME_EVENT_BUS_BACKEND` from two to one.
4. **The PG→file carry-over subsystem in the desktop main process becomes dead** and can follow:
   `boot-store-backend.ts`, `migration-policy.ts`, `migration-runner.ts`, `pg-facts.ts` (~600 LOC + their tests).
5. **CLAUDE.md compliance, incidentally:** `encrypt_existing_columns.py` is a one-shot data migration, which
   that file explicitly lists as not belonging in this service.

---

## 6. Sequencing, if taken

1. **Retire self-host first, in its own PR** — `deploy/self-host/**`, `release-images.yml`,
   `services/*/Dockerfile`, `README.md:62`, `docs/deployment/*`. This is the outward-facing, user-visible
   half; keep it separable so it can be reverted independently of the code deletion.
2. **Delete the PG arm** — adapter, migrations, event bus, checkpointer arm, one-shot job, deps, tests.
3. **Collapse the seams** — `factory.py` two-arm, checkpointer two-arm, delete `setup_/teardown_runtime_checkpointer`
   and their 4 call sites, drop `RUNTIME_EVENT_BUS_BACKEND` entirely.
4. **Follow-up PR:** delete the desktop carry-over subsystem and guard the `atlas_ai` `CREATE DATABASE`.
5. **Verify on the live packaged app**, not units — `tools/desktop-journeys/`. The unit suite is green over
   the file store today and will stay green whether or not the deletion broke a boot path.

---

## 7. Recommendation

**Do it, but do not expect it to remove Postgres from the desktop.** Option 1 is a _code-surface_ decision,
not a _runtime-footprint_ decision — the installer, the 6 processes, and all 11 supervisor failure modes
survive it untouched, because `services/backend` is the actual reason the postmaster boots.

The honest sequence, if the destination is "no Postgres in the product":

1. **Free, now:** guard the `atlas_ai` `CREATE DATABASE` (one line, `supervisor.ts:144`).
2. **Option 1** (~1–2 weeks incl. self-host retirement): deletes 21k unexercised LOC and three deps.
   Irreversible for any future hosted offering — that is the price.
3. **The `services/backend` → SQLite port** (8–12 weeks): the only change that actually deletes the 66 MB,
   the 6 processes, the unrotatable password, and the ~1,800 lines of postmaster-babysitting code.

Doing 1 and 2 without 3 leaves the desktop shipping an embedded PostgreSQL to serve ~40 small tables — which
is the state the companion audit already flags as the architectural incoherence.

---

## 8. Not verified

- **No packaged-app run.** Deletion impact is traced by reading gates and call sites, not by building.
- **Image-size delta not measured** — `psycopg[binary]` bundles libpq; the 401 MB staged `services/` figure
  is pre-deletion.
- **The 42 test files** were counted by symbol reference; how many are _entirely_ about Postgres versus
  parametrised over all backends was not separated, so "≈50 files deleted" is an upper bound.
- **`packages/audit-chain`** was not read here either (same gap as the companion audit) — it underpins the
  audit tables in both services and could constrain the storage engine.
- **Whether self-host has real users.** Repo-side it is live and advertised; download/telemetry evidence is
  outside what I can see, and that fact belongs to you, not to the code.
