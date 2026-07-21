# PRD-E — Settings convergence + nav icons

**Status:** Draft · **Surface:** Settings (web ⇄ desktop) · **Package:**
`@0x-copilot/chat-surface` + `apps/frontend` · **Blocked by:** A · **Blocks:** F

## 1. Context & problem

**P1 — Two settings screens.** The design-spec'd settings surface
(`chat-surface/src/settings/SettingsSurface.tsx` + `settingsNav.ts`) is the SSOT
and is mounted **only by desktop** (`apps/desktop/renderer/SettingsMount.tsx`). The
**web** app renders an entirely separate legacy screen
(`apps/frontend/src/features/settings/SettingsScreen.tsx` with its own
`railSections` + inline `RailGlyph`), wired at `App.tsx:746`, with a different IA
(Account / Workspace / AI & data / Notifications; Connectors, Skills, API keys; no
Advanced group; no BYOK tag). So "does Settings match the design" is _false on
web_ and the two navs drift independently — the exact anti-pattern the
`chat-surface` SSOT exists to prevent.

**P2 — The SSOT nav renders no icons.** Even on desktop, `SettingsSurface` only
emits an icon when `renderNavIcon` is provided, and `SettingsMount` never provides
it (`SettingsSurface.tsx:416-421`; `SettingsMount.tsx:658-667`). The nav is
icon-less. There is no `SettingsNavIcon → SVG` glyph set in `chat-surface` (only
the un-shareable legacy web `RailGlyph`).

**P3 — Nav chrome deltas.** Missing header ("Settings" + "Solo desktop"); active
state doesn't accent the icon (and adds a rail-style left bar the settings spec
lacks); nav bg `--color-surface` (design `--ink2` = `--color-bg-elevated`); item
font 13.6px (design 12px); a duplicate `sliders` icon on both "Models" (curation)
and "Model & behavior".

## 2. Goals / Non-goals

**Goals**

- G1 — **Web mounts `SettingsSurface` + `settingsNav`** through a web binder
  (`renderSection`) that maps each visible slug to the chat-surface section bodies,
  bound to web data ports — mirroring desktop's `SettingsMount`. Retire
  `SettingsScreen`'s parallel `railSections`/`RailGlyph`.
- G2 — Wire `renderNavIcon` on both hosts using the PRD-A `Icon` set; every nav
  item shows its design glyph (14×14, stroke 1.7).
- G3 — Nav chrome to spec: header, active icon accent, bg, item font; reconcile the
  duplicate `sliders` icon.

**Non-goals**

- NG1 — Re-authoring the section **bodies** (Profile/Appearance/etc.) — they exist
  and are reused; only the nav + host wiring changes.
- NG2 — Filling the stubbed bodies (notifications/privacy/local-models data) — that
  is body-level work tracked in PRD-H / follow-ups, not nav convergence.
- NG3 — Removing web-only sections that have no desktop analog is a **migration
  mapping** decision (see FR-E.5), not a silent drop.

## 3. User stories

| ID     | As a…           | I want…                                             | so that…                                       |
| ------ | --------------- | --------------------------------------------------- | ---------------------------------------------- |
| US-E.1 | Solo user (web) | the same Settings as desktop                        | web and desktop feel like one product          |
| US-E.2 | Solo user       | an icon on every settings nav item                  | the nav is scannable and matches the design    |
| US-E.3 | Developer       | one settings nav + one set of bodies for both hosts | a settings change lands once, not twice        |
| US-E.4 | Team admin      | team sections still gated by profile                | solo users never see workspace/members/billing |

**Acceptance (US-E.1):** _Given_ the web app on `#/settings`, _when_ it renders,
_then_ it mounts `SettingsSurface` with the `settingsNav` groups (Account / Models
& keys / Data & privacy / Notifications / Advanced), each item with its icon, and
the legacy `SettingsScreen` is no longer reachable.

## 4. Functional requirements

- **FR-E.1** — Add a web `SettingsBinder` (in `apps/frontend/src/features/settings/`)
  that renders `<SettingsSurface renderNavIcon renderSection />`, resolving each
  `SettingsSectionSlug` to the chat-surface section body with web-owned ports
  (`ProviderKeysPort` via `createProviderKeysPort`, profile/appearance/etc. via the
  web api clients). `App.tsx` dispatches `settings` here instead of `SettingsScreen`.
- **FR-E.2** — Provide `renderNavIcon = (icon: SettingsNavIcon) => <Icon
name={icon} size={14} />` (PRD-A) to `SettingsSurface` on **both** hosts
  (web binder + `SettingsMount`).
- **FR-E.3** — `SettingsSurface` renders a nav header: title "Settings" + hint
  derived from profile ("Solo desktop" for `single_user_desktop`).
- **FR-E.4** — `SettingsNavItem` colours the icon `var(--color-accent)` when
  active; remove the rail-style left bar (not in the settings spec); nav bg =
  `var(--color-bg-elevated)`; item font = `var(--font-size-xs)`.
