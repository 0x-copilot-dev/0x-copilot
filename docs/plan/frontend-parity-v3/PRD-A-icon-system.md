# PRD-A — Shared icon system

**Status:** Draft · **Surface:** all · **Package:** `@0x-copilot/chat-surface`
· **Blocks:** C, D, E, G · **Blocked by:** —

## 1. Context & problem

**P1 — There is no canonical icon set.** Every surface that needs a line icon
either hand-draws its own inline `<svg>` or omits icons entirely:

- The **rail** hand-draws a `Glyph()` switch of inline SVGs at `stroke-width 1.5`,
  `18×18` (`packages/chat-surface/src/shell/AppRail.tsx:80-216`), three of whose
  paths don't match the design (square folder, node-graph "Tools", wrench
  "Skills").
- The **settings nav** renders **no icons at all** — `SettingsSurface` only emits
  an icon when a `renderNavIcon` prop is supplied, and the only mount never
  supplies one (`SettingsSurface.tsx:416-421`; `SettingsMount.tsx:658-667`). The
  icon tokens exist in the nav model (`settingsNav.ts:58-71`) but there is nothing
  to render them.
- The **⌘K palette** renders single-character **text glyphs** (`·@≡↻✱▣✉☐`) keyed
  off a different taxonomy (`PaletteHitRow.tsx:94-100,203-222`).
- **Destination rows** (Activity/Chats/Projects) have no leading icons at all
  (PRD-G).
- The design's canonical `Icon` registry (44 glyphs, `stroke-width 1.7`,
  `viewBox 0 0 24 24`, `currentColor`) exists only as the design source
  (`copilot-data.jsx`) and, partially and divergently, as the **legacy** web
  `RailGlyph` (`apps/frontend/.../SettingsScreen.tsx`, `stroke 1.6`, `16×16`),
  which is behind the `apps/*` boundary and cannot be shared.

**Consequence.** Icon drift is guaranteed: each surface encodes its own geometry
and paths, so "fix the Tools icon" is an N-place edit, and new surfaces start from
zero. This is the root cause behind rail-icon mismatches, the icon-less settings
nav, and the palette's text glyphs.

## 2. Goals / Non-goals

**Goals**

- G1 — One canonical, framework-agnostic icon component in `chat-surface`, sourced
  byte-faithfully from the design's `Icon` registry (`stroke-width 1.7`,
  `viewBox 0 0 24 24`, `fill none`, `stroke currentColor`, round caps/joins),
  size-parameterised, `currentColor`-driven.
- G2 — Rail, settings nav, palette, and destination rows all consume it; no inline
  icon SVGs remain in those surfaces.
- G3 — A stable name→glyph map so a slug/section/command references an icon by
  name, and the map is the only place a path lives.

**Non-goals**

- NG1 — The brand `Mark` (turbine) is already a shared component
  (`shell/BrandMark.tsx`); this PRD does not touch it (it re-exports it for
  discoverability only).
- NG2 — Migrating message-area icons (`icons/{Copy,Retry,Thinking}Icon.tsx`) into
  the set — they are fine as-is; the new set may absorb them in a later cleanup.
- NG3 — Icon theming beyond `currentColor` (no multi-tone icons).

## 3. User stories

| ID     | As a…     | I want…                                               | so that…                                    |
| ------ | --------- | ----------------------------------------------------- | ------------------------------------------- |
| US-A.1 | Solo user | rail/nav/palette icons that match the design          | the app reads as one coherent product       |
| US-A.2 | Developer | to reference an icon by name (`<Icon name="plug" />`) | I never hand-draw or copy an SVG path again |
| US-A.3 | Developer | one file where each glyph's path lives                | fixing/adding a glyph is a single edit      |

**Acceptance (US-A.2):** _Given_ a component needs the Tools icon, _when_ it
renders `<Icon name="plug" size={17} />`, _then_ it gets the design's plug path at
stroke-width 1.7 in `currentColor`, and no local SVG is defined.

## 4. Functional requirements

- **FR-A.1** — Add `packages/chat-surface/src/icons/Icon.tsx` exporting an `Icon`
  component: props `{ name: IconName; size?: number; className?; style?;
title?; strokeWidth? }`. Defaults: `size` 16, `strokeWidth` 1.7. Renders
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth
stroke-linecap="round" stroke-linejoin="round" aria-hidden focusable={false}>`
  with the named glyph's `<path>`s. When `title` is provided, drop `aria-hidden`,
  add `role="img"` + `<title>`.
- **FR-A.2** — Define `ICON_PATHS: Record<IconName, ReactNode>` containing the
  design glyphs required by this suite (minimum set): `run, chats, folder,
