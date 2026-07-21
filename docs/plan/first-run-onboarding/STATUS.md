# First-Run Onboarding — STATUS

Tracker for the FTUE program. Update on every merged PR. A phase is **done** only when code + host wiring (desktop **and** web) + tests + this file all agree.

## Scope (locked)

Hosted trial: **SHELVED** (deferred; if revived, gated on holding ≥50k $CPILOT — not an open no-key trial) · Safe{Wallet}+Sheets: **BUILD** · Placement: **faithful shared build in `packages/chat-surface`**.

## Phases

| Phase    | Title                                           | State           | PR  | Notes                                                                                                                                                                                                            |
| -------- | ----------------------------------------------- | --------------- | --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Research | Design import + codebase inventory              | ✅ done         | —   | 4 research sweeps; README §4 inventory                                                                                                                                                                           |
| P0       | First-run flag + gate seam + skip               | ✅ desktop code | —   | main `first-run.json`+IPC+preload; `FirstRunGate` seam+skip; store test; tsc+lint+166 tests green. Body is a placeholder → real 3-state surface in P1; web-host KV binding lands with the web onboarding feature |
| P1       | Gate surface + BYOK card + inline key form      | ⬜ todo         | —   | `/v1/settings/provider-keys`; `FirstRunSurface` scaffold                                                                                                                                                         |
| P2       | Local-model card + Qwen 3 4B preset             | ⬜ todo         | —   | curated preset; `enable_local_models` default decision                                                                                                                                                           |
| P3       | Onboarding composer + chips + run-create + ack  | ⬜ todo         | —   | reuse `AssistantComposer`; two-step create; handoff                                                                                                                                                              |
| P4       | Wallet chip + Tools popover + web-search toggle | ⬜ todo         | —   | `/v1/me/profile` chip; per-run web-search flag                                                                                                                                                                   |
| ~~P5~~   | ~~Hosted trial lane~~                           | ⏸ shelved       | —   | dropped from v1; future = ≥50k $CPILOT holder gate (README §7.1)                                                                                                                                                 |
| P6       | Safe{Wallet} + Sheets connectors                | ⬜ todo (gated) | —   | Safe MCP + approval-gated signing; Sheets R/W — needs security sign-off                                                                                                                                          |
| P7       | E2E parity + verification pass                  | ⬜ todo         | —   | live-stack per-journey; ui-design-reviewer vs `design-source/`                                                                                                                                                   |

## Decisions pending (block gated phases)

- [ ] ~~P5~~ (shelved): if revived — $CPILOT threshold (≥50k), on-chain holdings-check + caching, credit source, billing owner.
- [ ] P6-Safe: signing UX, tx simulation, chain/amount guardrails (principle: propose-only agent, human signs, per-call approval). **Design-pass security review = needs-changes across all 3 lenses (1 critical, 8 high, 12 medium) — see `phases/security-review-safe-*.md`; resolve before any P6 code.**
- [ ] P2: desktop `enable_local_models` default; Qwen 3 4B vs a lighter shipped preset.

## Verify-at-impl

- [ ] **P3 BLOCKER (not just verify):** CSV attach — the accept list (`features/chat/runtime/attachments/file.ts:15`, office+pdf only) rejects `.csv`; the `airdrop-claims.csv` fixture does not exist. P3 must extend BOTH host adapters (web + desktop `createDesktopAttachmentAdapter`) and author the CSV + its two host resolutions.
- [ ] Finish catalog-driven model picker (`ModelPicker.tsx` hardcodes 3 models) so the gate/model popover is `/v1/agent/models`-driven.
- [ ] Server `truncated_display_address` not exposed as a profile field — chip truncates client-side.

## Design pass (workflow) — results & sequencing

Ran an 8-agent design workflow: one grounded implementation PRD per phase + a 3-lens adversarial Safe-signing security review + a completeness critic. Outputs in [`phases/`](./phases/).

- **PRDs delivered:** P0, P1, P2, P3, P6a, P6b (`phases/PRD-*.md`). **P4 PRD timed out** (broad agent hit the idle-timeout) → re-run narrower before the P4 build.
- **Sequencing (critic):** `P0 → P1 → (P2 ∥ P3) → P4 → (P6a ∥ P6b — serialized on ai-backend runtime files, gated) → P7`. P2's supervisor env edit (`RUNTIME_ENABLE_LOCAL_MODELS` in `service-env.ts`) is a hard pre-req that can land first.
- **Cross-cutting (own once):** one `onboarding.css` (P1 owns) + one token map; one canonical `FirstRunStore` port (P0) that P1–P3 import; one two-step run-create port (P2/P3 share); `SuggestionChips` + `WalletChip` are net-new shared components whose client-side address truncation lives IN the package (hosts can't share code). All onboarding I/O via host-injected ports (chat-surface eslint bans window/fetch/localStorage).
- **P4 is a real prerequisite, bigger than a chip:** the shipped `ToolPicker` has NO connector-install / 1-click / per-chat-scope / web-search-toggle plumbing — that UI is P4, and P6a/P6b's "1-click, zero new UI" claims depend on it.
- **ModelPicker owner:** `ModelPicker.tsx` hardcodes 3 Claude models; assign an owner (P3 / shared pre-req) to make the composer model list `/v1/agent/models`-driven, else the FTUE model popover shows the wrong models.
- **P0 hardening (open):** key the flag by the verified session's `claims.sub` derived in main (via `AuthService` async session load, not a sync cache read) instead of the renderer-supplied `workspaceId`. Current P0 keys by `workspaceId` (correct for single-user; per-account isolation is the hardening) — matches the "caller-supplied identity is untrusted" rule.
- **P6b ordering:** widen backend `Literal['read','draft']` (`desktop_routes.py:91`) to include `write` BEFORE the client sends the `write` scope, else Pydantic 422s.

## Build progress — parallel streams (isolated worktrees → `ftue/*` branches)

| Stream                                     | Branch                  | State                           | Notes                                                                                                                                                                                              |
| ------------------------------------------ | ----------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Composer catalog-driven models             | `ftue/composer-catalog` | ✅ merged                       | ModelPicker/Composer take injected `models`; chat-surface tsc + 140 composer tests green                                                                                                           |
| Backend prereqs (P2-env + web-search flag) | `ftue/backend-prereqs`  | ✅ merged                       | `RUNTIME_ENABLE_LOCAL_MODELS` in supervisor; per-run `web_search_enabled` threaded (ai-backend + facade + api-types + FE); ai-backend 11+401, facade 6, api-types 44, desktop service-env 25 green |
| P1 gate surface                            | `ftue/p1-surface`       | ⏳ running                      | flagship — FirstRunSurface + Gate + KeyForm + onboarding.css + ports + desktop wiring                                                                                                              |
| P3 CSV prereq                              | `ftue/p3-csv-prereq`    | ⏳ running                      | accept-list widening + `airdrop-claims.csv` fixture                                                                                                                                                |
| P4 design PRD                              | —                       | ✅ `phases/PRD-P4.md`           | wallet chip + connector-aware Tools popover + web-search toggle UI                                                                                                                                 |
| P6a hardened design                        | —                       | ✅ `phases/PRD-P6a-hardened.md` | security review resolved: decoded-calldata authority + fail-closed sim + facade bound-Safe enforcement                                                                                             |

Design-pass now complete — all PRDs present (P0–P4, P6a[+hardened], P6b). Reconcile watch: backend-prereqs changed `_validate_capability_mode` to probe with `None` (behavior-adjacent, test-covered) — re-verified on merge.
