# PRD-01 — Provider Keys (BYOK)

**One-line summary:** Converge web and desktop onto the single chat-surface `ProviderKeysPage`/`AddProviderKeyModal`, fix the desktop default-model-chip divergence, decide the "OpenAI-compatible endpoint" affordance, replace the single workspace-default hack with a true per-provider default model, ship real brand logos, and reconcile the "macOS Keychain" copy with where keys actually live — all while keeping the plaintext-once security invariant intact.

**Status:** Draft
**Nav slug:** `provider-keys` (tag: **BYOK**), group **Models & keys** (`settingsNav.ts`)
**Related files:**

- SSOT UI: `packages/chat-surface/src/settings/ProviderKeysPage.tsx`, `packages/chat-surface/src/settings/AddProviderKeyModal.tsx`, `packages/chat-surface/src/settings/data/providerKeys.ts`, `packages/chat-surface/src/settings/SettingsChrome.tsx`
- Host binders: `apps/frontend/src/features/settings/SettingsBinder.tsx` (web), `apps/desktop/renderer/SettingsMount.tsx` (desktop)
- Contracts: `packages/api-types/src/providerKeys.ts`, `packages/api-types/src/workspaceDefaults.ts`
- Facade: `services/backend-facade/src/backend_facade/settings_routes.py`
- Backend: `services/backend/src/backend_app/provider_keys/{routes,service,store,live_validator}.py`; migrations `0034_*`, `0036_*` (the `provider_api_keys` CHECK)
- Dead code (retire): `apps/frontend/src/features/settings/sections/ProviderKeys.tsx`, `apps/frontend/src/api/providerKeysApi.ts`

---

## 2. Problem statement

**User pain.** A BYOK, fully-local user brings their own provider key, picks a default model for that provider, and expects the choice to stick. On **desktop** it does not: the default-model chip on a connected row is populated only from the in-session pick and **vanishes on reload**. Two providers cannot each keep their own default model — picking a default for a second provider silently overwrites the first. The "Add a provider → Another provider / Any OpenAI-compatible endpoint works too" affordance reads like a real feature but only opens the modal for the first known provider — there is no custom-endpoint entry, so a user who wants a self-hosted or unlisted OpenAI-compatible provider dead-ends. Connected rows show a flat first-letter glyph rather than the colored brand logos the design promises. And the on-screen storage promise ("macOS Keychain") does not match where the key is actually stored.

**Engineering reality.** The backend is fully wired and honest: `GET` list, `PUT {provider}` (add _and_ rotate via `ON CONFLICT` upsert preserving `created_at` — there is **no** separate rotate endpoint), `POST {provider}/validate` (live tri-state probe that does **not** store), `DELETE` (204, idempotent). Plaintext is encrypted at rest via `TokenVault`; reads return only a last-4 `key_hint`. The chat-surface page is the one settings section already unified across both hosts — but the two host binders have **drifted**: web's `SettingsBinder.tsx` seeds `modelChips` from `useWorkspaceDefaults`; desktop's `SettingsMount.tsx` mounts `<ProviderKeysPage port … onToast … />` **without `modelChips`**. Meanwhile the "default model per provider" abstraction is a lie at the data layer: `saveDefaultModel` does a read-merge-write full-document `PUT /v1/agent/workspace/defaults` that stores a **single** `default_model`, and `ProviderKeySummary` has **no** per-provider model field. Legacy `apps/frontend/src/features/settings/sections/ProviderKeys.tsx` (+ `providerKeysApi.ts`, which lacks `validate`) still ships through `SettingsScreen` — a second, inferior copy of this exact section that must be retired as part of the convergence.

This PRD closes the cross-host drift, upgrades the persistence model so per-provider defaults are real, and resolves the two honest-label questions (custom endpoint, keychain copy) with concrete decisions.

---

## 3. Goals & non-goals

**Goals**

1. **Cross-host parity:** web and desktop mount the identical SSOT `ProviderKeysPage` with identical props and identical behavior — including the default-model chip surviving reload on **both** hosts.
2. **True per-provider default model:** a connected row's default model persists per provider and does not clobber other providers' defaults.
3. **Visual parity with v3:** real colored brand logos, the exact card/row/modal structure, copy, and states from the design.
4. **Honest custom-endpoint story:** either a real OpenAI-compatible custom-endpoint add flow, or a label that plainly states it is not yet available (decision in §11).
5. **Reconciled storage copy:** the on-screen storage promise matches where keys actually live, per deployment substrate.
6. **Retire the legacy web section** so `provider-keys` has exactly one implementation.
7. **Preserve the plaintext-once invariant** end-to-end: plaintext appears exactly once (PUT body / validate body), is never stored in the client, never logged, never echoed; reads carry only `key_hint`.

