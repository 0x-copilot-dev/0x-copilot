# Settings Parity & Wiring — status

**Branch:** `claude/0xcopilot-settings-parity-2654f3` (kept in sync with `main`).
**Scope:** the three **Models & keys** settings sections — Provider keys, Local models, Model & behavior — to full v3-design parity + real backend wiring, converged across web & desktop.
**Predecessor:** [frontend-parity-v3](../frontend-parity-v3/README.md) (PRD-E settings convergence, PRD-F provider keys).

## PRDs

| PRD | Section                                                           | Doc                                                            |
| --- | ----------------------------------------------------------------- | -------------------------------------------------------------- |
| 00  | Program overview (architecture, tokens, NFR platform, sequencing) | [PRD-00-overview.md](./PRD-00-overview.md)                     |
| 01  | Provider keys (BYOK)                                              | [PRD-01-provider-keys.md](./PRD-01-provider-keys.md)           |
| 02  | Local models (Ollama)                                             | [PRD-02-local-models.md](./PRD-02-local-models.md)             |
| 03  | Model & behavior                                                  | [PRD-03-model-and-behavior.md](./PRD-03-model-and-behavior.md) |

## Locked decisions (no-bandaid defaults; PRD-00 §7)

| #   | Decision                                     | Locked choice                                                                                                                       |
| --- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| D-1 | Per-provider default model                   | **Real per-provider default** — add `ProviderKeySummary.default_model` + store column; stop clobbering the single workspace default |
| D-2 | Custom OpenAI-compatible endpoint            | **Real add-a-custom-endpoint flow** (base-URL + key) over the existing OpenAI-compatible registry                                   |
| D-3 | "Default local model" home                   | **`workspace_defaults`** (beside `default_model`, the run-resolution source)                                                        |
| D-4 | Available-models catalog                     | **Curated static list** (api-types) + **free-text advanced path** (keep power-user flow)                                            |
| D-5 | Reasoning-depth vocabulary                   | **Canonical `auto/quick/standard/deep`** at UI/settings; map to runtime `fast/balanced/deep`                                        |
| D-6 | Web access                                   | **Real tool-gate** — capability classification + run-context filter (load-time), not UI-only                                        |
| D-7 | Default-model / reasoning / web-access scope | **Org-scoped `workspace_defaults`** for the solo profile (org==user)                                                                |

## Legend

✅ shipped (green: typecheck + tests) · 🟡 in progress · ⬜ not started

## Waves & slices

| Wave / slice | Summary                                                                                                                                                                                                                                      | Status |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| **A1**       | Token/type parity: added `--color-accent-line` + missing `--color-danger-contrast`/`--color-success-contrast` (3 themes) to the SoT; purged 56 stale atlas-orange `var()` fallbacks in settings pages. Global base-size deferred (see note). | ✅     |
| **B1**       | Provider keys: fix desktop `modelChips` divergence                                                                                                                                                                                           | ⬜     |
| **B2**       | Provider keys: true per-provider `default_model` (contract + store + migration)                                                                                                                                                              | ⬜     |
| **B3**       | Provider keys: real brand logos; keychain-copy reconciliation; custom OpenAI-compatible endpoint (D-2)                                                                                                                                       | ⬜     |
| **C1**       | Local models: `LocalModelsPort` + desktop live wiring (status/list/pull-SSE/delete); both hosts mount `LocalModelsPage`                                                                                                                      | ⬜     |
| **C2**       | Local models: default-local-model persistence + chip + set-as-default round-trip                                                                                                                                                             | ⬜     |
| **C3**       | Local models: curated available catalog + free-text path                                                                                                                                                                                     | ⬜     |
| **D1**       | Model & behavior: reasoning-depth vocabulary unification + persisted-default consumption                                                                                                                                                     | ⬜     |
| **D2**       | Model & behavior: approval-policy **runtime enforcement** + desktop→storage wiring                                                                                                                                                           | ⬜     |
| **D3**       | Model & behavior: web-access **real tool-gate**                                                                                                                                                                                              | ⬜     |
| **D4**       | Model & behavior: spend-cap config surface bound to `/v1/budgets` + admin-scope authorization fix                                                                                                                                            | ⬜     |

**Sequencing refinement (principal-eng review).** The original PRD-00 §6 "A2 — big-bang web convergence" was split into the section verticals to avoid a regressed half-wired intermediate: web converges onto `LocalModelsPage` **inside C1** (once the port + real wiring exist) and onto `ModelBehaviorPage` **inside D5** (once D1–D4 wire the knobs, so web never loses its currently-wired `ToolUsePolicyPanel`). Dead provider-keys web code is retired in D5.

**Deferred (out of this program).** Pinning the global base body font-size to the design's ~13px "quiet" size is a genuine, documented drift but is **app-wide** (affects chat, activity, every surface) and **orthogonal to these three settings sections** (which set explicit `--font-size-*` on every text node). It needs cross-surface visual verification and should land as its own verified slice, not a blind global change here.

## Commit trail

- `7f490fac` docs(settings-parity): PRD suite (PRD-00..03 + STATUS)
- **A1** feat(design-system,chat-surface): accent-line + contrast tokens; purge stale atlas-orange fallbacks
