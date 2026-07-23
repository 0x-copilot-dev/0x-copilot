# Provider-key / model-selection / credential-lane architecture

**Status source of truth for this effort.** Branch `claude/provider-key-model-arch`
off `origin/main` @ `292e3ea9`.

## Why

Five reported defects in the first-run "add a key → pick a model → send" flow were
symptoms of **four architectural faults** — three conflated concepts with no single
owner, plus a credential lane that fails silently:

| Concept                                           | Should be                                      | Was                                                                                         |
| ------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------- |
| **Catalog** (what models exist & are usable)      | one backend-owned, capability-typed list       | 7 divergent lists + an unfiltered raw provider probe                                        |
| **Credentials** (do I have a key for provider P)  | one resolver: env keys ∪ this user's BYOK      | answered 3 inconsistent ways (add-key UI, catalog badge = env-only, run gate = Null on web) |
| **Selection** (which model runs now / by default) | one persisted default the live pick seeds from | Settings default decorative; composer pick hardcoded-seeded & ephemeral                     |
| **Credential lane** (vault → runtime)             | required, fail-loud bridge                     | silently degrades to "no keys" and blames the user                                          |

Symptom → fault map (defect numbers from the investigation):

1. Add-key forces a model pick → selection welded into the credential surface.
2. `ada-2` embedding offered → raw provider probe used as a chat-model selection source.
3. Composer shows wrong model → catalog + default are two unsynchronized sources.
4. "API key missing" on send → lane unconfigured on web → `NullUserPoliciesResolver` drops BYOK keys.
5. Pill "Add key" doesn't open Settings → deep-link prop exists, no host wires it.

## Locked product decisions

- **Add-key is credential-only; the default model is auto-picked, zero prompt.**
  (First key silently becomes the working default; no model step in key entry.)
- **The composer's "Add a key" navigates to one Settings surface** — no inline form.

## The four moves

- **M1 — one credential-truth resolver.** The catalog's `configured`/"your key" badge
  is computed from the _same_ per-(org,user) policies resolver the run-create gate
  uses (env ∪ BYOK), not env-only. Badge and gate can no longer disagree.
- **M2 — one capability-typed catalog; everyone reads it.** Models carry a `kind`;
  every selection surface reads `GET /v1/agent/models` through one typed port. The
  raw provider probe is demoted to a key-liveness check only. Kills the 6 client
  lists and the `ada-2` leak by construction.
- **M3 — split credentials from selection.** One credential-only add-key surface;
  selection = persisted `workspace/defaults.default_model` → composer pill seeds from
  it; pill "Add a key" navigates to Settings; auto-pick default on first key.
- **M4 — the credential lane becomes a required deployment contract, fail-loud.**
  `BACKEND_BASE_URL` + `ENTERPRISE_SERVICE_TOKEN` wired in every topology; partial
  config raises instead of silently dropping keys; honest error taxonomy.

## Phased delivery

Recommended order: **M4 + M1 first** (unblocks sending, one credential truth) →
**M2** (kill the 7 lists / `ada-2`) → **M3** (UX consolidation). Each phase is
independently shippable and strictly improves consistency.

### Phase 1 — credential lane + one credential truth ✅ (this branch)

Backend-only, self-contained, tested.

- [x] **M4b** `UserPoliciesResolverFactory` fails loud on partial config
      (`TrustedBackendLaneError`); both-set → HTTP, neither-set → Null. Kills the
      silent-degradation trap. `agent_runtime/api/user_policies_resolver.py`.
- [x] **M4a-prod** self-host prod compose: add `BACKEND_BASE_URL: http://backend:8100`
      to `ai-backend` (it already had the token → was the exact half-configured state,
      which M4b would now crash on). `deploy/self-host/docker-compose.prod.yml`.
- [x] **M1** catalog `configured` reflects env ∪ caller BYOK. `ModelCatalog.build`
      takes `user_key_providers`; the models route resolves them via the same
      `runtime_user_policies_resolver` the run gate uses.
      `model_catalog.py`, `conversation_query_service.py`, `runtime_api/http/routes.py`.
- [x] Tests: `test_user_policies_resolver_factory.py` (all-or-nothing lane);
      `test_model_catalog.py::TestModelCatalogByokConfigured` (BYOK flips the badge).
      Result: 762 passed in the `agent_runtime/api` + `runtime_api` sweep; the one
      failure (`test_conversation_context_route … available_tokens`) is **pre-existing
      on origin/main** (stale LiteLLM context-window constant), tracked separately.

### Phase 1b — dev lane wiring ⏳ (needs live smoke)

- [ ] **M4a-dev** `docker-compose.dev.yml`: introduce a shared dev
      `ENTERPRISE_SERVICE_TOKEN` on `backend` + `ai-backend` and
      `BACKEND_BASE_URL: http://backend:8100` on `ai-backend`. Deferred out of Phase 1
      because it changes the dev backend's internal-auth posture and must be verified
      with `make docker-dev` + a real add-key→send. (Desktop already wires both via
      `apps/desktop/main/services/service-env.ts`, so desktop BYOK is unaffected.)
