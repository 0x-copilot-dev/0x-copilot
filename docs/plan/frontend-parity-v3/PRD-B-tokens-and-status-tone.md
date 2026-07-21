# PRD-B — Tokens & status-tone semantics

**Status:** Draft · **Surface:** all · **Package:** `@0x-copilot/design-system`
(+ `chat-surface` consumers) · **Blocks:** C, D, F, G · **Blocked by:** —

## 1. Context & problem

The token crosswalk is 1:1 (see README), yet colour bugs are pervasive because
components pick the **wrong** token, reference a **non-existent** token, hard-code
a literal, or map status→tone **inconsistently**. Four distinct root causes:

- **P1 — Undefined token silently falls back to a hard-coded grey.** The ⌘K
  selected/hover row uses `var(--color-surface-elevated, #2a2a2a)`; that token is
  **not defined** in `design-system/styles.css`, so every selected palette row is
  `#2a2a2a` (`PaletteHitRow.tsx:65-69`). The design wants `--panel2`
  (`--color-surface-muted`, `#16161a`).
- **P2 — Inverted status→tone mapping, duplicated per surface.** Activity maps
  `done → muted (grey)` and `stopped → danger (red)`
  (`ActivityDestination.tsx:80-93`); Chats maps `done → muted`
  (`ChatsArchive.tsx:67-78`). The design maps `running/done → success (jade)`,
  `paused → warning`, `stopped/archived → muted/off`. Each surface re-implements
  the map, so they disagree.
