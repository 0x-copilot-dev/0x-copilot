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

### Phase 2 — one typed catalog ⏳

- [ ] `api-types`: add `kind` (chat/embedding/…) + per-provider `recommended` to `ModelCatalogModel`.
- [ ] ai-backend: emit `kind`; mark one recommended chat model per provider.
- [ ] chat-surface: one catalog port/hook every composer consumes.
- [ ] web: delete `demoModels` (`ChatScreen`) + `webModelCatalog`; read the port.
- [ ] desktop: delete `CURATED_CLOUD_MODELS` (`desktopModelCatalog`); read the port.
- [ ] add-key modal: stop rendering the raw provider probe as a picker.

### Phase 3 — split credentials/selection + nav + auto-pick ⏳

- [ ] One credential-only add-key surface (collapse `KeyForm` + `AddProviderKeyModal`).
- [ ] Auto-pick default on first key (first credentialed provider → workspace default).
- [ ] Composer pill "Add a key" → navigate to Settings (wire `onAddProviderKey` in both hosts).
- [ ] Web composer seeds `selectedModel` from `workspace/defaults.default_model`.

## Follow-ups / tech debt

- Per-request cache for the models route's BYOK resolve (M1 adds one internal
  round-trip per catalog fetch; correct but uncached).
- Pre-existing: `test_conversation_context_route … test_populated_run_returns_window_and_headroom`
  hardcodes a stale context window (1,050,000 vs live 272,000).
