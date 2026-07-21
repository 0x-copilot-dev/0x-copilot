# PRD-C — App rail parity

**Status:** Draft · **Surface:** rail (web + desktop) · **Package:**
`@0x-copilot/chat-surface` · **Blocked by:** A, B · **Feeds from:** H (badge/identity data)

## 1. Context & problem

The rail (`packages/chat-surface/src/shell/AppRail.tsx`) is shared and mounts on
both hosts, and its geometry already matches the design (48/34/32/26). But it
draws its own icons and picks wrong colour tokens, so the **default solo rail
shows three wrong icons and a too-dark chrome** — the single most visible parity
gap in the product.

- **P1 — Wrong icons.** `projects` = square folder (design: rounded folder);
  `connectors`/"Tools" = node-graph (design: power plug); `tools`/"Skills" =
  wrench (design: sparkle); Settings = Feather cog (design: 8-spoke gear); stroke
  1.5/size 18 (design 1.7/17). `AppRail.tsx:80-235`.
- **P2 — Wrong colour tokens.** Rail bg = `--color-bg` (design `--ink2` =
  `--color-bg-elevated`); active + hover bg = `--color-bg-elevated` (design
  `--panel2` = `--color-surface-muted`) — everything one shade too dark.
  `AppRail.tsx:302,270,34`. Avatar bg = `--color-bg-elevated` (design `--panel3`)
  - a stray border.
- **P3 — Active affordance geometry.** Design active bar is a `::before` at
  `left:-8px`, `h16`, `radius 0 2 2 0` in the rail gutter; current is an inset
  button-edge bar `left:0`, `h≈22`. `AppRail.tsx:282-290`.
- **P4 — No identity / no badge.** Foot avatar renders a neutral glyph (design: the
  user's first initial); no Run badge exists (design shows a run count on Run when
  off-workspace). `AppRail.tsx:238-258,417`; badge markup absent.

## 2. Goals / Non-goals

**Goals**

- G1 — Rail renders the design icons via the PRD-A `Icon` set (delete inline
  `Glyph`/`SettingsGlyph`/`AvatarGlyph`).
- G2 — Correct tokens: rail bg `--color-bg-elevated`; active/hover
  `--color-surface-muted`; avatar `--color-surface-elevated` (PRD-B token), no
  stray border; active bar geometry per design.
- G3 — Avatar shows the user's initial; Run badge shows a live count — both wired
  through props/ports (data from PRD-H), with a safe empty default.

**Non-goals**

- NG1 — Changing the rail's destination set / order / profile gate (correct
  already in `destinations.ts`).
