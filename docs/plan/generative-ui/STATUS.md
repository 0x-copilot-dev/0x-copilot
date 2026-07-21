# Generative UI — implementation status

Branch: `claude/generative-ui-components-5b6ba7`. Integration is sequential-merge on this branch; each PRD is built by an isolated-worktree agent (Opus 4.8), verified, then merged.

## Wave 0 — contract ✅ DONE
- **PRD-01** surface contract (SurfaceSpec schema in service-contracts, api-types envelope + `surface_spec_generated` event, pydantic mirror + parity test) — `f8017658`.

## Wave 1 — surfaces render live E2E ✅ DONE (verified)
- **PRD-02** backend surface emission + 12 curated builtin specs — `6d21c72a`
- **PRD-03** archetype renderer pack (record/table/message/doc/board) — `f875a11a`
- **PRD-04** cockpit wiring (surface tabs, spec-merge, pendingDiff, on-surface decisions) — `bc8b7adf`
- **PRD-06** word-level text diff + email `renderDiff` — `6b20e6d2`
- **PRD-02b** E2E wire fix: lift `surface_uri`/`surface` to tool_result event-payload top level (handles both fake-stream and production JSON-content shapes) + route both production draft emitters through the surface path — `8c140dae`
- **PRD-05** web host registration + flagged Run cockpit route (`runCockpitWeb`, default OFF) — `4cb7e21b`

**Verification (integration branch, all merged):**
- ai-backend `tests/unit`: **2848 passed**, 45 skipped, 0 failed.
- chat-surface: **2140 passed**. surface-renderers: **164 passed**.
- api-types / chat-surface / surface-renderers / frontend: typecheck **PASS**; frontend build **PASS**.
- Frontend suite: 1263 passed. 4 failures (`keymap` ×2, `ActivityRoute`, `ChatsArchiveRoute`) proven **pre-existing** — identical failures at merge-base `38ed7e2b` with a clean `npm install`; a local **Node 25** / jsdom-localStorage artifact, not introduced by this work and unrelated to surfaces.

E2E path now closed: MCP tool result → `SurfaceProjector` attaches `surface_uri` + envelope → worker lifts to event-payload top level → `eventProjector` binds → `TcSurfaceMount` resolves the archetype renderer → on-surface approve/reject wired to the decision endpoint.

## Wave 2 — long-tail generation ⏳ IN PROGRESS
- **PRD-07** spec generator (nano/mini model + `spec-authoring` skill bundle, schema-constrained + path-lint + retry, async off render path) + `SurfaceSpecStorePort` (in-memory/file). — in progress (`gu/prd-07`)
- **PRD-08** backend spec registry (org-scoped `/internal/v1/surfaces/specs`) + `BackendHttpSurfaceSpecStore`. — pending (after 07)

## Wave 3 — action safety ⏳ PENDING
- **PRD-09** edit-on-surface + gated commit (`approve_with_edits`, idempotency, precondition re-check, audit).

## Wave 4 — escape hatch + hardening ⏳ PENDING
- **PRD-10** tier-2 completion (production worker 6C + desktop lifecycle unstub).
- **PRD-11** eval harness + metering + injection lint + registry scoping.

## Notes for future sessions
- Worktree toolchain: run tests against worktree source using the main checkout's venv/node_modules. Python: `PYTHONPATH="src:../../packages/service-contracts/src"` + the main-checkout venv python. TS: shadow-symlink `node_modules/@0x-copilot/{api-types,chat-surface,surface-renderers,…} → worktree/packages/*` (gitignored) so cross-package imports resolve to worktree source; or a real `npm install` in a throwaway worktree for CI-equivalent runs.
- No new runtime deps were added by any PRD.
