# Orphan audit — what in the surfaces/ledger area is actually reachable

**Date:** 2026-08-05 · **Branch:** `claude/dynamic-generative-ui-audit-28d256`
Companion: [STATE.md](STATE.md) · [FINDINGS.md](FINDINGS.md)

Motivated by a direct question: _"do an audit first of what all uses this, i think a lot of it
is legacy/orphan code."_ This file records the parts verified by hand. The module-level
reachability sweep of `surfaces_v2` is tracked separately below.

---

## Part 1 — HTTP surface: 26 of 96 ai-backend routes have no caller

**Method.** Every route in `services/ai-backend/src/runtime_api/http/` is registered through
`router.add_api_route("<path>", handler, methods=[...])`, not a decorator — a detail that
defeats the obvious grep and produced three wrong counts before this one. Routes were
extracted by parsing `add_api_route` plus the file's `prefix=`, then matched as regexes
(`{param}` → `[^/]+`) against two caller sets:

- every `/v1/...` path literal in `packages/**` and `apps/**` (TS/TSX, excluding
  `node_modules` and `dist`), and
- every `/v1/...` / `/internal/v1/...` path literal in `services/backend-facade/src`.

The facade matters because it is a **per-route proxy**, not a catch-all — verified by reading
`settings_routes.py`, `connector_routes.py`, `todos_routes.py`, which each forward a named path.
So a route absent from both sets is unreachable from the product.

**Result: 96 routes, 26 unreachable (27%).**

| File                               | Route                                                  | Spot-checked                                     |
| ---------------------------------- | ------------------------------------------------------ | ------------------------------------------------ |
| `todo_extractions.py`              | `/v1/agent/todo-extractions` (+ `/accept`, `/reject`)  | ✅ zero hits                                     |
| `self_fork_routes.py`              | `/v1/agent/conversations/{id}/fork`                    | ✅ client calls only `/shares/{token}/fork`      |
| `audit_list_routes.py`             | `/v1/agent/audit/list`                                 | ✅ zero hits                                     |
| `routes.py`                        | `/v1/agent/audit/cursor`                               | ✅ zero hits                                     |
| `routes.py`                        | `/v1/agent/me`, `/v1/agent/me/conversations`           | ✅ client calls only `/v1/agent/me/inbox/stream` |
| `routes.py`                        | `/v1/agent/org`, `/org/purpose`, `/org/subagents`      | —                                                |
| `routes.py`                        | `/v1/agent/runs/{run_id}/calls`                        | ✅ zero hits                                     |
| `routes.py`                        | `/v1/agent/skills/system`                              | ✅ zero hits                                     |
| `local_release_control.py`         | `/v1/agent/export`, `/install`, `/rollback`, `/verify` | —                                                |
| `retention_routes.py`              | `/v1/retention/policies`, `/policies/{id}`             | —                                                |
| `legacy_migration_routes.py`       | `/internal/v1/admin/e2/legacy-migrations/{id}`         | —                                                |
| `legacy_migration_routes.py`       | `/internal/v1/admin/e2/legacy-stage-migrations/{id}`   | —                                                |
| `llm_embed_routes.py`              | `/internal/v1/llm/embed`                               | —                                                |
| `account_merge_routes.py`          | `/internal/v1/admin/account-merge`                     | —                                                |
| `agent_usage.py`                   | `/v1/usage/org/agent/{agent_id}`                       | —                                                |
| `evaluation_diagnostics.py`        | `/v1/agent/snapshot`                                   | —                                                |
| `desktop_workspace_attestation.py` | `/v1/agent/v1/agent/desktop-workspace-attestation`     | ⚠️ see below                                     |

### Two things this turned up that are separate bugs

1. **A doubled prefix.** `desktop_workspace_attestation.py` resolves to
   `/v1/agent/v1/agent/desktop-workspace-attestation`. Either the file sets a prefix that is
   already applied at registration, or the extraction mis-inferred it. Worth one look —
   if real, that endpoint has never been callable at its intended path.