- [ ] Note for native `make dev`: with M4b, a half-configured `services/ai-backend/.env`
      now fails loud at startup — document "set both or neither".

### Phase 2 — one typed catalog ⏳ (foundation done)

- [x] `api-types`: add `kind` (`chat`/`embedding`/`image`/`audio`) to `ModelCatalogModel`
      (optional, defaults `chat`). `ModelKind` type. typecheck green.
- [x] ai-backend: `ModelCatalogItem.kind` (Literal, default `"chat"`) — the curated
      catalog is chat-only, so the default flows through `ModelCatalog.build` unchanged;
      test asserts every item is `kind == "chat"`. 763 passed.
- [x] web Settings model-select: `webModelCatalog.ts` **deleted**; `SettingsBinder`
      reads the one catalog via `listModels()` (`configured` = env ∪ BYOK from M1),
      dropping the separate provider-key probe. Frontend `tsc` 0 errors.
- [x] desktop: `CURATED_CLOUD_MODELS` **deleted**; all 3 consumers (Run composer
      `useRunComposerBindings`, Settings model-select `SettingsMount`, onboarding
      `useOnboardingComposerModels`) read the one catalog via `/v1/agent/models`
      (`mergeCatalog` folds fetched cloud + local; `configured` = env ∪ BYOK from
      M1). Desktop `tsc` 0 errors; **1095 desktop tests pass**.
- [ ] web-cockpit + Settings pickers: add explicit `kind === "chat"` filter (no-op
      today — catalog is chat-only — but the enforceable invariant; needs the
      worktree-package symlink so `.kind` resolves).
- [ ] legacy web `ChatScreen` `demoModels`: intentionally left (retirement path).

TS verification note: worktree apps resolve `@0x-copilot/*` through the main
checkout's `node_modules` (stale vs this branch). To typecheck worktree app
changes, symlink the branch's packages in first:
`ln -sfn "$WT/packages/<pkg>" "$WT/node_modules/@0x-copilot/<pkg>"`.

`recommended` (per-provider default marker) is deferred to Phase 3, where the
auto-pick rule pins its exact semantics — added with its consumer, not before.
The `ada-2` leak lives in the add-key modal's raw-probe dropdown; it is killed in
Phase 3 (credential-only add-key removes the model step entirely), not here.

### Phase 3 — split credentials/selection + nav + auto-pick ⏳

- [x] Composer pill "Add a key" → navigate to Settings (#5): `ModelPill` precedence
      flipped (nav wins over the inline port); `AssistantComposer` forwards
      `onAddProviderKey`; web + desktop main `RunComposer` wire it to their
      Settings→provider-keys nav. Follow-up: the desktop empty-state's
      `OnboardingComposer` mount doesn't forward it yet (its hero add-key already
      navigates). chat-surface + frontend + desktop tsc 0; 33 + 23 tests pass.
- [ ] One credential-only add-key surface (collapse `KeyForm` + `AddProviderKeyModal`,
      remove the mandatory model step) → kills #1 (forced model pick) + #2 (ada-2).
- [ ] Auto-pick default on first key (first credentialed provider → workspace default).
- [ ] Web composer seeds `selectedModel` from `workspace/defaults.default_model` → rest of #3.
- [ ] Forward `onAddProviderKey` through `OnboardingComposer` (desktop empty-state pill).

## Live smoke (packaged desktop topology, 2026-07-24)

Staged the runtime from this branch (Python 3.13 + embedded Postgres 17 + the 3
services built from source) and ran `tools/desktop-runtime/run-local.mjs` — the
supervised topology (production posture, BYOK lane wired, SIWE sign-in). **11/11
PASS**: all 3 services boot healthy, SIWE login, hermetic run streams 21 events to
`run_completed`.

The smoke CAUGHT a real over-aggression in the M4b guard: `ENTERPRISE_SERVICE_TOKEN`
also authenticates MCP/skills internal calls (over their own `*_REGISTRY_URL`s), so
"token set, `BACKEND_BASE_URL` unset" is a legitimate BYOK-off config — but the first
guard crashed it (and would crash any MCP-but-no-BYOK deployment). Fixed to
**asymmetric**: fail loud only on `URL`-without-token (unambiguously broken lane); on
token-without-`URL`, warn loudly + disable the lane (never crash). Also wired
`BACKEND_BASE_URL` into `run-local.mjs` so the harness faithfully mirrors
`service-env.ts`. Not smoked: a real-model run asserting the BYOK key reaches the
gate (needs a live key; the fake-model run bypasses the gate). Unit tests
(`test_run_coordinator_byok`, factory, catalog) cover the resolver path.

## Follow-ups / tech debt

- Per-request cache for the models route's BYOK resolve (M1 adds one internal
  round-trip per catalog fetch; correct but uncached).
- Pre-existing: `test_conversation_context_route … test_populated_run_returns_window_and_headroom`
  hardcodes a stale context window (1,050,000 vs live 272,000).