**Non-goals**

- Adding new **providers** to the backend enum (Groq/xAI stay `comingSoon`); widening the enum is a separate 4-place change (§9) and out of scope here except as an enabling note.
- Server-side "reveal key" (explicitly forbidden by the contract — "do not add a reveal field").
- Redesigning `workspace_defaults`, model curation (the `models` section), or local models (PRD-covered elsewhere).
- Multi-key-per-provider (each provider holds exactly one key; `PUT` upserts).

---

## 4. Users & scenarios

**Personas**

- **Dana — solo-desktop BYOK user** (`single_user_desktop` profile). Runs 0xCopilot locally, pastes their own Anthropic + OpenAI keys, expects zero cloud storage of secrets and per-provider control over which model each key defaults to.
- **Priya — team admin** (`team` profile, web). Same section, but keys are org-scoped and audited; cares that plaintext never lands in logs/audit and that the storage copy is defensible in a review.

**Scenarios**

1. **Add first key (happy path).** Dana opens Provider keys → "Add a provider" → Anthropic → "Add key" → pastes `sk-ant-…` → "Validate key" → step-2 spinner "Validating with Anthropic…" → step-3 "Key verified · sk-••••", picks _Claude Sonnet 4.5_ from the live model radio list → "Add key". Row moves to **Connected** with a success chip showing the chosen model and a masked hint `sk-ant-•••• 4f2a`.
2. **Two providers, independent defaults.** Dana adds OpenAI and picks _GPT-5_ as its default. The Anthropic row's default chip **still says Claude Sonnet 4.5**. Both survive a reload — on **desktop and web**.
3. **Rotate.** Dana clicks **Rotate** on the OpenAI row → the modal reopens at step 1 (key entry) → validates → saves. The `updated_at`/hint refresh; `created_at` is preserved server-side (same `PUT` upsert). No default-model change.
4. **Offline add.** Dana is offline; validate returns `valid: null` (`provider_unreachable`). The flow does **not** hard-fail — it advances to step 3 using the catalog model list, and `PUT` stores the key with `live_check: "skipped_unreachable"`. A Toast notes the key was stored without a live check.
5. **Custom / unlisted provider.** Dana clicks "Another provider" expecting to point at a self-hosted OpenAI-compatible endpoint — the flow must either let them (real feature) or clearly tell them it is not available yet (decision §11), never silently dead-end into a 422.

---

## 5. Current state

| Capability                            | State                                                                                                                                                                                             | Evidence                                                                                                                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend list/add/rotate/delete        | **Built & live** — `GET` list, `PUT {provider}` (add+rotate, `ON CONFLICT` upsert preserving `created_at`), `DELETE` 204 idempotent                                                               | `services/backend/src/backend_app/provider_keys/routes.py:149,166,247`; upsert in `store.py:249`                                              |
| Live validate probe (tri-state)       | **Built & live** — `POST {provider}/validate` → `{valid, models, reason}`, does not store                                                                                                         | `routes.py:209`; `live_validator.py`                                                                                                          |
| Encryption at rest + `key_hint`       | **Built & live** — `TokenVault` encrypt-on-write, only last-4 hint returned, identity-audit rows on set/delete                                                                                    | `service.py`; `store.py:53`                                                                                                                   |
| Facade proxy                          | **Built & live** — thin verbatim GET/PUT/validate/DELETE, org/user from verified session                                                                                                          | `services/backend-facade/src/backend_facade/settings_routes.py`                                                                               |
| api-types contract                    | **Built & live** — `ProviderKeyProvider` union (openai/anthropic/google/openrouter), `ProviderKeySummary{provider,key_hint,updated_at}`, Put/Validate shapes, `PutProviderKeyResponse.live_check` | `packages/api-types/src/providerKeys.ts`                                                                                                      |
| SSOT page + modal + port              | **Built & live, unified across hosts** — `ProviderKeysPage`, `AddProviderKeyModal`, `createProviderKeysPort`, `PROVIDER_CATALOG`                                                                  | `packages/chat-surface/src/settings/…`, `data/providerKeys.ts`                                                                                |
| **Desktop default-model chip**        | **Divergence BUG** — desktop mounts `ProviderKeysPage` **without `modelChips`**; chip is session-only and disappears on reload                                                                    | `apps/desktop/renderer/SettingsMount.tsx:700` (`<ProviderKeysPage port … onToast … />`) vs web `SettingsBinder.tsx:201` (passes `modelChips`) |
| **Per-provider default model**        | **Stubbed / wrong** — `saveDefaultModel` writes a **single** workspace `default_model` via full-doc PUT; second provider overwrites first; `ProviderKeySummary` has no model field                | `data/providerKeys.ts:279-303`; contract `providerKeys.ts:41`                                                                                 |
| **Custom OpenAI-compatible endpoint** | **Label only** — "Any OpenAI-compatible endpoint works too" opens modal for first addable known provider; no base-URL entry; Groq/xAI `comingSoon+disabled`                                       | `data/providerKeys.ts:105-123` (`comingSoon`)                                                                                                 |
| OpenRouter model list                 | **Degraded** — validate returns `valid:true` but **empty** `models`; modal falls back to hardcoded catalog                                                                                        | `live_validator.py` (openrouter probes `/api/v1/key`); `data/providerKeys.ts:88-96`                                                           |
| Brand logos                           | **Missing** — connected/empty rows use a first-letter glyph, not colored SVGs                                                                                                                     | `ProviderKeysPage.tsx` `logoGlyphStyle`                                                                                                       |
| Keychain copy                         | **Reworded, not reconciled** — code says "Keys are encrypted at rest in your local vault and never sent to a 0xCopilot server."; design says "macOS Keychain"                                     | `ProviderKeysPage.tsx:54` (`PROVIDER_KEYS_KEYCHAIN_NOTE`) vs design copy                                                                      |
| Legacy web section                    | **Dead code still shipped** — `sections/ProviderKeys.tsx` + `api/providerKeysApi.ts` (list/put/delete, no validate) via `SettingsScreen`                                                          | `apps/frontend/src/features/settings/sections/ProviderKeys.tsx`, `apps/frontend/src/api/providerKeysApi.ts`                                   |

