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

## Wave 2 — long-tail generation ✅ DONE (verified)

- **PRD-07** spec generator (nano/mini model + `spec-authoring` skill: SKILL.md + 6 few-shot examples; schema-constrained decode, path-lint + retry-once, async off render path, per-run cap) + `SurfaceSpecStorePort` (in-memory/file) — `e0177992`. **Adversarially verified CONFIRMED** on all 7 criteria (injection kill-switch driven with hostile inputs directly; no live model in CI; structured-output enforced; skill real).
- **PRD-08** backend surface-spec registry (org-scoped `/internal/v1/surfaces/specs`, service-token + org/user header auth, 422 on invalid, override precedence) + `BackendHttpSurfaceSpecStore` (TTL cache) + `SURFACE_SPEC_STORE_BACKEND` selection — `cd8347a2`.
- **Hardening** (`6df04901`): from the adversarial review — lint `link.url_path` across all rows, not just `items[0]`, so the backend kill-switch is sufficient on its own (FE sanitiser remains defence-in-depth).

**Verification:** ai-backend `tests/unit` **2949 passed** (1 known-flaky wall-clock timing test, passes on re-run), 45 skipped; backend surface_specs **42 passed** (29 unit + 13 routes); dark-capabilities gate **exit 0**; migration manifest clean.

## Wave 3 — action safety ✅ DONE (adversarially verified)

- **PRD-09a** contract — `approve_with_edits` + `SurfaceEdits` + `isSurfaceEdits` + facade passthrough — `765b2393`.
- **PRD-09b** gated commit executor — server-side edit merge (`extra=forbid`, connector/scope from the server-held proposal only), idempotency (ledger claim before execute + `(draft_id,version)` CAS), precondition re-read before write (drift → abort + supersede), ordered audit chain, fail-closed — `c100a776`.
- **PRD-09c** edit-on-surface overlay (message/record forms, body textarea + hunk-toggle over `DiffText`, wired to `approve_with_edits`) — `54bd58bf`.
- **Adversarial pass on the commit gate: CONFIRMED — no defects, no security holes, no false claims.** Actively hunted a bypass across executor + coordinator + live worker path; none. Fail-closed, merge-integrity, idempotency, precondition, audit all traced through code + non-vacuous tests. Disclosed deviation (executor not wired to the live draft-send path — no real send-connector exists yet) opens no hole; live path is approval-gated + idempotent via the draft status machine + version CAS.
- **Hardening follow-up** (in progress): explicit 422 for `edits` on non-editable approval kinds (was a silent drop) + worker-side field allowlist re-assert.
- Deferred (blocked on there being a real connector): wire the executor onto the live draft-send / MCP-field-write path.

**Verification:** ai-backend `tests/unit` **2976 passed**, 45 skipped; chat-surface **2152 passed**; 26 safety-invariant tests; dark-capabilities gate exit 0.

## Wave 4 — escape hatch + hardening ✅ DONE

- **PRD-10** tier-2 completion — production Web Worker (`createTier2WorkerFactory`, globals scrubbed so fetch/XHR/importScripts fail), desktop lifecycle unstubbed end-to-end (`RunFeedLifecycleEventSource` → real AST + smoke-render pipeline → install), read/write install consent gate (write layouts require a one-time consent, read installs silently), `RUNTIME_TIER2_GENERATION` default-off, D29 fail-closed un-refused — `a804903b`.
- **PRD-11** hardening — spec-level injection lint (named `SpecLintCode` reason codes), OTel metering counters (verified via `InMemoryMetricReader`), hermetic eval harness (22 real + 7 adversarial fixtures, replay model, baseline committed, `evals` marker excluded by default), registry-scoping factory + provider (default stays the module-global — zero behavior change) — `3be3632e`.

---

## ✅ ALL WAVES COMPLETE — PRD-01 … PRD-11 + hardening

**Closing full-repo verification (integration branch):**

- ai-backend `tests/unit` **3011 passed**, 45 skipped, 0 failed · backend surface_specs **42** · chat-surface **2178** · surface-renderers **164** · desktop tier-2 **203** (desktop full **961**, only the pre-existing `PaletteHost` jest-dom flake) · facade **230**.
- Typecheck PASS: api-types, chat-surface, surface-renderers, frontend. Frontend build PASS.
- dark-capabilities CI gate **exit 0**; migration manifest clean.
- Only pre-existing environmental failures remain (local **Node 25** jsdom + a jest-dom matcher), proven identical at the merge-base — none introduced by this work.
- Security-critical cores (spec generator, gated commit) each passed a **skeptical adversarial verification that hunted for a bypass** (injection kill-switch, no-live-model, fail-closed, idempotency) — both CONFIRMED with no holes.

**Follow-ups (tracked, non-blocking):** wire `SurfaceCommitExecutor` onto the live draft-send/MCP-field-write path once a real send-connector exists; optionally rename `RUNTIME_TIER2_GENERATION` → `RUNTIME_ENABLE_TIER2_GENERATION` for mechanical dark-cap enforcement; grow the archetype library (event/timeline/dashboard/file) and curated builtin specs beyond the initial 12.

## Notes for future sessions

- Worktree toolchain: run tests against worktree source using the main checkout's venv/node_modules. Python: `PYTHONPATH="src:../../packages/service-contracts/src"` + the main-checkout venv python. TS: shadow-symlink `node_modules/@0x-copilot/{api-types,chat-surface,surface-renderers,…} → worktree/packages/*` (gitignored) so cross-package imports resolve to worktree source; or a real `npm install` in a throwaway worktree for CI-equivalent runs.
- No new runtime deps were added by any PRD.
