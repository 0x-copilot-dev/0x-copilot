# AC2b — File-native runtime store as the desktop default (cutover)

Status: **Approved for implementation** · Owner: runtime · Supersedes the "opt-in only" posture of [02-ac2-file-session-store.md](02-ac2-file-session-store.md) for the `single_user_desktop` profile.

> Spec-first per [services/ai-backend/docs/CLAUDE.md](../../../../services/ai-backend/docs/CLAUDE.md). This document is grounded in a code-level audit; every claim below is anchored to `path:line` in the design sections.

---

## 1. Problem statement

The 0xCopilot desktop app creates and streams agent runs through the AI runtime (`ai-backend`). Today a run is **created and queued but never executed**: the composer shows the message, the SSE stream connects, and the UI sits on _"Listening for run events…"_ forever. No `model_delta`, no thinking, no `final_response`.

Root cause: the desktop supervises exactly three processes — `backend`, `ai-backend` (the API), `backend-facade` — and **no run executor**. The API can host an in-process worker, but its start guard (`runtime_api/app.py:797`) returns early unless the store backend is `in_memory`/`in_memory_async`. The desktop uses a durable store (Postgres today, `file` under the opt-in flag), so the guard fires and **no worker is ever constructed**. Runs queue with nobody to claim them. The web dev stack works only because it uses the in-memory store, which happens to satisfy the guard.

The guard conflates two orthogonal concerns — **store backend** (where data lives) and **process topology** (does this process run its own worker). The worker itself is backend-agnostic (production drives Postgres from a dedicated worker process every day). The store string is a stale proxy for "single-process dev/test" that the durable desktop backends break.

Separately, the desktop's persistence substrate is itself wrong for a single-user app. It embeds a **client-server RDBMS (Postgres)** — a supervised process, a `pgdata` dir, and a yoyo/psycopg migration dance — to serve exactly one in-process client. The decided direction ([00-overview.md §25](00-overview.md)) is a **file-native, single-writer JSONL store**: local-first, inspectable on disk (the "git for an agent's work" product model), embedded-native for one process, and with no RDBMS dependency for the AI runtime. That adapter (`RUNTIME_STORE_BACKEND=file`) is fully built but shipped **off by default**, and — because it was never the live path — carries a latent data-loss defect (citations) and has no run executor wired for it.

**This change makes the file-native store the desktop default, done right: one coherent single-process runtime (API + in-process worker + file store + in-memory event bus), with the worker-topology defect fixed, the citation durability defect closed, existing data preserved, and no regressions to the web or production/server paths.**

## 2. Goals / Non-goals

### Goals

- G1. Desktop runs execute and stream (deltas + thinking + tools + final response) at parity with the web surface.
- G2. The file-native store is the **default** AI runtime store for `single_user_desktop`.
- G3. Zero data loss and zero UI-history regression for any existing install; Postgres remains a one-env-var escape hatch.
- G4. The `backend` service, the web dev stack, and the production `api`+`worker` deployment are provably untouched.
- G5. Durability parity: nothing that is durable under Postgres becomes ephemeral under `file`.

### Non-goals

- N1. Automatic Postgres→file **data migration on boot**. The offline migration CLI (`runtime_adapters.migrate`) already exists and is tested; wiring it into first-run boot (incl. multi-scope discovery from a Postgres source) remains a documented fast-follow, consistent with the AC2 deferral. Existing-data installs stay on Postgres (safe) until they opt in or migrate.
- N2. Multi-process / horizontally-scaled file store. The file queue is single-writer by construction; this profile is single-process by definition.
- N3. Changing the `backend` service off Postgres (it still owns MCP/OAuth/vault/audit on embedded PG).
- N4. Making `append_events_batch` transactional under `file` (see NFR-6 — accepted, justified semantic difference).

## 3. Design principles