---

## 6. Design & UX specification

The section renders the v2 "quiet" system, resolving all color/spacing/type to `packages/design-system/src/styles.css` tokens — **no hard-coded hex, no px font-sizes in section code**. One accent (sky `#5fb2ec`); jade (`#57c785`) only for the live/success default-model chip; ember (`#f0764f`) only for **Remove**; amber only for the offline/skipped-check warning.

### 6.1 Section header

- **Title:** "Provider keys"
- **Description:** "Bring your own key. 0xCopilot talks to each provider directly — the key stays on your machine."

### 6.2 Card — "Connected" (`SetCard`)

- Header: label **"Connected"**, meta **"{n} active"** (`n` = number of stored keys from `list()`).
- **Keychain note** (`SetNote`): copy is **substrate-aware** (see §6.7 + FR-9). Web/team default and desktop non-opt-in default: _"Keys are encrypted at rest in your local vault and never sent to a 0xCopilot server."_ Desktop when the OS-keychain secret gate is enabled: _"Your vault key is held in your macOS Keychain; provider keys are encrypted at rest and never sent to a 0xCopilot server."_
- Each connected row (`Krow`), one per `ProviderKeySummary`:
  - **Logo** — colored brand mark (FR-8), 20px, radius `--radius-6`.
  - **Name** (e.g. "Anthropic") + a **success chip** (jade) showing the row's **default model** (e.g. "Claude Sonnet 4.5"). Chip renders only when a default model is known for that provider (from `modelChips`/`default_model`); otherwise the row shows no chip (empty state, not "—").
  - **Sub:** masked hint in JetBrains Mono — `"{prefix}•••• {key_hint}"` (e.g. `sk-ant-•••• 4f2a`). The hint value is the last-4 `key_hint`; the leading prefix is cosmetic from the catalog entry.
  - **Actions (ghost buttons):** **Rotate** (reopens the Add modal at step 1 for that provider), **Remove** (trash icon, ember; opens confirm — see 6.5).
- **Loading:** skeleton rows while `list()` is in flight (role `status`, `aria-busy="true"`).
- **Empty:** when `n === 0`, the Connected card is hidden (or shows a one-line "No keys yet — add one below"); the "Add a provider" card carries the flow.

### 6.3 Card — "Add a provider" (`SetCard`)

- One row per **not-yet-connected** provider from `PROVIDER_CATALOG` where `contractBacked === true`: logo, name, sub **"Not connected"**, primary **"Add key"** button.
- `comingSoon` providers (Groq, xAI) render **disabled** with a muted **"Coming soon"** affordance — the CTA never fires a request that would 422.
- Generic `Frow` at the bottom: label **"Another provider"**, hint **"Any OpenAI-compatible endpoint works too."**, primary **"Add a key"** (key icon). Behavior governed by §11 Decision D1.

### 6.4 AddKeyModal — 3-step flow (`AddProviderKeyModal`, `StepDots total=3`)

