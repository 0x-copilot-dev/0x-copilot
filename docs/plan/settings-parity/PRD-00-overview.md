# PRD-00 — Settings Parity & Wiring: Program Overview

**Status:** Draft · **Branch:** `claude/0xcopilot-settings-parity-2654f3` · **Predecessor:** [frontend-parity-v3](../frontend-parity-v3/README.md) (PRD-E settings convergence, PRD-F provider keys)

This program brings the three **Models & keys** settings sections — **Provider keys**, **Local models**, **Model & behavior** — to full parity with the _0xCopilot App v3_ design (Claude Design project `73f810d9`) **and** wires them to real backend services, on **both** the web and desktop surfaces.

The three section PRDs:

- [PRD-01 — Provider keys (BYOK)](./PRD-01-provider-keys.md)
- [PRD-02 — Local models (Ollama)](./PRD-02-local-models.md)
- [PRD-03 — Model & behavior](./PRD-03-model-and-behavior.md)

---

## 1. Problem statement

The branch name says it: **`composer-web-desktop-mismatch`**. The Settings surface is supposed to be a single source of truth in `packages/chat-surface`, mounted identically by web (`apps/frontend`) and desktop (`apps/desktop`). In practice, the two apps have drifted:

| Section              | Web renders                                                                         | Desktop renders                                                           | Consequence                                                              |
| -------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **Provider keys**    | chat-surface `ProviderKeysPage` ✅                                                  | chat-surface `ProviderKeysPage` ✅ (but **omits `modelChips`**)           | Default-model chip persists on web, **vanishes on reload on desktop**    |
| **Local models**     | **legacy** `sections/LocalModels.tsx` (real backend, non-design markup)             | design `LocalModelsPage` **on stubs** (Ollama hardcoded off; pull throws) | Same slug, two different UIs; **the design page has zero live coverage** |
| **Model & behavior** | **legacy** `sections/ModelAndBehavior.tsx` (no default-model / depth / web / spend) | design `ModelBehaviorPage`, **only default-model persists**               | Same slug, two different UIs; four controls are inert                    |

Underneath the UI, backend maturity is wildly uneven — and _"the setting exists" does not mean "the runtime obeys it."_ Some capabilities are fully enforced (spend budgets, default-model resolution, per-run reasoning depth), some are stored-and-surfaced-but-never-consumed (approval policy, four workspace behavior knobs), and some are entirely absent below the toggle (web access).

**The user-visible harm:** a solo desktop user opening Settings today sees a Local models panel that can never load, a Model & behavior panel whose Approval-policy / Web-access / Spend controls silently forget on reload, and a Provider-keys card whose model chip disappears — while the _web_ build of the same product behaves differently again. This is the opposite of a trustworthy local-first control surface.

## 2. What "done" means

1. **Visual parity** — each section matches the v3 design (structure, spacing, type scale, tokens, copy, states, modals) on both hosts.
2. **Cross-host parity** — web and desktop render the _same_ chat-surface SSOT page for all three sections; the legacy web sections are retired.
3. **Real wiring** — every control that appears persists and is honored by the runtime, or is honestly gated/hidden. No inert toggles, no fake success toasts.
4. **Boundaries intact** — chat-surface stays substrate-agnostic; apps call only the facade; design-system `:root` stays the one token SoT; BYOK plaintext travels once.

## 3. Goals & non-goals

**Goals**

- Converge web onto the chat-surface SSOT pages for local-models and model-behavior; retire the legacy sections and dead provider-keys code.
- Wire the real backends (provider keys, local models, model & behavior) through **both** host binders in lockstep.
- Close the genuine backend gaps: default-local-model persistence, approval-policy runtime enforcement, web-access tool-gate, spend-cap config surface.
- Land the token/type parity cleanups the sections depend on.

**Non-goals**