- **Topology, not store, decides execution.** "Run the worker in-process" is a property of _single-process deployments_, expressed by the deployment profile — the same signal the adapter factory already uses to admit the `file` backend (`factory.py:58`).
- **One coherent desktop runtime.** A single `ai-backend` process owns the API, the worker, and the store; the in-memory event bus is correct precisely because there is one process.
- **No-regression by construction.** For Postgres/in-memory, every new seam resolves to _exactly_ the value the old code computed; only the `file` path changes behavior. This makes "no regression for existing backends" a provable property, not a test-hope.
- **Fail closed for servers.** Server profiles (`saas_multi_tenant`, `single_tenant_*`) must never gain an in-process worker (they run a dedicated one) — double-claim is worse than no-claim.
- **Preserve data; make the safe default.** New installs get the new default; existing data is never stranded.

## 4. Architecture

### 4.1 The coherent desktop runtime (target)

```
                 ┌───────────────────────── ai-backend process ─────────────────────────┐
  facade ──SSE──▶│  runtime_api (FastAPI)                                                 │
                 │     └─ SSE adapter ── waits on ──┐                                       │
                 │                                  ▼                                       │
                 │                        InMemoryEventBus (per-run asyncio.Condition)      │
                 │                                  ▲                                       │
                 │     in-process RuntimeWorker ── notify_sync(run_id) after each append    │
                 │            │ claim_next()/mark_* (in-proc asyncio lock)                   │
                 │            ▼                                                              │
                 │     FileRuntimeApiStore  ── JSONL folders + state ledgers + SQLite index │
                 │            + FileCitationStore (durable) + AsyncSqliteSaver checkpointer  │
                 └───────────────────────────────────────────────────────────────────────┘
  backend (backend_app)  ──▶  embedded Postgres (atlas_backend)   ← unchanged
```

One process; in-memory bus wakes the SSE waiter sub-tick; the 2s poll is only a backstop. The store, worker, and API share one `FileRuntimeApiStore` instance, so the in-memory materialized views and projector stores (subagent panel) are consistent (`app.py:807` builds the worker from `app.state.runtime_ports`).

### 4.2 Change map (evidence-anchored)

| #   | Change                                      | File(s)                                                                                                                                                                 | Why                                                                                                                                                                                                                                                                            |
| --- | ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| C1  | Topology-gated worker start                 | `runtime_api/app.py:790-828`                                                                                                                                            | Start the in-process worker when `deployment.name == single_user_desktop` **or** store ∈ {in_memory,in_memory_async}; else return. Fixes G1 for both file and Postgres desktop; fails closed for servers.                                                                      |
| C2  | `citation_store` becomes a first-class port | `runtime_adapters/factory.py:109-133` (+ all 4 construction sites)                                                                                                      | Add `citation_store: CitationStorePort` to `RuntimePorts`. file → the same `FileCitationStore(layout)` the read-side `source_store` projects over; postgres → `postgres_store`; in_memory → one shared `InMemoryCitationStore`. Closes the citation data-loss regression (G5). |
| C3  | Worker prefers injected citation store      | `runtime_worker/loop.py:76-80`, `runtime_api/app.py:807`, `runtime_worker/__main__.py`                                                                                  | `RuntimeWorker` takes `citation_store=`; resolution becomes `injected or <today's isinstance fallback>`. Provably identical for postgres/in_memory; durable for file.                                                                                                          |
| C4  | Standalone worker refuses `file`            | `runtime_worker/__main__.py`                                                                                                                                            | The multi-process worker cannot coordinate with the single-writer file queue; fail fast with a clear message (defensive; not a desktop path).                                                                                                                                  |
| C5  | Desktop store resolver + file default       | `apps/desktop/main/services/service-env.ts`, `desktop-supervisor.ts`                                                                                                    | Resolve `file` vs `postgres` via an explicit policy (§5, FR-6). `buildServiceEnv` takes a resolved `aiStoreBackend` input (policy in the async supervisor, env assembly stays pure).                                                                                           |
| C6  | Docs                                        | this PRD + `services/ai-backend/docs/architecture/03-adapters.md`, `reference/env-vars.md`, `apps/desktop/README.md`, `docs/operations/desktop-file-store-migration.md` | Document the `file` backend, the new default, and the escape hatch.                                                                                                                                                                                                            |