- **Step 0 (only when no provider preset):** "Choose a provider" — list of addable providers; picking one advances to step 1 with `{provider}` bound.
- **Step 1 — enter key:** mono input placeholder from catalog (`sk-…`, `sk-ant-…`, `AIza…`, `sk-or-v1-…`). Hint: _"Paste a key from your {provider} dashboard. It's sent only to {provider} to verify, then stored in your local vault."_ (design says "macOS Keychain" — reconciled per §6.7). CTA **"Validate key"** (disabled until non-empty; client-side `checkProviderKeyFormat` gates obvious format errors with a `role="alert"` message).
- **Step 2 — validating:** spinner + **"Validating with {provider}…"**. Calls `port.validate(provider, apiKey)`:
  - `valid: true` → advance to step 3, offering **real** `models` from the probe.
  - `valid: false` (`invalid_key`) → bounce to step 1 with a `role="alert"` error ("That key was rejected by the provider — check you pasted the whole value.").
  - `valid: null` (`provider_unreachable`) → **advance** to step 3 (offline-friendly), falling back to catalog models, with an amber inline note ("Couldn't reach {provider} — you can still save; we'll verify on first use.").
- **Step 3 — verified + pick default:** header **"Key verified · sk-••••"**; sub **"Set the default model for this provider"**; a **radio list** of models (real from probe, else catalog fallback; OpenRouter always falls back — see FR-6). CTA **"Add key"** → `port.save(provider, apiKey)` then `port.saveDefaultModel(provider, chosenModel)`. On success: close modal, refresh list, Toast (see 6.6), row appears/updates in Connected with the chosen default chip.
- **Modal a11y:** `role="dialog"`, `aria-modal="true"`, labelled by the step heading; focus trapped; initial focus on the key input (step 1) or first radio (step 3); Esc closes (and, per security, clears the in-memory key immediately — FR-10). `StepDots` are decorative (`aria-hidden`) with an SR-only "Step k of 3".

### 6.5 Remove confirm

- Clicking **Remove** opens a confirm (`role="alertdialog"`): title "Remove {provider} key?", body "0xCopilot will stop using this key. You can add it again anytime." Destructive CTA **"Remove"** (ember), secondary "Cancel". On confirm → `port.remove(provider)` → row leaves Connected → Toast "Removed {provider} key." Idempotent (204).

### 6.6 Immediate-action toasts vs SaveBar

- This section uses **one-shot Toasts** for all mutations (add / rotate / remove / default-model), because each is an immediate, atomic action — **not** a dirty multi-field edit. There is **no** docked SaveBar here. Toast copy: "Added {provider} key.", "Rotated {provider} key.", "Removed {provider} key.", plus the offline variant "Stored {provider} key — not verified (offline)."

### 6.7 Keychain copy reconciliation (design ⇄ truth)

The design's card note and step-1 hint say "macOS Keychain". In reality keys live **TokenVault-encrypted in the local DB**; the OS keychain only optionally gates the vault secret on desktop when the user opts in. The section must **not** state a false storage location. Resolution: copy is host-supplied (a `keychainNote`/`storageNote` prop) so each substrate tells the truth (FR-9, Decision D3). The exported `PROVIDER_KEYS_KEYCHAIN_NOTE` default stays the vault wording; desktop may override to the Keychain-gated wording only when the opt-in is active.

### 6.8 Enumerated parity DELTAS to fix

- **Δ1 (cross-host, P0):** desktop must pass `modelChips` so default chips persist across reload → parity with web.
- **Δ2 (data, P0):** per-provider default model must persist independently (no overwrite) → requires a per-provider store + a summary field (FR-3/FR-4).
- **Δ3 (visual, P1):** replace first-letter glyph with real colored brand logos (Anthropic/OpenAI/Google/OpenRouter; placeholder mark for custom).
- **Δ4 (label honesty, P1):** the "Another provider / OpenAI-compatible" affordance must match its actual capability (Decision D1).
- **Δ5 (copy, P1):** keychain note must be substrate-truthful (FR-9).
- **Δ6 (degraded, P2):** OpenRouter empty-models fallback must be explicit and labeled, not silent.
- **Δ7 (cleanup, P1):** retire the legacy web section so only the SSOT page renders `provider-keys`.

### 6.9 Accessibility & motion

- All cards use `role="group"` with an accessible name from the header. Rows are a list (`role="list"`/`listitem`). Buttons have discernible names ("Rotate Anthropic key", "Remove Anthropic key"). Errors use `role="alert"`; the validating spinner uses `role="status"`. Respect `prefers-reduced-motion` for the step transitions and spinner (swap animation for a static "Validating…" state). Contrast meets WCAG AA against the near-black surface; the jade default chip must not be the sole signal of the default (the model name text carries it).

