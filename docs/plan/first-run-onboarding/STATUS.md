# First-Run Onboarding — STATUS

Tracker for the FTUE program. Update on every merged PR. A phase is **done** only when code + host wiring (desktop **and** web) + tests + this file all agree.

## Scope (locked)

Hosted trial: **SHELVED** (deferred; if revived, gated on holding ≥50k $CPILOT — not an open no-key trial) · Safe{Wallet}+Sheets: **BUILD** · Placement: **faithful shared build in `packages/chat-surface`**.

## Phases

| Phase    | Title                                           | State           | PR  | Notes                                                                   |
| -------- | ----------------------------------------------- | --------------- | --- | ----------------------------------------------------------------------- |
| Research | Design import + codebase inventory              | ✅ done         | —   | 4 research sweeps; README §4 inventory                                  |
| P0       | First-run flag + gate seam + skip               | ⬜ todo         | —   | main-process `first-run.json`+IPC; web KV; `bootstrap.tsx` seam         |
| P1       | Gate surface + BYOK card + inline key form      | ⬜ todo         | —   | `/v1/settings/provider-keys`; `FirstRunSurface` scaffold                |
| P2       | Local-model card + Qwen 3 4B preset             | ⬜ todo         | —   | curated preset; `enable_local_models` default decision                  |
| P3       | Onboarding composer + chips + run-create + ack  | ⬜ todo         | —   | reuse `AssistantComposer`; two-step create; handoff                     |
| P4       | Wallet chip + Tools popover + web-search toggle | ⬜ todo         | —   | `/v1/me/profile` chip; per-run web-search flag                          |
| ~~P5~~   | ~~Hosted trial lane~~                           | ⏸ shelved       | —   | dropped from v1; future = ≥50k $CPILOT holder gate (README §7.1)        |
| P6       | Safe{Wallet} + Sheets connectors                | ⬜ todo (gated) | —   | Safe MCP + approval-gated signing; Sheets R/W — needs security sign-off |
| P7       | E2E parity + verification pass                  | ⬜ todo         | —   | live-stack per-journey; ui-design-reviewer vs `design-source/`          |

## Decisions pending (block gated phases)

- [ ] ~~P5~~ (shelved): if revived — $CPILOT threshold (≥50k), on-chain holdings-check + caching, credit source, billing owner.
- [ ] P6-Safe: signing UX, tx simulation, chain/amount guardrails (principle: propose-only agent, human signs, per-call approval).
- [ ] P2: desktop `enable_local_models` default; Qwen 3 4B vs a lighter shipped preset.

## Verify-at-impl

- [ ] CSV attachment accept path (`features/chat/runtime/attachments/file.ts:15`).
- [ ] Finish catalog-driven model picker (`ModelPicker.tsx` hardcodes 3 models) so the gate/model popover is `/v1/agent/models`-driven.
- [ ] Server `truncated_display_address` not exposed as a profile field — chip truncates client-side.