## 5. Data-continuity policy (first-boot store resolution)

Resolution order for the desktop `ai-backend` store (`file` | `postgres`):

1. **Explicit opt-out** — `COPILOT_DESKTOP_FILE_STORE_V1` ∈ {`0`,`false`,`no`,`off`,`disabled`} → **postgres** (byte-identical to today's env).
2. **Explicit opt-in** — value ∈ {`1`,`true`,`yes`,`on`,`enabled`} → **file**.
3. **File store already present** — `<userData>/agent-data/v1` exists & initialized → **file** (idempotent).
4. **Existing Postgres history** — `atlas_ai` has conversation rows → **postgres** (preserve visible history; C1 makes runs execute here too). No regression.
5. **Fresh install** (none of the above) → **file** (the new default).

Rationale: this delivers the file default to the launch audience (fresh installs) and to anyone who opts in, while guaranteeing no existing install loses history — and the worker fix (C1) means streaming works under whichever store is chosen. Automatic migration (N1) would additionally upgrade existing-data installs to file; it is deferred to keep this change focused and low-risk, and because it needs multi-scope discovery from a Postgres source that the current engine defers.

## 6. Functional requirements

- **FR-1.** On `single_user_desktop`, the `ai-backend` API process MUST start exactly one in-process `RuntimeWorker` that claims queued runs, for store backend `file` and `postgres`.
- **FR-2.** The in-process worker MUST NOT be started for server profiles (`saas_multi_tenant`, `single_tenant_managed`, `single_tenant_self_hosted`).
- **FR-3.** The existing in-memory dev/test path (`make dev`, unit tests) MUST continue to start the in-process worker unchanged.
- **FR-4.** Under the `file` backend, citations written during a run MUST persist to the same durable `FileCitationStore` ledger that the Sources/`source_store` read path projects over, and MUST survive an app restart.
- **FR-5.** Under `postgres` and `in_memory`, citation read/write behavior MUST be identical to today.
- **FR-6.** The desktop MUST resolve the `ai-backend` store per §5, defaulting to `file` for fresh installs, preserving `postgres` for existing-history installs, and honoring both explicit env directions.
- **FR-7.** When the desktop resolves to `file`, the `ai-backend` Postgres migration gate MUST be skipped (no relational DB env); when it resolves to `postgres`, the gate MUST run exactly as today.
- **FR-8.** The `backend` service MUST always run on embedded Postgres with its migrations, in both store modes.
- **FR-9.** Runs on `file` MUST stream `model_delta`, reasoning/thinking, tool, and subagent events, and a terminal `run_completed`, over SSE, at event-parity with the web surface.
- **FR-10.** The standalone `python -m runtime_worker` MUST refuse to start against the `file` backend with a clear configuration error.

## 7. Non-functional requirements

- **NFR-1 (No regressions).** Web dev, `backend`, and production `api`+`worker` behavior is unchanged. Enforced by: profile-gated worker start; citation seam that resolves to the prior value for non-file backends; store resolver that reproduces the exact prior Postgres env on opt-out.
- **NFR-2 (Durability parity).** No port durable under Postgres is ephemeral under `file`. Citations (C2/C3) close the one known gap; the LangGraph checkpointer is _more_ durable under file (SQLite vs InMemory) — an improvement, not a regression.
- **NFR-3 (Single-writer safety).** The file queue's in-process lock is safe only in one process; the profile gate (FR-1/2) and the standalone-worker refusal (FR-10) enforce single-writer.
- **NFR-4 (Fail-closed config).** `file` requires `single_user_desktop` + `RUNTIME_FILE_STORE_ROOT` (`factory.py:58-73`); the event bus stays `in_memory` (never LISTEN/NOTIFY without a DB); a missing/invalid combination fails at startup, never silently degrades.
- **NFR-5 (Boot integrity).** First file boot never runs relational migrations, never requires `DATABASE_URL` for `ai-backend`, and the health gate stays DB-free (`/v1/health`).
- **NFR-6 (Event-batch durability, accepted difference).** `append_events_batch` is non-atomic under `file` (per-line append+fsync) vs Postgres's single transaction. A crash mid-flush persists _whole_ JSONL lines (no torn line — `_jsonl.py` fail-closed contract), only fewer of them; coalesced `MODEL_DELTA` deltas are incremental and superseded by the authoritative `final_response`, so partial persistence is benign. Making multi-line JSONL append atomic would rewrite the streaming hot path and add latency — explicitly out of scope, documented.
- **NFR-7 (Observability).** Worker start/skip decisions and the resolved store backend are logged at boot with the deciding signal (profile, backend, resolution reason).
- **NFR-8 (Testability).** Every requirement above has a unit test (§8); the store resolver and worker-start gate are pure/injectable and covered for all profile × backend combinations.
- **NFR-9 (Rollback).** Setting `COPILOT_DESKTOP_FILE_STORE_V1=0` returns the desktop to the exact prior Postgres runtime with no code change.

## 8. Regression-avoidance & test plan

Python (`services/ai-backend`, run via the service `.venv`):

- T1. `start_in_process_worker`: worker task **is** created for `single_user_desktop` × {`file`,`postgres`,`in_memory`}; **is not** created for `saas_multi_tenant`/`single_tenant_*` × `postgres`; still created for the dev default profile × `in_memory` (guards `make dev`). _(No such test exists today — new coverage for a previously-untested seam.)_
- T2. Factory `citation_store` wiring: file → the instance `source_store` reads; postgres → `postgres_store`; in_memory → shared with `source_store`.
- T3. Citation durability under `file`: write via the worker/run-handler path, reopen the store, assert citations are present and visible to `source_store`.
- T4. `RuntimeWorker(citation_store=…)`: injected wins; `None` reproduces the exact prior isinstance fallback (postgres → persistence; else InMemory).
- T5. Standalone worker refuses `file` (FR-10).
- T6. Existing suites green: `runtime_adapters/file/*`, `test_migration.py`, factory, worker loop, adapter parity.

Desktop (`apps/desktop`, vitest + typecheck):

- T7. Store resolver truth table (§5): opt-out→postgres, opt-in→file, file-store-present→file, pg-has-data→postgres, fresh→file.
- T8. `buildServiceEnv('ai-backend')` default now emits `RUNTIME_STORE_BACKEND=file` + `RUNTIME_FILE_STORE_ROOT`, and leaves `DATABASE_URL`/`RUNTIME_DATABASE_URL`/`RUNTIME_MIGRATIONS_AUTO_APPLY` unset; opt-out reproduces the prior Postgres env byte-for-byte.
- T9. Migration-skip gate fires in the (now default) file mode and runs in postgres mode.

Live verification (post-implementation): supervised desktop boot on the file store, POST a run, assert `run_completed` + streamed deltas/thinking over SSE (closes the CI gap where no test exercises a live run).

## 9. Risks & mitigations

| Risk                                                                          | Mitigation                                                                                       |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Deleting the worker guard double-starts a worker in prod (flag defaults true) | Gate on profile, not on the flag; server profiles fall through to `return`. T1 asserts it.       |
| Citation seam changes postgres/in_memory behavior                             | Injected value equals the prior fallback for those backends; T4 pins it.                         |
| Existing user (has Postgres "Hello" data) sees empty app                      | §5 step 4 keeps them on Postgres; C1 makes their runs stream anyway; opt-in/migration available. |
| File store used in a would-be multi-process topology                          | Profile gate + FR-10 refusal; documented single-writer invariant.                                |
| Event-bus accidentally resolves to postgres without a DB                      | Desktop pins `RUNTIME_EVENT_BUS_BACKEND=in_memory`; NFR-4.                                       |

## 10. Work breakdown

1. ai-backend: `RuntimePorts.citation_store` + factory wiring (C2) → worker injection (C3) → topology gate (C1) → standalone refusal (C4) → tests (T1–T5).
2. desktop: store resolver + file default + pure `buildServiceEnv` input (C5) → tests (T7–T9).
3. docs (C6).
4. Full suites + adversarial review + live smoke.