---

## 7. Functional requirements

**FR-1 — Desktop `modelChips` parity (Δ1).** `apps/desktop/renderer/SettingsMount.tsx` MUST pass a `modelChips` prop to `ProviderKeysPage`, seeded from the desktop's workspace-defaults source (the same projection web uses), so a connected row's default-model chip survives reload. _(Layer: desktop host binder only; SSOT page unchanged.)_ When FR-3/FR-4 land, both binders seed `modelChips` from the per-provider defaults instead (see FR-5).

**FR-2 — Cross-host lockstep on props.** Both binders MUST mount `ProviderKeysPage` with the identical prop set (`port`, `onToast`, `modelChips`, and — per Decisions — `storageNote`/custom-endpoint handlers). A test MUST assert the desktop and web mounts pass equivalent props. _(Layer: both host binders; a lockstep test in each app.)_

**FR-3 — Per-provider default model: contract.** `packages/api-types/src/providerKeys.ts` MUST add an optional `default_model?: string | null` to `ProviderKeySummary` (non-breaking additive field), returned by `GET` list and `PUT`. _(Layer: api-types.)_

**FR-4 — Per-provider default model: store + routes.** The backend MUST persist a per-provider default model alongside each key (a nullable `default_model` column on `provider_api_keys`, set via a new field on the `PUT` body or a dedicated `PUT {provider}/default-model`, and returned in the summary). `saveDefaultModel` in `createProviderKeysPort` MUST target this per-provider endpoint instead of the workspace full-doc PUT, so setting a default for one provider NEVER clobbers another's. _(Layer: `store.py` + migration, `routes.py`, `service.py`, facade proxy, api-types, `data/providerKeys.ts`.)_ See Decision D2 for the endpoint shape.

**FR-5 — Both hosts seed `modelChips` from per-provider defaults.** Once FR-3/FR-4 land, both `SettingsBinder.tsx` and `SettingsMount.tsx` MUST derive `modelChips` from each summary's `default_model` (keyed by provider slug, with the `google`⇄`gemini` normalization preserved), retiring the single-`default_model` workspace read for chip seeding. _(Layer: both host binders; `data/providerKeys.ts` port default.)_

**FR-6 — OpenRouter model fallback is explicit (Δ6).** When `validate` returns `valid: true` with empty `models` (OpenRouter today), step 3 MUST render the catalog model list AND show a muted note ("Model list unavailable from OpenRouter — showing common routes."). The chosen value still persists via FR-4. _(Layer: `AddProviderKeyModal`, `data/providerKeys.ts`.)_

**FR-7 — Rotate uses the existing PUT (no new endpoint).** **Rotate** MUST reopen the Add modal at step 1 for the target provider and call the existing `PUT {provider}` on save; the UI MUST NOT assume a separate rotate route. Rotate MUST NOT reset the provider's `default_model`. _(Layer: SSOT page/modal; backend unchanged.)_

**FR-8 — Real brand logos (Δ3).** The SSOT page MUST render colored brand marks for `anthropic`/`openai`/`google`/`openrouter` (inline SVG components in chat-surface; no external asset fetch — the package is substrate-agnostic and must not `fetch`). A neutral placeholder mark is used for `comingSoon`/custom entries. Logos MUST use tokenized sizing/radius. _(Layer: `ProviderKeysPage.tsx` + a small `providerLogos.tsx` in chat-surface settings.)_

**FR-9 — Substrate-truthful storage copy (Δ5).** The keychain/storage note MUST be a host-supplied prop (default = the vault wording exported as `PROVIDER_KEYS_KEYCHAIN_NOTE`). Desktop MAY pass the macOS-Keychain wording ONLY when the OS-keychain vault-secret opt-in is active (queried via `window.bridge.ipc` in the desktop host, never in the package). The step-1 modal hint MUST use the same substrate-truthful phrase. _(Layer: SSOT page prop; both host binders; desktop native seam.)_

**FR-10 — Plaintext-once in the client.** The plaintext key MUST exist only transiently in the modal's local state, travel exactly once through `port.save`/`port.validate` request bodies, and be cleared on modal close/step-back/success/Esc. It MUST NOT be written to any `KeyValueStore`, `SecretStorage`, log, telemetry event, or URL. Reads render only `key_hint`. There MUST be no "reveal" affordance. _(Layer: SSOT modal; enforced by test.)_

**FR-11 — Validate never stores; save is the only write.** The section MUST call `POST {provider}/validate` for the live probe (which does not store) and `PUT {provider}` exactly once on "Add key". No code path may persist a key during validation. _(Layer: SSOT modal, `data/providerKeys.ts`; backend already enforces.)_

