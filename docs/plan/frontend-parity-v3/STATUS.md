# Frontend Parity v3 — implementation status

**Branch:** `claude/frontend-ui-ux-audit-266ee0` (kept in sync with `main`).
Last updated: after Wave 2 + projects — all 8 PRDs shipped (see rows). Every commit below is green (typecheck + `vitest`)
and pushed. This file is the hand-off state for any agent continuing the work.

## Legend

✅ shipped · 🟡 partial (slice shipped, rest specced) · ⬜ not started

| PRD                                      | Status | Shipped                                                                                                                                                                                                                                                                                                                                | Remaining                                                                                                                                                               |
| ---------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A — Shared icon system**               | ✅     | `chat-surface/src/icons/{Icon.tsx,paths.tsx}` — one `<Icon name/>` + `ICON_PATHS` (v3 glyphs, stroke 1.7). Barrel-exported. 44 tests. Rail slugs added.                                                                                                                                                                                | —                                                                                                                                                                       |
| **B — Tokens & status-tone**             | ✅     | `--color-surface-elevated/-border-stronger/-text-strong` (dark/light/slate). `runStatusTone()` SSOT (`shell/statusTone.ts`) — done→success, stopped→muted, dot on live only; Activity + Chats delegate. `StatusPill` `showDot`. Palette selected-row token fix. Dense `.ui-button--sm` / `.ui-badge`. Terracotta fallbacks purged.     | —                                                                                                                                                                       |
| **C — App rail**                         | ✅     | `AppRail` design icons + tokens + gutter active-bar + avatar initial + Run-badge props; `ChatShell` forwards `railIdentity`/`railBadges`; **web now feeds `railIdentity` from the profile** (avatar shows the user's initial).                                                                                                         | Run **badge** count needs a run-list `activeRunCount` source — small follow-up.                                                                                         |
| **D — ⌘K palette**                       | ✅     | `SHELL_COMMANDS` (13 design commands) launcher: empty-query default + merged above search; design chrome (scrim/blur, 540px, search icon, "No matches.", mono keyword, trigger "Search & commands"); `onCommand` wired in web `App.tsx` + desktop `PaletteHost`. Backend search index untouched. Tests updated + `shellCommands.test`. | Desktop palette commands now NAVIGATE (design) rather than firing the old modal flows — intended, but revisit if the direct-launch UX is wanted (needs richer intents). |
| **E — Settings convergence + nav icons** | ✅     | PR-E.1 desktop nav icons/chrome; **PR-E.2 web `SettingsBinder` mounts the SSOT `SettingsSurface`+`settingsNav`** (renderNavIcon, every slug mapped, invariant test).                                                                                                                                                                   | PR-E.3 delete legacy `SettingsScreen` — kept for connectors/skills (no SSOT slot); follow-up: move them to rail destinations.                                           |
| **F — Provider keys**                    | ✅     | PR-F.2 fidelity; PR-F.1 web mounts redesigned `ProviderKeysPage`; **PR-F.3 live `validate` port** (real probe + real models, 'Validate key'); PR-F.4 groq/xai `comingSoon` (no 422).                                                                                                                                                   | PR-F.5 backend `default_model` projection (host-merge `modelChips` used instead) — optional follow-up.                                                                  |
| **G — Destination parity**               | ✅     | G.1/G.2/G.3 primitives + Activity/Chats; **G.4 Projects `.grid3` card grid** (colour tile + first letter + 'N chats · M files'); **G.5 detail `.sect-h` sections** (Chats·N / Files·M, no tabs for solo; team tabs profile-gated); **G.6 `ItemLink` project-name resolver** via `projectNameCache`.                                    | —                                                                                                                                                                       |
| **H — Backend data-plane**               | ✅     | H.4 conversation pinned/preview/model + `/pin`; **H.1 `_projects-stub`→`api-types` rewire** (both stubs deleted); **H.2 `GET /v1/projects/stream` SSE** (tenant-scoped, facade proxy — fixes the reconnect loop); **H.3 `PostgresProjectsStore`** wired for desktop.                                                                   | rail `activeRunCount` feed (C badge); live-Postgres SQL verification + audit-chain signing + RLS stamping for the PG adapter (no live DB in-session).                   |

## Commit trail (this branch, after the last `main` merge)

- `docs+feat … PRD suite (A–H) + shared Icon system (PRD-A)`
- `feat(design-system,chat-surface): tokens + status-tone SSOT (PRD-B)`
- `feat(chat-surface): extend Icon set with rail destination glyphs (PRD-C)`
- `feat(chat-surface): app rail parity … (PRD-C)`
- `feat(chat-surface): ⌘K static command launcher + design chrome (PRD-D)`
- `feat(settings): nav icons + chrome parity (PRD-E, PR-E.1)`

## Conventions for continuing

- Full PRDs are the spec: `docs/plan/frontend-parity-v3/PRD-{A..H}.md` (each has
  FR/NFR, affected files with `file:line`, PR breakdown, tests, DoD).
- Foundations A + B are the substrate — build surfaces on `<Icon>` + `runStatusTone`
  - the new tokens, never re-inline SVGs or hard-code status colours.
- Worktree note: `apps/*` resolve `@0x-copilot/chat-surface` from the **main**
  checkout's `node_modules`, so a local `apps/frontend` typecheck won't see new
  chat-surface exports. Verify app-side types with a temp `tsconfig` whose `paths`
  point `@0x-copilot/*` at `../../packages/*/src` (see PRD work log), or rely on CI.
- The prettier pre-commit hook reformats then aborts the first commit; stage +
  commit twice with the same message.