- **P3 — Hard-coded literals bypass tokens.** The Projects detail status pill
  hard-codes `rgba(34,197,94,.12)`/`rgb(74,222,128)`/`rgb(251,191,36)`/
  `rgb(148,163,184)` (`ProjectDetailView.tsx:348-371`) — none are the design
  tokens. Stale Claude-terracotta `#d97757` fallbacks are scattered across
  `StatusPill.tsx`, `PageHeader.tsx`, `ActivityDestination.tsx`,
  `CommandPalette.tsx` (inert while the token loads, wrong if it ever doesn't).
- **P4 — Button/badge scale heavier than the design.** `.ui-button--sm`
  (`min-height 2rem`/32px, `radius-md`/8px, `padding .35/.7rem`, `font-weight
650`) and `.ui-badge` (`min-height 1.65rem`/26px, weight 650) are taller/heavier
  than the design's `.cbtn--sm` (~20px, `radius-sm`/6px, `padding 4/9px`, weight 500) and `.chip` (`1px 8px`) — the note at `styles.css:359-361` documents 650 as
  intentional brand vocabulary, so this is a **decision**, not a clean bug.

## 2. Goals / Non-goals

**Goals**

- G1 — Add the three missing tokens (`--color-surface-elevated`,
  `--color-border-stronger`, `--color-text-strong`) mapping design `--panel3`,
  `--line3`, `--tx2` — resolving P1 and giving PRD-C/G an honest target for the
  rail avatar (`--panel3`) and strong text.
- G2 — One SSOT `statusTone()` helper in `chat-surface`; Activity, Chats,
  Projects, and any future run-status chip consume it. Delete per-surface maps.
- G3 — Purge hard-coded status literals and stale `#d97757` fallbacks; every
  status colour resolves to a token.
- G4 — Decide + apply the dense button/badge scale for row-embedded controls
  (see decision below).

**Non-goals**

- NG1 — Re-theming: the palette and light/dark values are correct and unchanged.
- NG2 — Changing the accent-rotation system (`[data-accent]`) — out of scope.

## 3. User stories

| ID     | As a…     | I want…                                                  | so that…                                           |
| ------ | --------- | -------------------------------------------------------- | -------------------------------------------------- |
| US-B.1 | Solo user | a completed run's chip to read "done" in green, not grey | status colour matches meaning across the app       |
| US-B.2 | Solo user | ⌘K selected row highlighted in the app's surface colour  | the palette doesn't show a stray light-grey band   |
| US-B.3 | Developer | one `statusTone(status)` helper                          | status colours can't drift between surfaces        |
| US-B.4 | Developer | tokens for every design colour I need                    | I never invent an undefined var or hard-code a hex |

**Acceptance (US-B.1):** _Given_ a conversation with `latest_run_status =
completed`, _when_ its row renders, _then_ the chip uses `--color-success` in both
Activity and Chats (asserted by a shared `statusTone` unit test).

## 4. Functional requirements

- **FR-B.1** — In `design-system/styles.css` `:root` (dark) and the light block,
  add `--color-surface-elevated: #1d1d23;` (design `--panel3`),
  `--color-border-stronger: rgba(255,255,255,.18);` (`--line3`), and
  `--color-text-strong: #d4d4db;` (`--tx2`), with light-theme counterparts. Update
  `.ui-*` classes only where they currently reference a missing value.
- **FR-B.2** — Add `packages/chat-surface/src/shell/statusTone.ts` exporting
  `type RunStatusTone = "success" | "warning" | "muted" | "accent"` and
  `statusTone(status: RunStatus): { tone; label; showDot }` with the design
  mapping: `running → success (showDot)`, `done/completed → success`,
  `paused/waiting_for_approval → warning`, `stopped/cancelled → muted`,
  `archived → muted`, `needs_input → accent`. `showDot` true **only** for live
  (`running`).
- **FR-B.3** — `StatusPill` renders its dot only when `showDot` (from tone data),
  not for every tone (`StatusPill.tsx:76-98`).
- **FR-B.4** — Replace `var(--color-surface-elevated, #2a2a2a)` in
  `PaletteHitRow.tsx:65` with `var(--color-surface-muted)` (FR-D covers the row
  redesign; this is the minimal colour correction if D lands later).
- **FR-B.5** — Replace hard-coded status literals in `ProjectDetailView.tsx:348-371`
  with `statusTone`-driven tokens; remove `#d97757` fallbacks in `StatusPill.tsx`,
  `PageHeader.tsx`, `ActivityDestination.tsx`, `CommandPalette.tsx` (use the bare
  `var(--color-accent)` / correct token with no literal fallback, or a token-only
  fallback).
- **FR-B.6** (decision-gated) — Introduce a **dense** control scale for
  row-embedded buttons/badges matching the design `.cbtn--sm`/`.chip`: either
  (a) retune `.ui-button--sm` to `min-height 1.5rem`, `radius-sm`, `padding
4px 9px`, `font-weight 500` and `.ui-badge` to `padding 1px 8px`,
  `min-height auto`, weight 600; **or** (b) add `.ui-button--dense` /
  `.ui-badge--dense` modifiers if the team wants to preserve 650 for hero CTAs.
  **Recommendation:** (a) — the design's quiet system is consistently dense; the
  650 "press-me" weight is the outlier. PRD carries the rationale so the reviewer
  decides.

## 5. Architecture & system design

- **SSOT.** Colour values: `design-system/styles.css` tokens. Status→tone:
  `chat-surface/src/shell/statusTone.ts` (single map). Control scale:
  `design-system/styles.css` `.ui-*` classes. No component may hard-code a status
  colour or a control dimension.
- **Data flow.** `statusTone(status)` is pure; surfaces pass its `tone` to
  `StatusPill`/badge and its `label` as chip text. `RunStatus` type is the
  api-types run status union (re-exported); the helper is exhaustive (a
  `never`-guarded switch) so a new status forces a mapping decision.
- **Reuse vs new.**
  | | path |
  | --- | --- |
  | New | `chat-surface/src/shell/statusTone.ts` (+ test) |
  | Modify | `design-system/src/styles.css`; `StatusPill.tsx`; `ActivityDestination.tsx`; `ChatsArchive.tsx`; `ProjectDetailView.tsx`; `PageHeader.tsx`; `PaletteHitRow.tsx` |
  | Delete | per-surface `activityStatusTone`/`statusTone` local maps |

## 6. Affected files

- **Create:** `chat-surface/src/shell/statusTone.ts`, `statusTone.test.ts`.
- **Modify:** `design-system/src/styles.css` (tokens + `.ui-button--sm`/`.ui-badge`);
  `chat-surface/src/shell/StatusPill.tsx`, `PageHeader.tsx`, `PaletteHitRow.tsx`;
  `chat-surface/src/destinations/activity/ActivityDestination.tsx`;
  `chat-surface/src/destinations/chats/ChatsArchive.tsx`;
  `chat-surface/src/destinations/projects/ProjectDetailView.tsx`.

## 7. PR / commit breakdown

- **PR-B.1** — Tokens: add the 3 tokens (dark+light) + fix `--color-surface-elevated`
  references. S.
- **PR-B.2** — `statusTone` SSOT + `StatusPill` dot gating; migrate Activity,
  Chats, Projects to it; delete local maps; purge terracotta fallbacks. M.
- **PR-B.3** — Dense control scale (decision (a)/(b)) in design-system; snapshot
  the button/badge visual guard. S/M.

## 8. Testing plan

- **Unit** (`statusTone.test.ts`): every `RunStatus` → expected `{tone,label,
showDot}`; `done`/`completed`→success, `stopped`→muted, `running`→success+dot,
  `paused`→warning; exhaustiveness (`never`) guard compiles.
- **Unit** (`StatusPill.test.tsx`): dot rendered iff `showDot`.
- **Integration:** Activity + Chats rows render the same chip colour for the same
  status (shared helper) — assert via testing-library on both surfaces.
- **Regression:** grep guard — CI check that no file under `chat-surface/src`
  references `--color-surface-elevated` with a literal fallback or `#d97757`.
- **Visual:** button/badge snapshot before/after PR-B.3 to size the blast radius.

## 9. UI/UX acceptance checklist

- [ ] `--color-surface-elevated` = `#1d1d23` dark; palette selected row uses
      `--color-surface-muted` (`#16161a`), no `#2a2a2a`.
- [ ] Chip colours: running/done → `--color-success`; paused → `--color-warning`;
      stopped/archived → muted; dot only on live.
- [ ] Projects detail pill uses tokens (no `rgb(...)` literals).
- [ ] `.ui-button--sm` and `.ui-badge` match the design dense scale (per decision);
      light + dark verified; single-accent discipline preserved.
- [ ] No `#d97757` remains as a fallback in shipping components.

## 10. Dependencies & sequencing

Upstream: none. Downstream: C (rail tokens), D (palette row colour), F (buttons),
G (chips). Land PR-B.1 before D; PR-B.2 before G; PR-B.3 before F.

## 11. Risks & mitigations

| Risk                                              | Mitigation                                                                                     |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Global button scale change regresses many screens | Decision (b) fallback (scoped `--dense`); PR-B.3 isolated + snapshot guard + reviewer sign-off |
| New tokens unused elsewhere become dead           | Each new token has ≥1 consumer in C/G; else drop it                                            |
| `statusTone` migration misses a call-site         | grep guard for local status-colour maps; typecheck on the shared `RunStatus`                   |

## 12. Definition of done

- [ ] Three tokens added (dark+light); no undefined-token fallbacks remain.
- [ ] `statusTone` SSOT consumed by Activity/Chats/Projects; local maps deleted.
- [ ] Terracotta fallbacks purged; grep guard green.
- [ ] Dense control scale applied per decision; visual snapshot reviewed.
- [ ] typecheck + vitest green; web unregressed.