**FR-12 — Retire legacy web section (Δ7).** `apps/frontend/src/features/settings/sections/ProviderKeys.tsx` and `apps/frontend/src/api/providerKeysApi.ts` MUST be removed and `SettingsScreen` updated so `provider-keys` renders only the SSOT `ProviderKeysPage` via `SettingsBinder`. No remaining import of the legacy files may exist. _(Layer: web app only.)_

**FR-13 — `comingSoon` never dead-ends.** Groq/xAI (and any `contractBacked:false`) rows MUST render disabled with a "Coming soon" affordance and MUST NOT issue a `save` that would 422. _(Layer: SSOT page; already partially present — pin with a test.)_

**FR-14 — Custom OpenAI-compatible affordance (Δ4).** The "Another provider" `Frow` MUST behave per Decision D1: either open a real custom-endpoint add flow (base URL + key + optional model) or render an honest "not yet available" state. It MUST NOT silently open the first known provider's modal. _(Layer: SSOT page/modal; if real feature, api-types + backend + facade.)_

**FR-15 — Offline add surfaces `live_check`.** When `PUT` returns `live_check: "skipped_unreachable"`, the success Toast MUST use the offline variant ("Stored {provider} key — not verified (offline)."). _(Layer: SSOT modal reads `PutProviderKeyResponse.live_check`; both hosts.)_

---

## 8. Non-functional requirements

**NFR-1 — Security / plaintext-once.** Plaintext travels exactly once (PUT/validate body), is encrypted at rest via `TokenVault` (managed adapter required in prod; `MCP_TOKEN_VAULT_SECRET ≥ 32`), never logged/audited/echoed, and reads return only `key_hint`. No client persistence of plaintext. No reveal field. (Ref: `providerKeys.ts` header invariant; `service.py`.)

**NFR-2 — Architectural boundaries.** chat-surface stays substrate-agnostic: no `window`/`document`/`fetch`/`localStorage`, no `apps/*` imports; brand logos are inline SVG (no asset fetch); all persistence flows through the injected `ProviderKeysPort`. Apps call the **facade only** (`:8200`); the desktop keychain-opt-in query goes through `window.bridge.ipc` **in the host**, never the package.

**NFR-3 — SSOT / cross-host lockstep.** Exactly one implementation of `provider-keys` (the chat-surface page). The two binders cannot share code (`apps/*→apps/*` banned); a prop-parity test in each app enforces lockstep. Any prop change updates BOTH binders in the same PR.