- **FR-E.5** — Reconcile the duplicate `sliders` icon: give "Models" (curation) a
  distinct icon (e.g. `chip`/`coin`) or fold it per the mapping; document the
  web→SSOT section mapping (legacy `API keys`→`keys`, `AI & data`→behavior/privacy,
  `Connectors`/`Skills` → rail destinations, not settings) so no web section is
  lost silently.
- **FR-E.6** — Delete `SettingsScreen.railSections` + `RailGlyph` once the binder
  is mounted and section parity is verified.

## 5. Architecture & system design

- **SSOT.** `settingsNav.ts` = the one nav model; `SettingsSurface` = the one
  chrome; the section bodies = the one implementation; icons = PRD-A. Web and
  desktop differ only in their **binder** (`renderSection` + data ports), exactly
  the documented host-adapter pattern (`chat-surface/CLAUDE.md`). No
  `apps/*→apps/*` import; the web binder duplicates only pure projection, not
  components.
- **Data flow.** `SettingsSurface(activeSlug, onNavigate, renderNavIcon,
renderSection)`; the host binder owns fetching per section. Profile gate stays in
  `settingsNavForProfile` (already correct).
- **Reuse vs new.** Reuse `SettingsSurface`, `settingsNav`, all section bodies,
  `Icon` (A). New: web `SettingsBinder`. Delete: `SettingsScreen`, `railSections`,
  `RailGlyph`.

## 6. Affected files

- **Create:** `apps/frontend/src/features/settings/SettingsBinder.tsx` (+ test).
- **Modify:** `chat-surface/src/settings/SettingsSurface.tsx` (header),
  `SettingsChrome.tsx` (active icon accent, bg, font, left-bar removal),
  `settingsNav.ts` (icon dedupe); `apps/frontend/src/app/App.tsx` (dispatch);
  `apps/desktop/renderer/SettingsMount.tsx` (`renderNavIcon`).
- **Delete:** `apps/frontend/src/features/settings/SettingsScreen.tsx` legacy nav
  path (`railSections`, `RailGlyph`) — after parity verified.

## 7. PR / commit breakdown

- **PR-E.1** — `renderNavIcon` wiring + nav header + active-icon-accent + bg/font +
  icon dedupe (desktop-visible immediately). Depends A. M.
- **PR-E.2** — Web `SettingsBinder` mounting `SettingsSurface`; `App.tsx` dispatch;
  section mapping. M/L.
- **PR-E.3** — Delete legacy `SettingsScreen` nav (`railSections`/`RailGlyph`) once
  PR-E.2 verified. S.

## 8. Testing plan

- **Unit** (`SettingsBinder.test.tsx`): each visible solo slug renders its body;
  team slugs absent for `single_user_desktop`; nav icons rendered (SVG per slug).
- **Unit** (`SettingsChrome`/`settingsNav`): active item icon coloured accent; no
  left bar; header present; "Models" and "Model & behavior" have distinct icons.
- **Integration:** web `#/settings/keys` renders `ProviderKeysPage` (sets up PRD-F);
  profile gate hides workspace group.
- **Regression:** desktop `SettingsMount` unaffected except gaining icons; existing
  `settingsNav.test.ts` (profile gate) green.

## 9. UI/UX acceptance checklist

- [ ] Both hosts: every nav item shows its design icon (14×14, stroke 1.7); active
      item icon = `--color-accent`; no left bar.
- [ ] Nav header "Settings" + "Solo desktop"; nav bg `--color-bg-elevated`; item
      font 12.5px; group labels mono-uppercase; collapsible Advanced.
- [ ] Groups/labels/order match design (Account / Models & keys / Data & privacy /
      Notifications / Advanced); BYOK tag on Provider keys.
- [ ] Web renders the SSOT surface (legacy screen unreachable); light + dark.

## 10. Dependencies & sequencing

Upstream A. Downstream F (rides the web mount). Land PR-E.1 (icons/chrome) early;
PR-E.2 (web mount) before F; PR-E.3 (delete) last.

## 11. Risks & mitigations

| Risk                                 | Mitigation                                                                                    |
| ------------------------------------ | --------------------------------------------------------------------------------------------- |
| Web loses a section during migration | FR-E.5 explicit web→SSOT mapping; parity checklist before PR-E.3 delete                       |
| Web data ports differ from desktop   | Binder owns web ports; bodies are presentational and port-agnostic                            |
| Stubbed bodies now visible on web    | Same honesty as desktop; stubs tracked; acceptable for convergence (bodies filled separately) |

## 12. Definition of done

- [ ] Web mounts `SettingsSurface`; legacy nav deleted; both hosts show nav icons.
- [ ] Nav chrome to spec; profile gate intact; section mapping documented.
- [ ] Unit + integration green; desktop unaffected; typecheck + vitest green.
