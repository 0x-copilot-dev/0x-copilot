# Orphan audit — what in the surfaces/ledger area is actually reachable

**Date:** 2026-08-05 · **Branch:** `claude/dynamic-generative-ui-audit-28d256`
Companion: [STATE.md](STATE.md) · [FINDINGS.md](FINDINGS.md)

Motivated by a direct question: _"do an audit first of what all uses this, i think a lot of it
is legacy/orphan code."_ This file records the parts verified by hand. The module-level
reachability sweep of `surfaces_v2` is tracked separately below.

---

## Part 1 — HTTP surface: 13 of 114 ai-backend routes have no caller

> **This section was wrong on first writing and has been corrected.** The original claimed
> "26 of 96 unreachable" and listed 17 rows, of which **10 carried paths the application never
> serves**. The error was one line of the extraction: it resolved **one prefix per file** and
> defaulted to `/v1/agent` when a file declared none. But `routes.py` alone builds five routers
> with five different prefixes, and several modules register fully-qualified paths on a bare
> `APIRouter`. That manufactured phantoms like `/v1/agent/audit/list` (really
> `/internal/v1/audit/list`, and reached — the facade fans out to both backends at
> `audit_routes.py:132`) and `/v1/agent/me` (really `/v1/usage/*`, reachable).
>
> Two specific retractions:
>
> - **`/v1/agent/conversations/{id}/fork` is called**, at
>   `apps/frontend/src/api/agentApi.ts:230`, through a template literal. The original marked this
>   ✅ spot-checked with "client calls only `/shares/{token}/fork`" — that spot-check read the
>   minified `apps/frontend/dist` bundle, not the source, and drew the opposite conclusion from
>   an adjacent function.
> - **The "doubled prefix" was an extraction artefact, not a bug.**
>   `desktop_workspace_attestation.py:19` builds `APIRouter(tags=[...])` with **no** prefix and
>   registers `/v1/agent/desktop-workspace-attestation` inline. The live route table contains it
>   once and contains zero doubled paths. The endpoint is also reachable — the facade names it at
>   `desktop_attestation_routes.py:13`.
>
> The corrected figures below come from `tools/check_route_reachability.py`, which resolves the
> real route table and is pinned by 47 tests plus a `test_no_route_carries_a_doubled_prefix`
> assertion against the live tree.

**Method.** Routes are registered through `router.add_api_route("<path>", handler, methods=[...])`,
not decorators — a detail that defeats the obvious grep (a decorator scan finds 1 of 114). Each
route's real prefix is resolved per _router_, not per file, then matched as a regex
(`{param}` → `[^/]+`) against three caller sets:

- every `/v1/...` path literal in `packages/**` and `apps/**` (TS/TSX, excluding
  `node_modules` and `dist`),
- every path literal in `services/backend-facade/src`, and
- every path literal in `services/backend/src`.

The facade matters because it is a **per-route proxy**, not a catch-all — verified in
`settings_routes.py`, `connector_routes.py`, `todos_routes.py`, which each forward a named path.
A route absent from all three is unreachable from the product.

**Result: 114 routes scanned, 13 unreachable (11%).** All 13 are baselined with a written reason
in `tools/route_reachability_baseline.txt`; the gate fails on a _new_ one and, because a wired
route makes its baseline line stale, the file can only shrink.

The 13 fall into three groups, and only the third is debt worth burning down.

**Operator-only ingress (2)** — service-token gated and deliberately not proxied by the facade,
per `app.py`'s own comment. Having no product caller is the design.

| Route                                                          |
| -------------------------------------------------------------- |
| `/internal/v1/admin/e2/legacy-migrations/{migration_id}`       |
| `/internal/v1/admin/e2/legacy-stage-migrations/{migration_id}` |

**Local-dev harness ingress (5)** — mounted only when `app.state.local_release_control_service`
is set, which no shipped configuration does. No harness in `tools/` calls them either.

| Route                                           |
| ----------------------------------------------- |
| `/internal/dev/evaluation/diagnostics/snapshot` |
| `/internal/dev/evaluation/releases/verify`      |
| `/internal/dev/evaluation/releases/install`     |
| `/internal/dev/evaluation/releases/rollback`    |
| `/internal/dev/evaluation/releases/export`      |

**Genuinely unwired product surface (6)** — each built to a spec, each with unit tests, none
forwarded by the facade, so no client could reach them even if one tried. Wire or delete.

| Route                                         | What it was for                                                                          |
| --------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `/v1/retention/policies`                      | retention policy admin (list + create); only its legal-holds siblings got wired          |
| `/v1/retention/policies/{policy_id}`          | retention policy delete                                                                  |
| `/v1/todo-extractions`                        | todo-extraction proposal list (P3-A2)                                                    |
| `/v1/todo-extractions/{extraction_id}/accept` | todo-extraction accept                                                                   |
| `/v1/todo-extractions/{extraction_id}/reject` | todo-extraction reject                                                                   |
| `/v1/usage/org/agent/{agent_id}`              | per-agent usage rollup (P8-A4); the ai-backend half landed, the facade forward never did |

### Known limits of this method — read before acting

- A client that assembles a path from variables (`` `${base}/fork` ``) is invisible to a literal
  scan. This is exactly what the original write-up got wrong, and it is why the baseline format
  requires each line to declare which of two reasons applies: genuinely unreachable, or reached
  by an invisible caller.