**NFR-4 — Token discipline / i18n.** No hard-coded hex or px font-sizes in section code — resolve to `design-system` tokens (this PRD's stance on the token-name parity question: use the **existing authored `--color-*`/`--font-*`/`--radius-*` names**; do NOT introduce terse `--ink`/`--sky`/`--r` aliases here — that alias decision is cross-cutting and owned by the program overview). All user-facing strings are centralized as constants for future i18n.

**NFR-5 — Performance.** `list()` renders skeletons within one frame; the section makes at most one `GET` on mount; validate is a single outbound probe; save is a single `PUT`. No N+1 per-provider requests on load.

**NFR-6 — Accessibility.** Meets §6.9: dialog semantics, focus trap/restore, `role="alert"`/`status`, reduced-motion, AA contrast, non-color-only default signal.

**NFR-7 — Telemetry/audit.** Client telemetry (if any) records provider slug + action outcome only — NEVER key material or `key_hint`. Backend already writes atomic identity-audit rows on set/delete; the new per-provider `default_model` write (FR-4) MUST also be audited (non-secret: provider + model name).

**NFR-8 — Graceful degradation.** Validate failure modes: `invalid_key` fails **closed** (block step 3); `provider_unreachable` fails **open** (allow save, mark unverified) — matching the existing tri-state contract. Missing `saveDefaultModel` on the port degrades to a view-only chip (existing behavior) rather than erroring.

---

## 9. Backend wiring & services required

Precise endpoints (method · path · layer). **EXISTING** unless marked NEW.

| Method · Path                                                                                                                                                      | Layer                            | Status   | Contract (api-types)                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------- | -------- | -------------------------------------------------------------------------------------- |
| `GET /v1/settings/provider-keys`                                                                                                                                   | facade → backend `routes.py:149` | EXISTING | `ListProviderKeysResponse` (+ `default_model` per FR-3)                                |
| `PUT /v1/settings/provider-keys/{provider}` (add **and** rotate; `ON CONFLICT` upsert preserving `created_at`)                                                     | facade → backend `routes.py:166` | EXISTING | `PutProviderKeyRequest` → `PutProviderKeyResponse` (`+ live_check`, `+ default_model`) |
| `POST /v1/settings/provider-keys/{provider}/validate` (live tri-state probe; does not store)                                                                       | facade → backend `routes.py:209` | EXISTING | `ValidateProviderKeyRequest` → `ValidateProviderKeyResponse`                           |
| `DELETE /v1/settings/provider-keys/{provider}` (204, idempotent)                                                                                                   | facade → backend `routes.py:247` | EXISTING | —                                                                                      |
| **Per-provider default model** — `PUT /v1/settings/provider-keys/{provider}/default-model` **(recommended, D2)** OR `default_model` field on the existing PUT body | facade → backend                 | **NEW**  | `SetProviderDefaultModelRequest{ model_name }` → `ProviderKeySummary`                  |

**Stores / migrations.**

- `provider_api_keys` (backend): add a **nullable `default_model TEXT`** column — **NEW migration** (next in sequence after `0036_*`). The provider CHECK constraint stays as-is (migrations `0034`/`0036`); **do not** widen it here (Groq/xAI remain out).
- The single workspace `default_model` in `ai-backend` `workspace_defaults` continues to select the _global_ run default; the per-provider `default_model` on the key is what seeds the row chip. Reconcile in Decision D2 (which one wins at run time).

**Enforcement points.**

- Facade derives `(org_id, user_id)` from the verified session — no identity params on the wire (`settings_routes.py`).
- Backend `service.py` encrypts on write; `store.py` returns only `key_hint`. The new `default_model` write is non-secret and additionally audited (NFR-7).
- RBAC scope `RUNTIME_USE`, scoped per `(org_id, user_id)`.

**Enabling note (out of scope, for the custom-endpoint decision).** Adding any new _provider_ still requires widening **four** places together: `ProviderName` StrEnum (`store.py:32`), the `provider_api_keys` CHECK migration, `live_validator` endpoints/auth-headers, and the api-types `ProviderKeyProvider` union (+ service `_KNOWN_PREFIXES`). A generic "custom OpenAI-compatible endpoint" (D1 option B) instead needs a distinct storage shape (slug + user-supplied base URL), not an enum widening.

---

## 10. Acceptance criteria

**Visual parity (both hosts)**

- [ ] On web AND desktop, the section renders the exact cards/rows/modal/copy in §6 with tokenized styles (no hard-coded hex/px).
- [ ] Connected rows show real colored brand logos (not first-letter glyphs) for the four backed providers; Groq/xAI show disabled "Coming soon".
- [ ] The keychain/storage note matches the actual storage location on each substrate.

**Cross-host equivalence**

- [ ] Desktop passes `modelChips`; the default-model chip persists across reload on desktop, identical to web.
- [ ] A prop-parity test in each app asserts web and desktop mount `ProviderKeysPage` with equivalent props.

**Persistence round-trips**

- [ ] Add Anthropic default = model A, add OpenAI default = model B; reload → Anthropic chip still A, OpenAI chip still B (no overwrite) on both hosts.
- [ ] Rotate updates hint/`updated_at`, preserves `created_at` and `default_model`.
- [ ] Delete removes the row; a second delete returns 204 without error.
- [ ] Offline add (`valid:null`) stores the key, shows the unverified Toast, and reflects `live_check:"skipped_unreachable"`.

**Security invariants**

- [ ] No response, log, telemetry event, `KeyValueStore`, or `SecretStorage` ever contains plaintext; reads carry only `key_hint`.
- [ ] Plaintext is cleared from modal state on close/back/success/Esc.
- [ ] No "reveal key" affordance anywhere.

**Cleanup**

- [ ] `sections/ProviderKeys.tsx` and `api/providerKeysApi.ts` are deleted; no imports remain; `provider-keys` renders only the SSOT page.

**Tests required**

- [ ] chat-surface unit: modal 3-step flow incl. tri-state validate branches, `comingSoon` disabled, plaintext-clear-on-close, OpenRouter fallback note.
- [ ] chat-surface unit: per-provider `modelChips` seeding + `google`⇄`gemini` normalization.
- [ ] backend: per-provider `default_model` migration + round-trip + audit row; independence across two providers.
- [ ] api-types typecheck green with additive `default_model`.
- [ ] web + desktop binder prop-lockstep tests.

---

## 11. Open decisions

**D1 — "Another provider / OpenAI-compatible endpoint": real feature or honest label?**

- _Option A — honest label (small):_ keep the row but make the CTA open a state that says "Custom OpenAI-compatible endpoints are coming soon" (or link to docs); never opens a known provider. No backend work.
- _Option B — real custom endpoint (large):_ a distinct add flow capturing `{ base_url, api_key, optional model }`, stored as a generic OpenAI-compatible provider (a new storage shape keyed by user-supplied slug + base URL, NOT an enum widening), routed through the OpenAI-compatible client.
- **Recommended: A for this PRD, B tracked as a follow-up.** Rationale: the plaintext-once + org-scoping + validation story for arbitrary user-supplied base URLs (SSRF surface, per-endpoint validation) is non-trivial and deserves its own PRD. Shipping A removes the current dishonest dead-end immediately; B is the right eventual answer but must not block the convergence.

**D2 — Per-provider default model: new sub-route vs field on PUT; and run-time precedence.**

- _Option A:_ dedicated `PUT {provider}/default-model` — keeps "set default" independent of "rotate key" (rotate need not resend the model).
- _Option B:_ add `default_model` to the existing `PutProviderKeyRequest` — fewer endpoints, but couples rotate and default-set.
- **Recommended: A.** Independent lifecycles (FR-7 says rotate must not reset the default) map cleanly to a dedicated route. **Run-time precedence:** the workspace-level `default_model` (in `ai-backend` `workspace_defaults`) remains the _global_ run default; the per-provider `default_model` seeds the row chip and is used when a run explicitly targets that provider. Document this so the two defaults don't appear to conflict.

**D3 — Keychain copy wording.**

- _Option A:_ single truthful vault wording everywhere (drop "macOS Keychain" entirely).
- _Option B:_ substrate-aware — desktop shows Keychain wording only when the OS-keychain opt-in gates the vault secret; otherwise vault wording.
- **Recommended: B (FR-9).** It is the only option that is true on both substrates and honors the design's intent where the Keychain is actually engaged, without asserting a false storage location on web or non-opted-in desktop.

**D4 — Token-name parity (cross-cutting).** This PRD's stance: use existing `--color-*`/`--font-*`/`--radius-*` tokens; do **not** add terse `--ink`/`--sky`/`--r` aliases in this section. Final resolution is owned by the program overview; flagged here for consistency only.

---

## 12. Rollout & sequencing

Small, independently shippable slices (each green on typecheck + unit tests; no slice regresses security invariants):

1. **PR-1 — Desktop chip parity (Δ1, P0).** Pass `modelChips` in `SettingsMount.tsx`, seeded from the desktop workspace-defaults source (mirrors web). Add the prop-lockstep test in both apps. _Ships the visible cross-host bug fix with zero backend change._ Risk: low.
2. **PR-2 — Retire legacy web section (Δ7, P1).** Delete `sections/ProviderKeys.tsx` + `providerKeysApi.ts`, route `provider-keys` through `SettingsBinder` only. Risk: low (grep for imports; the SSOT page already renders on web).
3. **PR-3 — Brand logos (Δ3, P1).** Inline-SVG `providerLogos.tsx` in chat-surface; swap glyph → logo; placeholder for custom/comingSoon. Risk: low (presentational).
4. **PR-4 — Keychain copy reconciliation (Δ5, P1).** `storageNote` prop + desktop opt-in query via `window.bridge.ipc`; update step-1 hint. Risk: low; touches native seam in the host only.
5. **PR-5 — Per-provider default model (Δ2, P0, backend).** Migration (`default_model` column) → backend route (D2-A) → facade proxy → api-types additive field → `saveDefaultModel` retargeted → both binders seed `modelChips` from summaries → audit row. Risk: medium (migration + multi-layer); ship behind the additive-field contract so old clients still work.
6. **PR-6 — OpenRouter fallback note + offline Toast (Δ6/FR-15, P2).** Explicit "showing common routes" note + `live_check` offline Toast variant. Risk: low.
7. **PR-7 — Custom-endpoint honest label (D1-A, Δ4, P1).** Replace the misleading `Frow` behavior. Risk: low. (Option B tracked as a separate future PRD.)

**Test strategy.** Unit-first in chat-surface (mock port covers all tri-state + step branches + plaintext-clear + comingSoon + OpenRouter fallback); backend pytest for the migration + per-provider independence + audit + upsert-preserves-`created_at`; api-types typecheck; per-app binder prop-lockstep tests; a security assertion test that no code path logs/persists plaintext or emits a reveal field.

**Risk notes.** (a) PR-5's migration is the only non-additive-feeling change — keep the column nullable and the api-types field optional so rollout order (backend-before-client) is safe. (b) The two binders drift silently unless the prop-lockstep tests exist (this is exactly how Δ1 shipped) — land those tests in PR-1 before anything else. (c) Do not widen the provider CHECK or `ProviderName` enum in any of these slices; Groq/xAI/custom stay out until their own 4-place change (§9).
