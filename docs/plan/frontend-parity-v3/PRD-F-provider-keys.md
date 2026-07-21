# PRD-F — Provider keys convergence + fidelity

**Status:** Draft · **Surface:** Settings → Provider keys (web ⇄ desktop) ·
**Package:** `@0x-copilot/chat-surface` + `apps/frontend` + `services/backend` ·
**Blocked by:** B, E

## 1. Context & problem

- **P1 — Web has no parity page.** The design-parity `ProviderKeysPage` +
  `AddProviderKeyModal` are mounted **desktop-only** (`SettingsMount.tsx:562`). Web
  renders the legacy `apps/frontend/src/features/settings/sections/ProviderKeys.tsx`
  (plain card list, inline Save/Replace/Remove, no logo, no model chip, no 3-step
  modal, no empty state). Web parity ≈ 0%. (PRD-E establishes the web
  `SettingsSurface` mount that makes `keys → ProviderKeysPage` possible.)
- **P2 — CTA emphasis inverted + primary "Add a key" missing.** Design: per-empty-
  provider rows carry a neutral `.cbtn--sm` "＋ Add key", and a generic `.frow` +
  primary `.cbtn--pri` "🔑 Add a key" ("Any OpenAI-compatible endpoint works
  too"). Current: per-row buttons are `variant="primary"` (accent-filled, no ＋),
  and the generic primary CTA is **absent** (only a `SetNote`)
  (`ProviderKeysPage.tsx:397-413`).
- **P3 — Row action fidelity.** Rotate should be ghost (currently
  `variant="secondary"`, filled); Remove should be a ghost **trash icon**
  (currently `variant="danger"` filled text "Remove") (`:338-360`). Model chip
  should be `chip--ok` (success) at `1px 8px` and present on **every** connected
  row — currently `Badge tone="neutral"`, only when a model was chosen in-session,
  so reloaded keys show **no chip** (`:320-327`).
- **P4 — `validate` endpoint unused; model list is static.** The backend
  `POST /v1/settings/provider-keys/{provider}/validate` exists end-to-end
  (facade `settings_routes.py:97-107`; backend `routes.py:208-240` +
  `live_validator.py`), but `createProviderKeysPort` **omits** `validate`
  (`providerKeys.ts:208-260`), so step-2 "Validating…" is a client-side format
  check and step-3 lists the **static catalog**, not the provider's real models.
- **P5 — Catalog offers providers the backend rejects.** `groq`/`xai` are in the
  catalog (`contractBacked:false`) but the backend `ProviderName` enum is
  `openai/anthropic/google/openrouter`, so their "Add key" **422s on save**
  (`store.py:32-41`).

## 2. Goals / Non-goals

**Goals**

- G1 — Web mounts `ProviderKeysPage`/`AddProviderKeyModal` (via PRD-E's
  `SettingsSurface`); retire the legacy web section.
- G2 — Restore the design CTA structure (neutral per-row "＋ Add key" + primary
  generic "🔑 Add a key"); Rotate ghost, Remove ghost-trash; model chip `chip--ok`
  present on load.
- G3 — Wire the live `validate` port so step-2 is a real probe and step-3 lists the
  provider's actual models; persist the chosen model onto the row.
- G4 — Reconcile the provider catalog with the backend enum (widen enum **or** hide
  unsupported providers) so no "Add key" dead-ends in a 422.

**Non-goals**

- NG1 — Reworking `TokenVault`/encryption (already correct: encrypt-at-rest,
  `key_hint`-only, tenant-scoped, audited).
- NG2 — The global button scale (owned by PRD-B; this PRD consumes the corrected
  `.ui-button--sm`).

## 3. User stories

| ID     | As a…           | I want…                                            | so that…                                                          |
| ------ | --------------- | -------------------------------------------------- | ----------------------------------------------------------------- |
| US-F.1 | Solo user (web) | the redesigned provider-keys page                  | web matches desktop and the design                                |
| US-F.2 | Solo user       | a clear primary "Add a key" and quiet per-row adds | the primary action is obvious, rows stay calm                     |
| US-F.3 | Solo user       | my provider's real models after validating         | I pick a model that actually exists, and the key is verified live |
| US-F.4 | Solo user       | to only see providers that work                    | I never hit a 422 after filling in a key                          |

**Acceptance (US-F.3):** _Given_ I paste a valid OpenAI key, _when_ step-2 runs,
_then_ the port calls `validate` and, on success, step-3 lists OpenAI's real
models; picking one persists so the row shows it (as `chip--ok`) after reload.

## 4. Functional requirements

- **FR-F.1** — Web `keys` slug (via PRD-E binder) renders `ProviderKeysPage` with
  `createProviderKeysPort(transport)` + `modelChips` from workspace defaults; the
  legacy `sections/ProviderKeys.tsx` is removed from the web settings route.
- **FR-F.2** — CTA structure: per-empty-provider row → neutral small button
  (`.ui-button--sm`, no `--primary`) labelled "Add key" with a `plus` icon; add a
  generic `.frow` ("Another provider" / "Any OpenAI-compatible endpoint works
  too") + primary button "Add a key" with a `key` icon
  (`ProviderKeysPage.tsx:397-413`).
- **FR-F.3** — Rotate = ghost; Remove = ghost icon button with `trash` icon (PRD-A),
  `aria-label="Remove {provider}"`; model chip = success tone (`chip--ok`) compact
  (`1px 8px`), rendered whenever the row has a model.
- **FR-F.4** — Add `validate(provider, key)` to `createProviderKeysPort` calling
  `POST /v1/settings/provider-keys/{provider}/validate`; `AddProviderKeyModal`
  step-2 calls it (spinner "Validating with {provider}…"); on success step-3 lists
  the returned models; step-1 CTA renamed "Validate key". Client format check
  remains a fast pre-gate.
- **FR-F.5** — Persist the chosen default model so the connected row shows it after
  reload: either extend `ProviderKeySummary` with an optional `default_model`
  projected by the backend, **or** have the host merge workspace-defaults model per
  provider into `modelChips`. (Decision: backend projection preferred — one source;
  see §5.)
- **FR-F.6** — Catalog/enum reconciliation: widen backend `ProviderName` to include
  `groq`/`xai` (with `live_validator` + model catalog entries) **or** mark them
  `comingSoon` and hide "Add key". (Decision: widen if validators are cheap; else
  hide — no dead-end 422.)

## 5. Architecture & system design

- **SSOT.** One provider-keys page (`chat-surface`), one port
  (`createProviderKeysPort`), one backend service (`backend_app/provider_keys`).
  Web and desktop differ only in the binder that supplies the port + `modelChips`.
  The model shown on a row has ONE source — prefer a backend-projected
  `default_model` on `ProviderKeySummary` (avoids the frontend re-deriving from a
  separate workspace-defaults contract, the current split that leaves the chip
  empty).
- **Data flow.** list/set/delete/validate all via the port → facade
  (`settings_routes.py`, never logs key material) → backend service (TokenVault).
  `validate` returns `{ ok, models[] }`; the modal renders `models`.
- **Reuse vs new.** Reuse `ProviderKeysPage`, `AddProviderKeyModal`, `Modal`,
  backend `validate`/`live_validator`. Modify `providerKeys.ts` (add `validate`,
  maybe `default_model`), `ProviderKeysPage.tsx` (CTAs, actions, chip), backend
  `ProviderName` + `ProviderKeySummary`. Delete legacy web `sections/ProviderKeys.tsx`.

## 6. Affected files

- **Modify:** `apps/frontend/src/api/providerKeys.ts` (or the chat-surface port
  factory) — add `validate`, optional `default_model`;
  `chat-surface/src/settings/ProviderKeysPage.tsx`, `AddProviderKeyModal.tsx`;
  `packages/api-types/src/providerKeys.ts` (`ProviderKeySummary.default_model?`,
  validate response); `services/backend/src/backend_app/provider_keys/{store.py,
routes.py,service.py,live_validator.py}` (enum widen or hide + projection);
  facade `settings_routes.py` (if new field passes through).
- **Delete:** `apps/frontend/src/features/settings/sections/ProviderKeys.tsx`
  (web) after PRD-E web mount.

## 7. PR / commit breakdown

- **PR-F.1** — Web mount `ProviderKeysPage` (via PRD-E) + retire legacy section. M.
- **PR-F.2** — CTA/emphasis fix + Rotate/Remove/chip fidelity (consumes PRD-B scale
  - PRD-A icons). M.
- **PR-F.3** — `validate` port + modal live probe + real model list; `default_model`
  projection so the chip persists. M (FE+BE).
- **PR-F.4** — Catalog/enum reconciliation (widen or hide groq/xai). S/M.

## 8. Testing plan

- **Unit** (FE): CTA structure (per-row neutral add + generic primary present);
  Rotate ghost / Remove ghost-trash; chip renders success tone whenever a model
  exists; modal step-2 calls `validate`, step-3 lists returned models; step-1 CTA
  "Validate key".
- **Unit** (BE, pytest in `services/backend/.venv`): `validate` route returns
  `{ok, models}` for a good key and `ok:false` for a bad one; `ProviderName`
  accepts the reconciled set; `ProviderKeySummary` carries `default_model`.
- **Integration:** web `#/settings/keys` renders the parity page; add→validate→
  choose-model→reload shows the chip; unsupported provider cannot reach a 422.
- **Regression:** encrypt-at-rest/`key_hint`/audit-row behaviour unchanged
  (existing `provider_keys` tests green).

## 9. UI/UX acceptance checklist

- [ ] Web + desktop render the same `ProviderKeysPage`; legacy web section gone.
- [ ] Primary "🔑 Add a key" present; per-row "＋ Add key" neutral; Rotate ghost;
      Remove ghost trash-icon; model chip = success tone, `1px 8px`, on every
      connected row after reload.
- [ ] Modal: step-2 live validate spinner; step-3 real models; step-1 "Validate
      key"; 500px; StepDots; focus-trap/ESC/aria intact.
- [ ] Buttons use the PRD-B dense scale (radius/weight/height); light + dark.
- [ ] No provider in the picker can 422 on save.

## 10. Dependencies & sequencing

Upstream B (button scale), E (web `SettingsSurface` mount). Downstream: none.

## 11. Risks & mitigations

| Risk                                                           | Mitigation                                                                                                             |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Widening `ProviderName` needs validators/catalogs for groq/xai | If not cheap, hide them (`comingSoon`) — either way no dead-end                                                        |
| `default_model` projection touches workspace-defaults contract | Keep the existing `saveDefaultModel` write; add read projection additively; feature-flag if risky                      |
| Live `validate` latency/roundtrip                              | Keep client format pre-gate; spinner + timeout + graceful "couldn't verify, saved anyway" per current server behaviour |

## 12. Definition of done

- [ ] Web + desktop on one `ProviderKeysPage`; legacy section deleted.
- [ ] CTAs/actions/chip match design; `validate` live; model persists on the row;
      no 422 dead-ends.
- [ ] FE + BE tests green; encryption/audit invariants intact; typecheck + vitest +
      pytest green.