- Redesigning the settings information architecture (nav groups are already the design's solo-desktop layout).
- The non-target sections (Profile, Appearance, Shortcuts, Privacy, Notifications, App-lock, Developer-tokens, team admin) beyond incidental token cleanups.
- Bundling/supervising an Ollama daemon (see PRD-02 for the lifecycle stance).
- Multi-user/team billing surfaces (gated to the `team` profile, out of scope here).

## 4. Cross-cutting decision — token & type parity (applies to all three PRDs)

The v3 design's CSS (`copilot.css`) uses terse token names — `--ink`, `--tx`, `--sky`, `--r`. The repo's design-system uses `--color-bg`, `--color-text`, `--color-accent`, `--radius-md` — **with byte-identical values**. The chat-surface settings pages already resolve to the `--color-*` names via inline styles; they never referenced the design's names.

**Decision:** **Do not** introduce the design's `--ink/--tx/--r` alias names. That would create two vocabularies for one value — a DRY / single-source violation, and `CLAUDE.md` mandates design-system `:root` as the ONE token SoT. Parity is achieved at the **value** level, which is already true. The remaining token work is small and surgical:

- **T-1 — Base type size.** No global token pins the body to the design's ~12.5–13px "quiet" size (`body{}` inherits UA 16px; `--font-size-xs` is 12.5px but nothing applies it globally). Pin the base size through a token (recurrence of the prior "Studio chat 13px" drift). _(design-system + shell)_
- **T-2 — Missing semantic tokens.** Add `--color-accent-line` (accent-tinted hairline; today only an inline `color-mix`) and a semantic `--color-violet` if any section needs the design's violet (currently only a `[data-accent=violet]` swatch). _(design-system)_
- **T-3 — Purge stale fallbacks.** `ProfilePage.tsx` / `NotificationsPage.tsx` embed pre-v2 atlas-orange hex inside `var()` fallbacks (`var(--color-accent, #d97757)`, `var(--color-danger, #d96b6b)`, solid `#232325` borders). These repaint the old look on any token-resolution miss — replace with the correct v2 values or drop them. _(chat-surface)_

Each section PRD assumes T-1..T-3 as a prerequisite (Wave A).

## 5. Convergence architecture (the spine)

```
                   packages/chat-surface  (SSOT, substrate-agnostic — ports only)
    ┌───────────────────────────────────────────────────────────────────────┐
    │ SettingsSurface (shell) · settingsNav.ts (nav SSOT + profile gate)      │
    │ ProviderKeysPage · LocalModelsPage · ModelBehaviorPage · ApprovalPolicy │
    │ ports:  ProviderKeysPort · ModelsPort · [NEW] LocalModelsPort ·         │
    │         [NEW] ModelBehaviorPort                                          │
    └───────────────▲───────────────────────────────────────▲────────────────┘
                    │ inject bodies + bind ports              │  (binders CANNOT share code —
   apps/frontend    │                                         │   apps/*→apps/* is banned;
   SettingsBinder.tsx (web)                    apps/desktop/renderer/SettingsMount.tsx
                    │                                         │   keep in LOCKSTEP)
                    └──────────────►  backend-facade :8200  ◄─┘
                          /v1/settings/provider-keys(+/validate)   → backend
                          /v1/local-models/* (status/list/size/pull-SSE/delete) → ai-backend
                          /v1/agent/workspace/defaults              → ai-backend
                          /v1/me/policies/tool-use                  → backend
                          /v1/budgets(/me)                          → ai-backend
                          /v1/me/preferences                        → backend
```

**Rules the whole program obeys (NFR platform):**

- **NFR-P1 — SSOT & lockstep.** All three sections render the chat-surface page on both hosts. The two host binders are duplicated by necessity; any prop/port change touches **both** `SettingsBinder.tsx` and `SettingsMount.tsx`. An invariant test asserts every nav slug maps to the same page on both hosts.
- **NFR-P2 — Substrate purity.** No `window`/`document`/`fetch`/`localStorage`/`EventSource` and no `apps/*` imports inside chat-surface. All I/O is a transport-backed **port** (for data) or a bare callback prop (for host-native seams). SSE lives in the host binder, never the package.
- **NFR-P3 — Facade only.** Apps never call `:8100`/`:8000`. New settings capabilities add a facade proxy; `ai-backend` never imports `backend` (policy is read only via the `/internal/v1/policies/runtime` aggregate).
- **NFR-P4 — Token discipline.** No hard-coded hex or px font-size in section code; resolve to design-system tokens. Only the design's pinned dims are literals (nav 216px, content 620px, modal 500px).
- **NFR-P5 — Security invariants.** BYOK plaintext travels exactly once in a PUT body, never stored/logged/echoed (masked `key_hint` only). Encryption at rest via `TokenVault` (managed adapter in prod). Any new admin-scoped mutation (e.g. budgets) gets an authorization check.
- **NFR-P6 — Honest state.** A control that cannot persist must not pretend to. No fake success toasts; degrade to real backend state or hide the control behind a capability flag.
- **NFR-P7 — SaveBar vs Toast split.** Dirty multi-field sections (Model & behavior) dock a **SaveBar** via `controller.setDirty`; immediate actions (add/rotate/remove key, download model, set default) fire a one-shot **Toast** via `controller.showToast`.
- **NFR-P8 — Accessibility & motion.** Nav is a `role=tablist` with roving focus; modals trap focus and close on ESC/backdrop; spinners/progress respect `prefers-reduced-motion`; every control is labelled.

## 6. Sequencing — waves & PR slices

Small, independently shippable, each green on typecheck + vitest + the relevant Python suite.

**Wave A — Parity foundation** _(prerequisite; PRD-00)_

- **A1** Token/type cleanup T-1..T-3.
- **A2** Web converges onto the SSOT pages for **local-models** and **model-behavior** (introduce `LocalModelsPort`; mount `LocalModelsPage` + `ModelBehaviorPage` on web); retire legacy `sections/LocalModels.tsx`, `sections/ModelAndBehavior.tsx`, and dead `sections/ProviderKeys.tsx` + `api/providerKeysApi.ts`. Invariant test for slug↔page.

**Wave B — Provider keys** _(PRD-01)_

- **B1** Fix desktop `modelChips` divergence (seed from workspace defaults on desktop).
- **B2** True per-provider default model (`ProviderKeySummary.default_model` field + store) — _[decision D-1]_.
- **B3** Real provider brand logos; reconcile keychain copy with the TokenVault truth; custom OpenAI-compatible endpoint — _[decision D-2]_.

**Wave C — Local models** _(PRD-02)_

- **C1** `LocalModelsPort` + desktop live wiring (status/list/pull-SSE/delete); both hosts mount `LocalModelsPage`.
- **C2** Default-local-model persistence + `default local` chip + set-as-default round-trip — _[decision D-3 home]_.
- **C3** Curated available-models catalog + free-text power path — _[decision D-4]_.

**Wave D — Model & behavior** _(PRD-03)_

- **D1** Reasoning-depth vocabulary unification + persisted-default consumption — _[decision D-5]_.
- **D2** Approval-policy **runtime enforcement** (wire `ToolUsePolicyGate`) + desktop→storage wiring (web===desktop).
- **D3** Web-access **real tool-gate** (new capability classification + run-context plumbing) — _[decision D-6]_.
- **D4** Spend-cap config surface bound to `/v1/budgets` + admin-scope authorization fix.

Waves B/C/D are independent after A and can run in parallel by isolated-worktree agents; A is the shared gate.

## 7. Program-level open decisions (consolidated)

| #       | Decision                                                                  | Recommended default                                                                                                                                                                            |
| ------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **D-1** | Per-provider default model vs single workspace default                    | Add `ProviderKeySummary.default_model` (per-provider) — the design shows a model chip per connected provider; a single workspace default can't represent that.                                 |
| **D-2** | Custom OpenAI-compatible endpoint: real feature or honest label           | Ship a real "Add a custom endpoint" flow (base-URL + key) — the design promises it and the runtime already has an OpenAI-compatible registry; otherwise relabel to remove the promise.         |
| **D-3** | Home for "default local model"                                            | `workspace_defaults` (sits beside `default_model`, ORG-scoped, already the run-resolution source) unless per-user is required, then `/v1/me/preferences`.                                      |
| **D-4** | Available-models catalog: curated static vs backend endpoint vs free-text | Curated static list in api-types (matches the design's pick-from-available) **plus** a free-text "advanced" path (preserves the web power-user flow).                                          |
| **D-5** | Canonical reasoning-depth vocabulary                                      | Standardize on the design's **auto/quick/standard/deep** at the settings/UI layer, map to the runtime's `fast/balanced/deep` per-run set; retire/def-map the unused `low/medium/high` default. |
| **D-6** | Web access: real tool-gate vs UI-only                                     | Real tool-gate — classify web/fetch/search tools with a capability flag and gate them in run context. A UI-only toggle violates NFR-P6.                                                        |
| **D-7** | Scope of default-model / reasoning-default / web-access: per-user vs org  | Keep model/reasoning defaults on org-scoped `workspace_defaults` for the solo profile (org==user); put web-access on the same to avoid a 4th home, unless team semantics demand per-user.      |

Each PRD carries the detailed options + rationale for its own decisions.

## 8. Acceptance (program)

- [ ] All three sections render the chat-surface SSOT page on **both** hosts; legacy web sections deleted; slug↔page invariant test green.
- [ ] Visual parity verified against the v3 design on both hosts (see per-PRD acceptance).
- [ ] Every visible control persists and round-trips, or is capability-gated — no inert controls, no fake toasts.
- [ ] Runtime honors: default model, reasoning depth, web access, approval policy, spend cap (each with a test proving enforcement, not just storage).
- [ ] Security: plaintext-once preserved; budgets admin-scope check added; no new `apps/*→apps/*` or `ai-backend→backend` imports.
- [ ] Typecheck + build green (frontend, api-types, desktop); `vitest` green (chat-surface, design-system); Python suites green (ai-backend, backend, facade).