- NG2 — Making the deployment profile facade-sourced (tracked separately; it's a
  build-time constant today and that's acceptable for v1).

## 3. User stories

| ID     | As a…     | I want…                                                           | so that…                                               |
| ------ | --------- | ----------------------------------------------------------------- | ------------------------------------------------------ |
| US-C.1 | Solo user | rail icons that match the design (folder/plug/sparkle/gear)       | the primary nav looks intentional                      |
| US-C.2 | Solo user | to see my initial and a run count on the rail                     | I recognise my session and see active work at a glance |
| US-C.3 | Solo user | the active destination highlighted with the correct bar + surface | the active state reads clearly, not as "darker box"    |

**Acceptance (US-C.2):** _Given_ an authenticated user "Sasha" with 1 active run,
_when_ the rail renders and the active destination ≠ Run, _then_ the avatar shows
"S" and the Run item shows a "1" badge; _when_ no runs are active, no badge shows.

## 4. Functional requirements

- **FR-C.1** — Rail per-destination icon resolves via `Icon` (PRD-A) keyed by a
  slug→IconName map (`run→run, chats→chats, projects→folder, activity→activity,
connectors→plug, tools→skill`), size 17, stroke 1.7. Settings foot = `gear`;
  brand unchanged (`BrandMark`).
- **FR-C.2** — Tokens: `railStyle.backgroundColor = var(--color-bg-elevated)`;
  `railButtonStyle` active/hover bg = `var(--color-surface-muted)`;
  `RAIL_STYLE_RULES` hover = `var(--color-surface-muted)`; avatar bg =
  `var(--color-surface-elevated)`, remove the extra border.
- **FR-C.3** — Active bar: reposition to `left:-8px` (rail gutter), `height 16`,
  `radius 0 2 2 0`, `--color-accent`; centre vertically.
- **FR-C.4** — Add optional `identity?: { initial: string }` prop; when present the
  foot avatar renders `identity.initial` (uppercased, 1 char) instead of the glyph;
  fallback glyph when absent.
- **FR-C.5** — Add optional `badges?: Partial<Record<ShellDestinationSlug,
number>>` prop; a destination renders a `.rbadge`-style count when
  `badges[slug] > 0` and that slug is not the active destination (design shows Run
  badge when off-workspace). Geometry from `copilot.css .rail-item .rbadge`
  (13px, accent bg, accent-ink text, mono 8.5px).
- **FR-C.6** — Icon size 17, stroke 1.7, item `gap 2`, brand `Mark size 22`.

## 5. Architecture & system design

- **SSOT.** Icons from PRD-A; the rail keeps ONLY the slug→IconName map + geometry
  constants. Identity/badge data are **host-supplied props** (rail stays a
  controlled pure view; no fetching, honouring `chat-surface` port discipline).
  `ChatShell` threads `identity`/`badges` from whatever the host provides
  (web: `AuthContext` + a runs count selector; desktop: bootstrap identity + run
  session). The data source itself is PRD-H.
- **Data flow.** `ChatShell` (or the host binder) → `AppRail` props. Empty/loading
  = no badge, glyph avatar. No new port required if the count is derived from
  existing run/session state the host already holds; PRD-H formalises the source.
- **Reuse vs new.** Reuse `Icon` (A), `BrandMark`, `destinations.ts`. Modify
  `AppRail.tsx`, `ChatShell.tsx` (prop pass-through), both host mounts.

## 6. Affected files

- **Modify:** `chat-surface/src/shell/AppRail.tsx` (icons, tokens, bar, avatar,
  badge, props); `chat-surface/src/shell/ChatShell.tsx` (thread props);
  `apps/frontend/src/app/App.tsx` + `apps/desktop/renderer/*` (supply
  `identity`/`badges`). **Delete:** inline `Glyph`, `SettingsGlyph`, `AvatarGlyph`.
- **Modify tests:** `AppRail.test.tsx` (icon-by-name, tokens, badge/identity).

## 7. PR / commit breakdown

- **PR-C.1** — Icons + tokens + active-bar (no data changes): rail visually
  matches design with static avatar/no-badge. Depends A, B. M.
- **PR-C.2** — Identity initial + Run badge props + host wiring (data from H, or a
  local derivation if H not yet landed). Depends PR-C.1, H (soft). S/M.

## 8. Testing plan

- **Unit** (`AppRail.test.tsx`): (a) `projects` renders `Icon name="folder"`;
  `connectors` → `plug`; `tools` → `skill`; Settings → `gear`; (b) rail bg =
  `--color-bg-elevated`; active/hover = `--color-surface-muted`; (c)
  `identity.initial` renders the letter; absent → glyph; (d) `badges.run=1` +
  active≠run → badge shown; active=run → hidden; `badges.run=0` → hidden.
- **Integration:** `ChatShell` passes props through to rail (mocked host).
- **Regression:** rail geometry constants (48/34/32/26) unchanged;
  `destinations.test.ts` still green (order/labels frozen).

## 9. UI/UX acceptance checklist

- [ ] Icons match design paths (folder rounded, plug, sparkle, 8-spoke gear),
      stroke 1.7, 17×17.
- [ ] Rail bg `--color-bg-elevated`; active/hover `--color-surface-muted`; avatar
      `--color-surface-elevated`, no border; active bar `left:-8px h16 radius 0 2 2 0`
      `--color-accent`.
- [ ] Avatar shows user initial; Run badge shows count off-workspace, hidden at 0.
- [ ] States: hover / active / focus-visible (`2px --color-accent` ring) /
      reduced-motion (transitions zeroed) all correct; light + dark.

## 10. Dependencies & sequencing

Upstream: A, B (hard for PR-C.1), H (soft for PR-C.2 data). Downstream: none.

## 11. Risks & mitigations

| Risk                                        | Mitigation                                                           |
| ------------------------------------------- | -------------------------------------------------------------------- |
| Identity data not yet available on a host   | Prop optional; glyph/no-badge fallback keeps rail correct without it |
| Badge count semantics differ web vs desktop | PRD-H defines the source; rail only renders the number it's given    |

## 12. Definition of done

- [ ] Inline glyphs deleted; rail consumes `Icon`; tokens + bar corrected.
- [ ] Avatar initial + Run badge wired with safe empty defaults.
- [ ] `AppRail.test.tsx` updated + green; `destinations.test.ts` green; web
      unregressed; typecheck + vitest green.