activity, plug, skill, gear, search, plus, check, x, chevronRight,
chevronDown, back, key, chip, sliders, lock, bell, user, sun, cmd, trash,
download, external, warn, send, globe, eye, doc, clock, shield, play, dots,
coin, bolt`. Paths copied verbatim from `DESIGN-REFERENCE.md` AREA-1/AREA-2 and
  `copilot-data.jsx`.
- **FR-A.3** — Export `IconName` union + a `hasIcon(name): boolean` guard for
  data-driven call sites (settings nav / palette) that map string keys to icons.
- **FR-A.4** — Barrel `packages/chat-surface/src/index.ts` exports `Icon`,
  `IconName`, `ICON_NAMES` (frozen array) in a new delimited block.
- **FR-A.5** — Provide a thin `SlugGlyph`/`renderNavIcon` adapter so
  `SettingsSurface`'s `renderNavIcon` slot and the rail's per-slug lookup both
  resolve through `ICON_PATHS` (no second name→glyph table). Settings icon tokens
  in `settingsNav.ts` MUST be a subset of `IconName` (compile-time enforced).

## 5. Architecture & system design

- **SSOT.** `ICON_PATHS` is the _only_ place an icon path exists. `AppRail`'s
  `Glyph`, `PaletteHitRow`'s `iconGlyph`, and the legacy `RailGlyph` are deleted
  or reduced to `Icon` call-sites (RailGlyph dies with PRD-E). The settings-nav
  `SettingsNavIcon` token type is `IconName`.
- **Home.** `chat-surface/src/icons/` — every consumer (rail, settings, palette,
  destinations) is already in `chat-surface`; it depends on `design-system` for
  tokens but icons are interaction-layer assets, so they live with their
  consumers. (Considered `design-system`; rejected — design-system is
  tokens+CSS-first and component-light, and putting icons there would invert the
  natural dependency for a purely chat-surface concern.)
- **Boundaries.** Pure presentational SVG; no ports, no substrate globals. Safe
  under the `no-restricted-globals` lint.
- **Reuse vs new.**
  | | path |
  | --- | --- |
  | New | `chat-surface/src/icons/Icon.tsx`, `icons/paths.ts`, `icons/index.ts` |
  | Modify | `chat-surface/src/index.ts` (barrel) |
  | Delete (in consumers' PRDs) | `AppRail.Glyph/SettingsGlyph`, `PaletteHitRow.iconGlyph`, web `RailGlyph` |

## 6. Affected files

- **Create:** `packages/chat-surface/src/icons/Icon.tsx`,
  `packages/chat-surface/src/icons/paths.ts`, `.../icons/Icon.test.tsx`.
- **Modify:** `packages/chat-surface/src/index.ts`,
  `packages/chat-surface/src/icons/` (extend existing dir; keep Copy/Retry/Thinking).
- **Delete:** none in this PRD (consumers delete their inline glyphs in C/D/E/G).

## 7. PR / commit breakdown

- **PR-A.1** — `Icon` component + `paths.ts` (full glyph set) + unit tests +
  barrel export. No consumer changes. Size: M. Leaves tree green (additive).

## 8. Testing plan

- **Unit** (`Icon.test.tsx`, vitest): (a) `Icon name="plug"` renders an `<svg>`
  with `stroke-width="1.7"`, `viewBox="0 0 24 24"`, and the plug path `d`;
  (b) `size` sets `width`/`height`; (c) default `aria-hidden` present, and with
  `title` → `role="img"` + `<title>` and no `aria-hidden`; (d) every `IconName`
  in `ICON_NAMES` resolves to a non-empty path (table test — guards against a name
  with no glyph).
- **Integration:** none (consumers test their own usage in C/D/E/G).
- **Regression:** additive-only; no existing surface changes here.

## 9. UI/UX acceptance checklist

- [ ] `stroke-width 1.7`, `viewBox 0 0 24 24`, round caps/joins, `fill none`,
      `stroke currentColor` — matches the design `Icon` registry exactly.
- [ ] Path `d` values byte-match `DESIGN-REFERENCE.md` for at minimum:
      `folder, plug, skill, gear, run, chats, activity, key, chip, sliders, shield,
bell, lock, bolt, user, sun, cmd`.
- [ ] Inherits colour from `currentColor` (verified by rendering inside a coloured
      parent); no hard-coded stroke colour.
- [ ] `prefers-reduced-motion`: n/a (static). a11y: decorative by default
      (`aria-hidden`), labelable via `title`.

## 10. Dependencies & sequencing

Upstream: none. Downstream: **C, D, E, G** consume `Icon`; land PR-A.1 first.

## 11. Risks & mitigations

| Risk                                        | Mitigation                                                                           |
| ------------------------------------------- | ------------------------------------------------------------------------------------ |
| Glyph path typo → wrong icon                | Table test asserts each `d` against the reference; visual check in Storybook/preview |
| Set grows unbounded                         | Restrict to the audited minimum set; add on demand, one path per addition            |
| Duplicate with `design-system` future icons | Document `chat-surface/icons` as the canonical home in `chat-surface/CLAUDE.md`      |

## 12. Definition of done

- [ ] `Icon` + `paths.ts` shipped, barrel-exported, unit-tested (all `IconName`
      resolve), typecheck + vitest green.
- [ ] `settingsNav.SettingsNavIcon` retyped to `IconName` (compile-time subset).
- [ ] `chat-surface/CLAUDE.md` notes the icon SSOT.
- [ ] No behaviour change to any shipping surface (additive PR).
