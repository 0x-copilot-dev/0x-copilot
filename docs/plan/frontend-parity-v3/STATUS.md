# Frontend Parity v3 — implementation status

**Branch:** `claude/frontend-ui-ux-audit-266ee0` (kept in sync with `main`).
Last updated: after Wave 1 (G.1-G.3 + F.2 + H.4). Every commit below is green (typecheck + `vitest`)
and pushed. This file is the hand-off state for any agent continuing the work.

## Legend

✅ shipped · 🟡 partial (slice shipped, rest specced) · ⬜ not started

| PRD                                      | Status | Shipped                                                                                                                                                                                                                                                                                                                                                                                              | Remaining                                                                                                                                                                                                                           |
| ---------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A — Shared icon system**               | ✅     | `chat-surface/src/icons/{Icon.tsx,paths.tsx}` — one `<Icon name/>` + `ICON_PATHS` (v3 glyphs, stroke 1.7). Barrel-exported. 44 tests. Rail slugs added.                                                                                                                                                                                                                                              | —                                                                                                                                                                                                                                   |
| **B — Tokens & status-tone**             | ✅     | `--color-surface-elevated/-border-stronger/-text-strong` (dark/light/slate). `runStatusTone()` SSOT (`shell/statusTone.ts`) — done→success, stopped→muted, dot on live only; Activity + Chats delegate. `StatusPill` `showDot`. Palette selected-row token fix. Dense `.ui-button--sm` / `.ui-badge`. Terracotta fallbacks purged.                                                                   | —                                                                                                                                                                                                                                   |
| **C — App rail**                         | ✅     | `AppRail` consumes `Icon` (folder/plug/sparkle/gear fixed); rail bg `--color-bg-elevated`, active/hover `--color-surface-muted`; active-bar gutter geometry; avatar `--color-surface-elevated` + user initial; Run badge; `ChatShell` forwards `railIdentity`/`railBadges`. +6 tests.                                                                                                                | Host DATA wiring for `railIdentity`/`railBadges` (needs PRD-H `activeRunCount` + identity). Props exist; hosts pass nothing yet.                                                                                                    |
| **D — ⌘K palette**                       | ✅     | `SHELL_COMMANDS` (13 design commands) launcher: empty-query default + merged above search; design chrome (scrim/blur, 540px, search icon, "No matches.", mono keyword, trigger "Search & commands"); `onCommand` wired in web `App.tsx` + desktop `PaletteHost`. Backend search index untouched. Tests updated + `shellCommands.test`.                                                               | Desktop palette commands now NAVIGATE (design) rather than firing the old modal flows — intended, but revisit if the direct-launch UX is wanted (needs richer intents).                                                             |
| **E — Settings convergence + nav icons** | 🟡     | **PR-E.1 shipped:** desktop `SettingsMount` feeds `renderNavIcon` (icons now render); nav header ("Settings" + profile hint); active-icon accent + left-bar removed; nav bg `--color-bg-elevated`; item font 12.5px; deduped Models `sliders`→`coin`. +3 tests.                                                                                                                                      | **PR-E.2/E.3 (the big one):** mount `SettingsSurface`+`settingsNav` on WEB via a `SettingsBinder`, retire the legacy `apps/frontend/.../SettingsScreen` (`railSections`/`RailGlyph`). Web still renders the legacy settings screen. |
| **F — Provider keys**                    | 🟡     | **PR-F.2 fidelity shipped** (`parity/g-chatsurface`): Rotate→ghost, Remove→ghost-trash icon, model chip→success tone, per-row "＋ Add key" neutral, primary "🔑 Add a key" CTA restored.                                                                                                                                                                                                             | PR-F.1 web mount (rides E.2); PR-F.3 wire the live `validate` port + `default_model` projection; PR-F.4 reconcile `groq/xai` enum.                                                                                                  |
| **G — Destination parity**               | 🟡     | **G.1/G.2/G.3 shipped** (`parity/g-chatsurface`): shared `Row`/`RowList`/`SectionHeader`/`PageLead` primitives; Activity — one `.rowlist` per day, leading icons, body-font meta, `.pg-lead` (no PageHeader), dot live-only; Chats — leading icons, `.pg-lead`, "New chat" on the Pinned header, "preview · mono model" sub, "Archived · history".                                                   | **G.4/G.5/G.6 (Projects):** `.grid3` card grid on web (blocked by `ItemLink kind="project"` resolver at `refs/index.ts` returning literal "Project"); detail `.sect-h` sections (team tabs profile-gated).                          |
| **H — Backend data-plane**               | 🟡     | **H.4 shipped** (`parity/h-conversations`): conversation `pinned` (first-class + migration 0034) / `preview` / `model` projected; `POST /v1/agent/conversations/{id}/pin` (ai-backend + facade, tenant-scoped, audited); `api-types` superset; frontend `chatsApi` reads projected fields + `pinConversation`. in_memory+file store-conformance tested; Postgres adapter written (live-PG deferred). | Projects `GET /v1/projects/stream` SSE + durable `PostgresProjectsStore` + `_projects-stub`→`api-types` rewire; rail identity + `activeRunCount` feed (→ PRD-C). Provider `validate` already server-side (F wires the port).        |

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