- Reachable-from-the-facade ≠ reachable-from-a-user. A facade route with no TS caller is still
  orphaned one layer up. Not measured here.
- This measures the HTTP surface only. A route being live says nothing about how much of the
  module behind it is live — see Part 3, where four of the largest _live_ modules turn out to
  execute only one half.

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

### The receipt / audit-export slice — now verified (2026-08-05)

The slice below was originally unchecked; it has since been mapped and adversarially attacked.
**All four modules stay `dark`, and the recommendation is WIRE IT, not delete it.** The
original verdict was right by accident — its stated mechanism is wrong.

**Every backend gate is OPEN on the packaged desktop.** The original claim was that
`receipt_export_v2` is store-gated off, citing `in_memory/runtime_api_store.py:115`
(`receipt_export_v2_available = False`). But the desktop runs the **file** store
(`service-env.ts:512` sets `RUNTIME_STORE_BACKEND="file"`), and
`file/runtime_api_store.py:271` sets that flag **`True`**. The signer gate is open too
(`service-env.ts:424,557` set `AUDIT_HMAC_KEY`), and the catalog is wired unconditionally
(`runtime_api/app.py:735`). Both routes would return 200 if anything called them.

**It is dark because nothing above the facade calls it.** Repo-wide, `receipt/export` appears
only in the route, the facade proxy, type declarations (`api-types/src/ledger.ts:1745,1775,1789`),
an authz route table, and tests. `ReceiptExportV2` has zero consumers in `packages/` or `apps/`.
This is the limitation the repo's own gate concedes at
`tools/check_route_reachability.py:105-107` — facade-reachable is not user-reachable — which is
why no receipt entry appears in the route baseline.

**Four corrections to earlier claims, including two of mine:**

| Claim                                                           | Reality                                                                                                                                                                                                |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `receipt_export.py` (v1) is superseded by v2                    | **v2 depends on v1.** `receipt_export_v2.py:978` calls `ReceiptExportVerifier(...).verify(bundle)` in `_verify_legacy`. Deleting v1 breaks v2.                                                         |
| Deleting the lane would strand `packages/audit-chain`           | **Refuted.** `copilot_audit_chain` has ten non-test `src` importers; only two are in this lane. `services/backend`'s `store.py`, `connectors/store.py`, `projects/store.py` are independent consumers. |
| `receipt.py` is a third caller of `ReceiptFoldV2`               | A **re-export, not a call** — `receipt.py:44` imports it; the only other occurrences are `__all__` at `:688-689`.                                                                                      |
| `packages/audit-chain` is "shared Python + TS" (root CLAUDE.md) | **Python-only.** No `package.json`, no TS. `docs/audit/flows/data-persistence-retention.md:155` already records this correction.                                                                       |

**A live side effect nobody had noticed.** `FileAuditExportVerificationStore.__init__`
(`file/audit_export_verification_store.py:40-46`) eagerly does
`mkdir(mode=0o700, parents=True, exist_ok=True)`, and the store is constructed at every API
boot (`app.py:226` → `_build_coordinators:516` → `:735`). So **a packaged desktop creates an
`audit_export_verification/` directory on every boot and never writes a byte into it** — the
object is live, every writer is unreachable.

**The worker arm is doubly unreachable.** `AuditExportVerificationSamplingLoop`'s only
construction site is `runtime_worker/__main__.py:415`, and that module never executes on
desktop (the app runs an in-process worker built directly at `runtime_api/app.py:1416-1495`).
Setting `AUDIT_EXPORT_VERIFICATION_SAMPLING_ENABLED=true` would still start nothing.

**Why it is not a deletion candidate.**

1. A prior audit already ruled on it. `docs/audit/ai-backend-smells/REDUCTION-LEDGER.md:184`,
   under _"Genuine gaps — keep, and in two cases wire"_: `audit_export_verification` (614) —
   _"real **compliance** obligations, currently unwired. **WIRE_IT.**"_
2. All four are **11–12 days old** (2026-07-24/25), and `TASKS.md:81` adopts: nothing younger
   than 30 days is deleted without asking its author.
3. PRD-E1 §D7 states verbatim the rule root CLAUDE.md restates — _"run execution does not
   silently claim audit exportability if the adapter is no-op/in-memory"_ — and these files are
   its **only** implementation (`in_memory/runtime_api_store.py:113-115` +
   `conversation_query_service.py:808`).

Note it is **not** in the customer-facing matrix: `docs/security/control-mapping.md` cites
`runtime_audit_log` / `PostgresRuntimeApiStore.write_audit_log` for audit durability, not this
lane. So deleting it would not falsify a customer claim — but it would delete a spec'd control
a prior audit told us to wire.

### ⚠️ (historical) One slice was not adversarially verified

The `refute:receipt-audit` agent died on a connection error. The receipt/audit lane's not-live
claims — `receipt_export_v2.py` (1,028), `receipt_v2.py` (793), `audit_export_verification.py`
(588), `receipt_export.py` (373) = **2,782 lines** — carry a mapper verdict with no second
opinion. Do not act on that slice without re-running the check.

### An orphan ratchet already exists

`services/ai-backend/tests/unit/test_orphan_ratchet.py` with a 33-line baseline at
`orphan_ratchet_baseline.txt` (which already lists `retention.py`). Enforcement should **extend
this**, not add a parallel mechanism.