2. **`/internal/v1/*` on ai-backend has no defined consumer.** `CLAUDE.md` documents the
   opposite direction (backend's `/internal/v1/*` consumed by ai-backend). Four of the
   unreachable routes are ai-backend internal routes; who is meant to call them is unstated.

### Known limits of this method — read before acting

- A client that assembles a path from variables (`` `${base}/fork` ``) is invisible to a
  literal grep. Every route above marked ✅ was spot-checked against that; the unmarked ones
  were not.
- Reachable-from-the-facade ≠ reachable-from-a-user. A facade route with no TS caller is
  still orphaned one layer up. Not measured here.
- This measures the HTTP surface only. A route being live says nothing about how much of
  the module behind it is live.

---

## Part 2 — the ledger vocabulary: one event can never be written

`LedgerEventType` declares **34** members (`surfaces_v2/ledger_models.py:57-90`).

**33 have a producer. Exactly one has none: `GATE_RESOLVED_V2` (`gate.resolved.v2`),
`ledger_models.py:89`.**

Verified directly: `GateResolvedV2Payload` is declared at `ledger_models.py:1077` and the class
constructor appears **nowhere** in the tree. Every other reference is a _reader_ —
`receipt_export_v2.py` (×2), `pending_work_v2_service.py` (×2), `receipt_v2.py`,
`pending_work_v2.py`, `legacy_v2_replay.py`, `presentation/lifecycle.py`, plus the wire mirror
`runtime_api/schemas/common.py` and the vocabulary registration itself.

Its twin `GATE_OPENED_V2` **does** have a producer, at
`capabilities/workspace/effects.py:243`.

> **The ledger can record a workspace gate opening and can never record it closing.**
> Five separate read models branch on an event that cannot exist.

**Emitted but unreachable on shipped defaults (5):** `OPERATION_REQUESTED`,
`OPERATION_CLASSIFIED`, `OPERATION_COMPLETED`, `OPERATION_FAILED` — producers at
`capabilities/operations/gateway.py:703/719/737/762` need `OPERATION_GATEWAY_MODE=enforce`;
`settings.py:335` defaults to `OFF` and `apps/desktop/main/services/service-env.ts:167` pins
`"off"`. Plus `GATE_OPENED_V2`, whose producer needs `WORKSPACE_EFFECT_MODE=enforce`.

**Live on shipped defaults: 28 of 34 (82%).** The ratio is not the problem. The one dangling
member is.

---

## Part 3 — module reachability: 52% of `surfaces_v2` actually executes

Six mapping agents, each adversarially re-checked by a verifier told to find a live caller the
mapper missed. **44 verifier judgements, zero refutations** — not one not-live claim was
overturned. Three label corrections only (test-only → dark, where product code constructs an
in-memory adapter at boot but never calls through it).

`surfaces_v2` is 18,967 lines. By module verdict: **65.3% live**, 27.5% dark, 3.4%
ci-gate-only, 3.7% test-only.

**That 65% is inflated**, and the reason is the most important structural finding here. Four of
the largest "live" modules are live in **one half only**:

| Module                             | Lines | What executes                            | What doesn't                                |
| ---------------------------------- | ----- | ---------------------------------------- | ------------------------------------------- |
| `lifecycle_reference_snapshots.py` | 1,201 | 76 lines at boot                         | `collect()` (`:503`) — no production caller |
| `staging.py`                       | 1,793 | the ~200-line fold                       | the ~800-line `WriteStager` producer        |
| `lifecycle_refs.py`                | 1,749 | the registry                             | the ~390-line enumerator (`:1342`)          |
| `legacy_stage_materialization.py`  | 156   | 1 of 8 exports, always → `None` (`:125`) | the other 7                                 |

Subtracting those orphan halves: **~9,944 lines (52.4%) actually execute for a real user on the
packaged desktop.**

A fold that runs while its own producer is dark is _the same defect as `GATE_RESOLVED_V2`_ — a
reader with no writer — just at function scope instead of vocabulary scope.

### The satellite lane is worse

The legacy-migration subsystem — **4,173 lines** across `agent_runtime/api/`, `runtime_api/`,
and `runtime_adapters/` — has never run and cannot run. `legacy_stage_migration_service.py`'s
only caller requires `E2_LEGACY_STAGE_MIGRATION_JOB_TOKEN`, which returns HTTP 503 when unset
(`auth.py:184-188`); the env name appears only inside test files.

### `runtime_worker/__main__.py` is dead — and that resolves two open flags

578 lines, no launcher. `services/ai-backend/Dockerfile:31` runs
`gunicorn runtime_api.app:app`; `docker-compose.dev.yml:23`, `Makefile:109`, and
`apps/desktop/main/services/service-env.ts:519` all set `RUNTIME_START_IN_PROCESS_WORKER="true"`.

It is also the **only** module that reads `ARTIFACT_CLEANUP_EXECUTION_ENABLED` and
`REPAIR_EXECUTION_ENABLED`. Those two flags were carried as an open question; they are answered.
Their consumer is a process nothing launches, so `artifact_cleanup_execution.py` (1,264) and
`repair_execution.py` (235) cannot run **at any flag value**. This is not a flag to flip.

### The client side confirms the diagnosis

`packages/api-types/src/legacyV2Replay.ts` is the one legacy module that **is** live — and it is
actively corrupting the modern render path. Independent confirmation of [STATE.md](STATE.md).

Also dead/dark on the client: `ReceiptSurface.tsx` (262, superseded by `ReceiptV2Surface` at
`RunDestination.tsx:3756`) and **seven built-but-never-mounted renderers** — Email (508),
SheetDiff (485), Sheet (299), Opportunity (218), Slide (184), SlideDiff (141),
`EditOverlay.tsx` (638).

### Established by hand, before the sweep

- `project_legacy_v2_replay` **is** consumed outside surfaces — by
  `agent_runtime/release/e2_final_conformance.py:50`, reached from
  `tools/check_e2_final_conformance.py:30` ← `.github/workflows/ci-e2-final-conformance.yml:95`.
  Any deletion repoints that gate first. This refuted the earlier plan's claim that nothing
  outside surfaces consumes it.
- There is **no `schema_version` / `ledger_version` field on ledger events**. That absence is
  why `isLegacySurfaceCreated` must guess "is this record historic?" from five string signals,
  all of which match current data. The guessing is a symptom; the missing stamp is the cause.

### ⚠️ One slice was not adversarially verified

The `refute:receipt-audit` agent died on a connection error. The receipt/audit lane's not-live
claims — `receipt_export_v2.py` (1,028), `receipt_v2.py` (793), `audit_export_verification.py`
(588), `receipt_export.py` (373) = **2,782 lines** — carry a mapper verdict with no second
opinion. Do not act on that slice without re-running the check.

### An orphan ratchet already exists

`services/ai-backend/tests/unit/test_orphan_ratchet.py` with a 33-line baseline at
`orphan_ratchet_baseline.txt` (which already lists `retention.py`). Enforcement should **extend
this**, not add a parallel mechanism.
