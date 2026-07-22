# Projects destination — design-parity audit

Design baseline: vendored Claude Design app-v3 `ProjectsSurface`
(`tools/design-parity/design-kit/app-v3/copilot-app.jsx` + `copilot.css`), measured at 1440x900.
Live: the real shipping components, rendered by `tools/design-parity/lib/render-live-projects.test.tsx`.

Three states measured: `default` (web host's own grid), `detail` (chat-surface
`ProjectDetailView` via the web host's `renderDetail` slot), `default-chatsurface`
(desktop host's list — `ProjectsDestination` CardGrid).

Companion documents in this directory: `FINDINGS.md` (root-cause narrative),
`report-default.md`, `report-detail.md`, `report-default-chatsurface.md` (raw rows).

---

# Part 1 — UI fidelity

Raw property rows: **HIGH 48 · MED 96 · LOW 112**. These are per-property rows,
not distinct defects. They collapse into **12 root causes**. Read the caveats at
the end of Part 1 before quoting any single number: 11 of the 48 HIGH rows are
comparator artifacts, and `default.page.container` is re-measured in all three
states, triple-counting 4 MEDIUM + 4 LOW rows.

| State                 | Live implementation                                                                       | HIGH | MED | LOW |
| --------------------- | ----------------------------------------------------------------------------------------- | ---- | --- | --- |
| `default`             | web host's bespoke grid — `apps/frontend/src/features/projects/ProjectsRoute.tsx:840-960` | 9    | 28  | 29  |
| `detail`              | `chat-surface` `ProjectDetailView` via the web host's `renderDetail` slot                 | 26   | 42  | 48  |
| `default-chatsurface` | desktop host's list — `chat-surface` `ProjectsDestination` CardGrid                       | 13   | 26  | 35  |

## RC-1 (HIGH) — Two hosts render two different Projects lists, and neither is the design's

**Anchors: essentially every `default` and `default-chatsurface` row (~60), plus the entire `detail` state on desktop.**

The web host does **not** mount the shared destination for the list. It renders a
bespoke scaffold — `apps/frontend/src/features/projects/ProjectsRoute.tsx:840-960`
with a scoped `<style>` string at `:965-1050` — and mounts `<ProjectsDestination>`
only once a project is focused (`ProjectsRoute.tsx:821-829`, verified: the detail
branch is the _only_ place the package component appears on web). The desktop host
mounts the shared `<ProjectsDestination>` bare
(`apps/desktop/renderer/destinationBinders.tsx:567` — `<ProjectsDestination items={result} onRetry={retry} />`).

| Property     | Design `.card.proj-card` (copilot.css:737-742, 1711-1716) | Web (`ProjectsRoute.tsx:977-999`)           | Desktop (`ProjectsDestination.tsx:378-387`) |
| ------------ | --------------------------------------------------------- | ------------------------------------------- | ------------------------------------------- |
| element      | single `<button>` — whole card is the hit area            | `<div>` + inner `<button>` + footer `<div>` | `<article>` — name link only                |
| borderRadius | `var(--r)` 8px                                            | 12px                                        | `var(--radius-md)` 8px                      |
| padding      | `var(--pad)` 13px                                         | 0 on card, `14px 14px 10px` on button       | 14px                                        |
| grid gap     | 10px (`.grid3`, copilot.css:1672-1682)                    | 12px (`ProjectsRoute.tsx:969`)              | 12px                                        |

Second-order HIGH: the desktop binder passes no `focusedProjectId` / `renderDetail`
(`destinationBinders.tsx:563-568`, whose own comment at `:542-544` reads
"Creation / mutation / detail flows aren't wired on desktop yet, so the grid renders
read-only"), and `ProjectsDestination.tsx:283`
(`const showingDetail = renderDetail !== undefined && focusedProjectId !== null;`)
gates the detail pane on both. **The entire `detail` design state is unreachable on
desktop.** The component is built; the binding is not.

Fix this first. Every RC below otherwise has to be fixed twice and will re-drift.

## RC-2 (HIGH) — No shared `ProjectIconTile`: the monogram tile exists in four divergent copies

**Anchors: `default.card.icon` (5 props), `detail.icon` (7), desktop `default.card.icon` (5).**

| Source                                                | size   | radius | font-size                 | weight  | colours                                                                              |
| ----------------------------------------------------- | ------ | ------ | ------------------------- | ------- | ------------------------------------------------------------------------------------ |
| Design `.proj-ic` (copilot.css:1698-1710)             | 32     | 8      | 13px                      | 600     | `--panel3` / `--tx2`, forced with `!important`                                       |
| Web card (`ProjectsRoute.tsx:1000-1013` + `:866-876`) | 32     | 8      | **14px**                  | **700** | inline `hsl(h 60% 28% / .45)` bg, `hsl(h 60% 50% / .55)` border, `hsl(h 70% 82%)` fg |
| Desktop card (`ProjectsDestination.tsx:394-404`)      | **28** | **6**  | `--font-size-lg` **16px** | 400     | solid `hsl(h 60% 28%)`, `--color-text`                                               |
| Detail header (`ProjectDetailView.tsx:268-292`)       | **44** | **10** | `--font-size-xl` **18px** | **700** | same hsl triple as the web card                                                      |

Size / radius / weight drift is unambiguous. The **colour** needs a product
decision, not a blind fix: the design's JSX sets a per-project colour inline
(`copilot-app.jsx:403`) but `.proj-ic` overrides it with
`background: var(--panel3) !important; color: var(--tx2) !important`, so the
_rendered_ design tile is neutral `rgb(29,29,35)` / `rgb(212,212,219)` — the colour
survives only in the fixture. Live renders the colour. See "Do not change" below.

## RC-3 (HIGH) — The detail chat list is hand-rolled; the design's row anatomy does not exist

**Anchors: 5 `missing-in-live` rows + 10 drift rows.**

`ProjectsRoute.tsx:641-673` renders the project's chats as a bare
`<ul style={{listStyle:"none",margin:0,padding:0}}>` of `<li style={{padding:"8px 0"}}>`,
each holding one accent-coloured `<button>`.

- `detail.chatrow.icon`, `.chip`, `.sub`, `.sub .mono`, `.time` → **missing-in-live** (5 HIGH)
- `detail.rowlist.chats` → background `--panel` → transparent; border 1px `--line` → 0; radius 8 → 0; `flex/column` → `block`
- `detail.chatrow` → padding `11px 14px` → `8px 0`; gap 12px → normal; `align-items:center` → normal; no hairline separator

The primitives that encode exactly this anatomy exist and are unused here:
`packages/chat-surface/src/destinations/_shared/Row.tsx:35-51` (icon / chip / sub /
meta slots) and `_shared/RowList.tsx:28-42` (bordered card + per-row hairlines).
`_shared/index.ts:1-3` states the intent verbatim — _"The design row anatomy
(`.pg-lead` / `.sect-h` / `.rowlist` / `.lrow`) defined once, so Activity / Chats /
Projects can't drift."_ Verified by reading the file: Projects is the one consumer
that does not import `Row` or `RowList`.

Content-level too: the live row title renders `a.preview` (`ProjectsRoute.tsx:670`)
where the design's title is the chat **name** with the preview on the sub-line
(`copilot-app.jsx:255-286`).

## RC-4 (HIGH) — Project files have no backend, so the design's Files rowlist is unrenderable

**Anchors: `detail.filerow`, `.filerow.name`, `.filerow.sub` missing-in-live + `detail.rowlist.files` (4 props).**

`GET /v1/projects/{id}/files` does not exist. `services/backend-facade/src/backend_facade/projects_routes.py:20-32`
enumerates every proxied projects route (list, get, create, patch, delete, restore,
members ×4, transfer, star/unstar) — no files route, and no handler under
`services/backend/src/backend_app/`.

The client is honest about it: `ProjectsRoute.tsx:679-684` deliberately omits the
`files` prop and `ProjectDetailView.tsx:592-606` degrades to the "Project files
coming soon" `EmptyState` rather than a stuck skeleton. This is a **missing
capability, not a CSS defect**. Note the design's own Files header is internally
inconsistent ("Files · 12" from the fixture over 4 rendered rows,
`copilot-app.jsx:369-371`) — its row count is not a spec.

## RC-5 (HIGH) — `.pg-lead` explainer missing on both hosts, although the shared primitive exists

**Anchors: `default.page.lead` missing-in-live (web + desktop).**

Design `.pg-lead` (copilot.css:1556-1562) is a 12px `--mut` explainer capped at 72ch,
present only in the default state. Neither host renders one.
`packages/chat-surface/src/destinations/_shared/PageLead.tsx:22-28` implements it,
is exported from `_shared/index.ts:5` (verified), and its tokens are already correct
(`--font-size-xs` 12.48px vs 12px; `--color-text-muted` == the design's `--mut`).
`ActivityDestination.tsx:315` and `ChatsArchive.tsx:300` already use it; nothing
under `destinations/projects/` imports it. Pure mount-and-write-copy fix.

## RC-6 (MEDIUM) — `.lrow__sub` text: wrong colour rung, wrong size rung, mono dropped

**Anchors: `default.card.desc` (2), `default.card.meta` (2), `detail.desc` (2), desktop card (5).**

Design `.lrow__sub` (copilot.css:1643-1648): 11px, `var(--mut2)` `#64646d`,
`var(--mono)` — with the _description_ overriding to body font inline while the
_counts_ line stays mono (`copilot-app.jsx:416-424`). That deliberate mono/system
pairing inside one card is lost in all four implementations:

| Live site                                            | size                     | colour                               | family     |
| ---------------------------------------------------- | ------------------------ | ------------------------------------ | ---------- |
| `ProjectsRoute.tsx:1020-1027` `.projects-card__desc` | 12px literal             | `--color-text-muted` `#98989f` WRONG | sans       |
| `ProjectsRoute.tsx:1028-1032` `.projects-card__meta` | 12px literal             | `--color-text-subtle` OK             | sans WRONG |
| `ProjectsDestination.tsx:414-428` desc + meta        | `--font-size-xs` 12.48px | `--color-text-muted` WRONG           | sans WRONG |
| `ProjectDetailView.tsx:441-452` description          | `--font-size-sm` 13.6px  | `--color-text-muted` WRONG           | sans       |

The correct rungs already exist and were verified in the token file:
`--color-text-subtle: #64646d` (`packages/design-system/src/styles.css:178`) is
byte-identical to `--mut2`, and `--font-size-2xs: 0.7rem` (11.2px, `:63`) is the
nearest rung to 11px. `_shared/Row.tsx:102-115` already uses exactly that triple —
in the component Projects does not use.

## RC-7 (MEDIUM, cross-surface) — `SectionHeader` hand-rolls the mono micro-label instead of the shipped `.ui-mono-caps` recipe

**Anchors: `detail.secth.chats` + `detail.secth.files` (4 props).**

Verified by reading the file: `packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:37-46`
sets `fontFamily: var(--font-mono)`, `fontSize: var(--font-size-2xs)` (11.2px),
`fontWeight: var(--font-weight-semibold)` (600) and a **literal** `letterSpacing: "0.12em"`.
Design `.sect-h` (copilot.css:1563-1573) is 9.5px, weight 400, .12em, colour `--mut2`,
margin `22px 0 10px`.

The canonical recipe shipped in the UI-kit consolidation and is 1.7px smaller and
200 lighter: `packages/design-system/src/styles.css:1098-1104` `.ui-mono-caps` =
`var(--font-mono)` + `var(--font-size-3xs)` (9px, `:62`) + `var(--tracking-mono-caps)`
(0.12em, `:92`) + uppercase — the exact design rule, differing only in colour token.
SectionHeader predates the recipe and never migrated, so it also keeps a
magic-number letter-spacing that the `--tracking-*` scale was introduced to
eliminate. Because SectionHeader is shared, this drift hits Activity, Chats and
every other `.sect-h`, not just Projects.

## RC-8 (MEDIUM) — Navigation controls styled as accent hyperlinks where the design uses quiet chrome

**Anchors: `detail.backlink` (8 props), `detail.chatrow.name` colour, desktop `default.card.name.link` colour.**

| Control           | Live                                                                                                    | Design                                                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| "← All projects"  | `ProjectsRoute.tsx:686-701` — 13px **sans**, weight 600, `--color-accent`, no icon, `padding: 0 0 12px` | `.backlink` copilot.css:1721-1739 — inline-flex, gap 6, **mono 11px**, `--mut`, 13×13 svg, `margin-bottom:14px`, hover → `--tx` |
| chat row title    | `ProjectsRoute.tsx:653-668` — `--color-accent`                                                          | `.lrow__name` copilot.css:1635-1642 — 12.5px / 500 / `--tx`                                                                     |
| desktop card name | `packages/chat-surface/src/refs/ItemLink.tsx:69-76` — `color: var(--color-accent)`                      | 14px / 600 / `--tx` (`copilot-app.jsx:406-412`)                                                                                 |

The design never paints a Projects navigation target in the accent colour. The
`ItemLink` case has the widest blast radius — every destination rendering an
`ItemLink` inherits it.

## RC-9 (MEDIUM) — No shared `.pg` page shell; Projects is the only destination that skips the 960px column

**Anchors: `default.page.container` padding / width / flexGrow, in all three states.**

Design `.pg` (copilot.css:1552-1555): `padding: 20px 24px 40px; max-width: 960px`.

- Web list: `ProjectsRoute.tsx:796-802` — verified: `padding: 24`, **no** max-width → measured 1040px vs 960.
- Detail: `ProjectDetailView.tsx:848-857` — `maxWidth: 1000`, `padding: "24px 28px 48px"`.
- Conforming siblings, all 960 but with five different paddings: `ActivityDestination.tsx:611-620`
  (`16px 20px 32px`), `ChatsArchive.tsx:147-156` (`24px 28px 96px`), `SkillsDestination.tsx:108`,
  `RoutineDetail.tsx:1068`.

`_shared/` ships `PageLead` / `SectionHeader` / `RowList` / `Row` (verified by `ls`)
but **no `Page`** — the one primitive that would have prevented this.

## RC-10 (MEDIUM, cross-surface) — Base body size is 13.6px, not the design's 13px

**Anchors: every `+0.6px` row — page.container, grid, card, card.hitarea, rowlist.chats, rowlist.files, chatrow (9 rows across 3 states).**

`packages/design-system/src/styles.css:356-378` sets `body { font-size: var(--font-size-sm) }`
= `0.85rem` = 13.6px (`--font-size-sm` confirmed at `:65`). The design's body is a
literal 13px (copilot.css:105-112). The in-file comment already documents the trade:
the rem ladder has no 13px rung, so `--font-size-sm` was reused "rather than mint a
new value or an ad-hoc px". **Not a defect** — but it is why roughly a third of the
MEDIUM rows exist, and it silently offsets every element that inherits rather than
declares a size.

## RC-11 (LOW, cross-surface) — App canvas is `#09090b`; the design's body is `#050506`

**Anchor: desktop `default.page.container` backgroundColor.**

`--color-bg: #09090b` (`packages/design-system/src/styles.css:168`, verified) vs
`body { background: #050506 }` (copilot.css:105). Surfaced only on the
`default-chatsurface` state because the chat-surface destination root paints
`--color-bg` explicitly (`ProjectDetailView.tsx:841`) while the design's `.pg` is
transparent over `body`. Affects every surface, not just Projects.

## RC-12 (LOW) — Detail title omits the design's -0.01em tracking although the token exists

**Anchor: `detail.title` letterSpacing `-0.18px` → `normal`.**

`ProjectDetailView.tsx:333-341` sets `--font-size-xl` + weight 600 (both correct)
but no `letterSpacing`; the design's global `h1-h4` rule (copilot.css:113-121)
applies `-0.01em`. The token exists: `--tracking-snug: -0.01em`
(`packages/design-system/src/styles.css:87`, verified).

## Measurement caveats — read before quoting a row

1. **11 of the 48 HIGH rows are comparator artifacts.** When an element has
   `border-style: none`, `getComputedStyle().borderColor` returns `currentColor`,
   so a `borderColor` row appears that merely restates the adjacent `color` row.
   Affected: `default.card.hitarea`, `default.card.desc`, `detail.desc`,
   `detail.backlink`, `detail.chatrow.name`, `detail.rowlist.chats`,
   `detail.rowlist.files`, `detail.chatrow`, desktop `card.name.link` / `.desc` / `.meta`.
2. **`default.card.hitarea backgroundColor --panel → transparent` is structural, not a colour bug.**
   The live card paints `--color-surface` (`#111114`, identical to the design's `--panel`)
   on the wrapper `div` while the inner `button` is transparent; `default.card backgroundColor`
   did **not** drift. Only the element carrying the surface moved (RC-1).
3. **`detail.secth` text "Chats · 3" → "Chats" is an anchor artifact.** The count _is_
   rendered, in a sibling `[data-testid="section-header-count"]` (`SectionHeader.tsx:76-84`).
4. **Neither side loads JetBrains Mono.** The design declares the family with no
   `@font-face` (copilot.css:39); the live `ds.css:11-20` `@font-face` 404s in the
   harness. Both fall back to system monospace — symmetric, so mono/sans `fontFamily`
   comparisons hold, but absolute mono metrics are fallback-rendered on both sides.
5. **`default.page.container` is re-measured in every state**, triple-counting 4 MEDIUM + 4 LOW rows.

## What could not be measured, and why

- **Hover / focus / active states.** The extractor reads static computed styles only,
  so `.card.proj-card:hover` (copilot.css:1717-1720), `.lrow:hover` (`:1601-1603`)
  and `.backlink:hover` (`:1734-1736`) were never compared.
- **`chip--warn` / `chip--off` variants.** The Launch Week fixture exercises only
  `chip--ok`, and the live side has no chip at all (RC-3) — no comparison target on either side.
- **The desktop project detail state.** Unreachable (RC-1), so no live DOM exists to diff.
  That absence _is_ the finding.
- **TemplateGallery, TemplateEditor, ProjectEditor, ProjectMembersTab, ProjectActivityTab,
  transfer-ownership / fork-from-template / archive-blocked dialogs.** The design harness
  exposes only `?dest=projects&state=default|detail` — no design side. Reported as
  live-capability-with-no-design, not drift.
- **Responsive behaviour.** Everything measured at 1440×900 only. The <900px single-column
  collapse (copilot.css:1678-1682 vs `ProjectsRoute.tsx:974-976`) matches by code read, not measurement.

---

# Part 2 — Feature parity

**Verdict key.** `GAP` = survived adversarial refutation, real. `PARITY` = live
matches the design. `EXTRA (INFO)` = live capability the design has no counterpart
for — _do not delete_. `NOT A GAP` = claim refuted. `UNVERIFIED` = lens consensus
that was not put through the refutation pass; treat as a lead, not a finding.

| Feature                                             | Design                                                                      | Live UI                                                | Web host                                                                       | Desktop host                                                                                                               | Facade                                                                                                                        | Backend                                                                                                                                                                                                   | Verdict                                                                                                                                                                                      |
| --------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **F01** Three-up project grid                       | `repeat(3,1fr)`, gap 10 (copilot.css:1672-1682)                             | two different grids                                    | fixed 3-up, gap 12 — `ProjectsRoute.tsx:844`, `:966-968`                       | width-derived `auto-fill/minmax` — `shell/CardGrid.tsx:31`                                                                 | `GET /v1/projects` — `projects_routes.py:118`                                                                                 | real — `projects/routes.py:204`, `store.py:662`                                                                                                                                                           | UNVERIFIED — subsumed by RC-1                                                                                                                                                                |
| **F02** Whole card is the open affordance           | single `<button>` — `copilot-app.jsx:396-401`                               | web: button + extra footer; desktop: inert `<article>` | ✅ `ProjectsRoute.tsx:864-869`, tested `ProjectsRoute.test.tsx:838-840`        | ❌ `<article>` `ProjectsDestination.tsx:456-462`; no open callback in props `:94-135`; binder `destinationBinders.tsx:567` | n/a (client)                                                                                                                  | n/a                                                                                                                                                                                                       | **GAP** — HOST_GAP (desktop)                                                                                                                                                                 |
| **F03** Project monogram tile                       | 32×32 letter monogram `.proj-ic` (copilot.css:1698-1710)                    | web monogram; desktop emoji                            | ✅ `ProjectsRoute.tsx:845,868-880` + CSS `:1000-1010`                          | ❌ `{project.icon_emoji}` 28×28 — `ProjectsDestination.tsx:394-405,469`                                                    | `projects_routes.py:118` carries name + icon_emoji                                                                            | `routes.py:859`; `icon_emoji` defaults to 📁 — `service.py:366`, `store.py:94`                                                                                                                            | **GAP** — HOST_GAP (desktop); every desktop card shows the same glyph                                                                                                                        |
| **F04** Per-project accent colour                   | data present, **neutralized** by `!important` (copilot.css:1706-7)          | live paints the hue, in 3 non-agreeing formulas        | `ProjectsRoute.tsx:871-876`                                                    | `ProjectsDestination.tsx:398`                                                                                              | `projects_routes.py:118`                                                                                                      | `color_hue` persisted, `routes.py:862`                                                                                                                                                                    | **NOT A GAP** — deliberate divergence; needs a decision, not a revert                                                                                                                        |
| **F05** Name / description / counts on the card     | name + desc + "N chats · N files"                                           | name+desc real; counts permanently 0                   | `ProjectsRoute.tsx:880-889`, counts `:847-848`                                 | `ProjectsDestination.tsx:475-480,531-535,558-562` (shows todos/routines instead of files)                                  | pass-through `projects_routes.py:118-138`                                                                                     | ⚠️ `service.py:1069-1085` synthesizes zeros; `store.upsert_counts` (`:604`,`:1115`) has **no production caller**; projector `activity_projector.py` (schema.sql:175-6) does not exist                     | **GAP** — PARTIAL: name/desc LIVE, counts BACKEND_STUB                                                                                                                                       |
| **F06** Surface lead copy                           | `.pg-lead` explainer, `copilot-app.jsx:391-394`                             | absent on both hosts                                   | ❌ no PageLead, no PageHeader — `ProjectsRoute.tsx:790-955`                    | ❌ subtitle is a count string — `ProjectsDestination.tsx:278-281`                                                          | n/a                                                                                                                           | n/a                                                                                                                                                                                                       | **GAP** — MISSING both hosts; primitive exists (`_shared/PageLead.tsx:29`) and is used by Chats + Activity                                                                                   |
| **F07** Topbar title + subtitle                     | "Projects" / "group chats, files & context" — `copilot-app.jsx:600,818-822` | title yes, subtitle no                                 | ❌ no `topbarLeaf` — `App.tsx:1200-1226`                                       | ❌ no `topbarLeaf` — `bootstrap.tsx:316-330`                                                                               | n/a                                                                                                                           | n/a                                                                                                                                                                                                       | **GAP** — PARTIAL: title DONE (`destinations.ts:85`→`Topbar.tsx:88,140-142`), subtitle MISSING. Proven at `render-live-projects.test.tsx:475-513`                                            |
| **F08** ⌘K entry point                              | "Go to Projects" navigation hit                                             | palette opens; no navigation-hit producer              | `features/palette/PaletteHost.tsx:51-59`                                       | `apps/desktop/renderer/PaletteHost.tsx`                                                                                    | `palette_routes.py`                                                                                                           | server emits only `action` (`palette/service.py:225`) and `entity` (`:307`)                                                                                                                               | UNVERIFIED — client branch for `kind:"navigation"` looks dead                                                                                                                                |
| **F09** Rail run badge                              | `.rbadge` on the rail                                                       | built; lenses disagree on binding                      | see rail-badge audit                                                           | `bootstrap.tsx:318-332` passes no `railBadges`                                                                             | `liveness_routes.py:25`                                                                                                       | ⚠️ `liveness/service.py:111` calls `GET /v1/agent/runs`, which is **POST-only** (`runtime_api/http/routes.py:634`)                                                                                        | OUT OF SCOPE — owned by the sibling rail-badge audit                                                                                                                                         |
| **F10** Drill into a project (grid → detail)        | click card → detail                                                         | web only                                               | ✅ `ProjectsRoute.tsx:289,821-829,866`                                         | ❌ no `renderDetail`/`focusedProjectId` — `destinationBinders.tsx:563-568`; `DestinationOutlet.tsx:196` mounts it propless | ✅ `GET /v1/projects/{id}` — `projects_routes.py:141`                                                                         | ✅ ACL-gated `routes.py:281` → `service.py`                                                                                                                                                               | **GAP** — HOST_GAP. Only desktop hit target resolves to a placeholder `{kind:"workspace"}` route (`destinations/projects/index.ts:167-172`) that `bootstrap.tsx:228-236` ignores             |
| **F11** Back to all projects                        | `.backlink` mono/11px/`--mut` + svg (copilot.css:1721-1740)                 | web-only, and mis-styled                               | ✅ `ProjectsRoute.tsx:686-703,827`, tested `:869-874`                          | ❌ no detail state to exit                                                                                                 | n/a                                                                                                                           | n/a                                                                                                                                                                                                       | **GAP** — HOST*GAP + STYLE_DRIFT. The close \_seam* is shared (`ProjectsDestination.tsx:127-131,313-325`); only the button markup is host-local                                              |
| **F12** Detail header restating identity            | tile + h2 + description                                                     | web-only; adds 3 pills                                 | ✅ `ProjectsRoute.tsx:748-776`, tested `ProjectsRoute.test.tsx:843-874`        | ❌ `ProjectDetailView` never imported under `apps/desktop/`                                                                | `projects_routes.py:141`                                                                                                      | `routes.py:845-882` `_to_wire` never emits `owner_display_name` (declared `api-types/src/projects.ts:312`)                                                                                                | **GAP** — HOST*GAP + content defect: `ownerNameFor` (`ProjectsRoute.tsx:1088-1094`) returns a raw user id, so the header prints "Owner: user*…"                                              |
| **F13** Project-scoped chat list + live count       | `Chats · N` over shared rows                                                | header renders, always "Chats · 0" over an empty list  | `ProjectsRoute.tsx:624-676,758`                                                | ❌ none                                                                                                                    | ❌ `GET /v1/projects/{id}/activity` registered on **neither** facade (`projects_routes.py` decorators `:66…:518`) nor backend | ❌ no project↔conversation link at all: `ConversationRecord` has no `project_id` (`schemas/conversations.py:114-175`); create-time value goes only into an audit blob (`conversation_coordinator.py:173`) | **GAP** — MISSING, three independent dead layers                                                                                                                                             |
| **F14** Chat rows are the SAME component Chats uses | one shared `ChatRow` — `copilot-app.jsx:255` used at `:314,320,326,366`     | two unrelated representations                          | bespoke `<ul>/<li>/<button>` — `ProjectsRoute.tsx:639-670`                     | ❌ none                                                                                                                    | ❌ `/activity` missing                                                                                                        | ❌ `store.append_activity`/`list_activity` (`store.py:310,314,562,575,1037,1070`) have **zero non-test callers**                                                                                          | **GAP** — MISSING. `_shared/Row` is imported by `ChatsArchive.tsx:41` and by no projects file                                                                                                |
| **F15** Chat status pill                            | running/done → `chip--ok`, paused → `chip--warn`, archived → `chip--off`    | no chip at all                                         | `ProjectsRoute.tsx:650-673`                                                    | ❌ none (no detail)                                                                                                        | ❌                                                                                                                            | ❌ `ProjectActivity` has no status field (`api-types/src/projects.ts:343-357`)                                                                                                                            | **GAP** — MISSING. Do **not** score against the project-lifecycle pill at `ProjectDetailView.tsx:422-437` — different entity, present and tested                                             |
| **F16** Live-run affordance on a running chat       | jade `<Mark/>` + `.dotk` — `copilot-app.jsx:262,266-269,275`                | absent everywhere                                      | ❌ no status conditional                                                       | ❌ no detail                                                                                                               | project-level only — `liveness_routes.py:25`                                                                                  | ⚠️ `liveness/service.py:111-117` calls a POST-only route                                                                                                                                                  | **GAP** — MISSING. `"dotk"` has zero occurrences in `packages/` + `apps/`                                                                                                                    |
| **F17** Chat preview + model attribution            | `{preview} · <span class="mono">{model}</span>` — `copilot-app.jsx:280`     | preview only, web only, no mono                        | `ProjectsRoute.tsx:668`                                                        | ❌ no detail                                                                                                               | ❌ no project-scoped conversations route                                                                                      | preview persisted (`store.py:171`, `schema.sql:196`); **no** model field; `list_conversations` (`runtime_api/http/routes.py:113-130`) has no `project_id` filter                                          | **GAP** — MISSING (preview half only). Capability is built in `ChatsArchive.tsx:435-452` and not reused                                                                                      |
| **F18** Relative timestamps on chat rows            | `.lrow__time` on chats, none on files                                       | **inverted** — files have `<time>`, chats do not       | `occurred_at` mapped at `ProjectsRoute.tsx:1114`, never rendered (`:651-671`)  | ❌ no detail                                                                                                               | ❌ `/activity` missing                                                                                                        | field exists (`api-types/src/projects.ts:356`)                                                                                                                                                            | **GAP** — MISSING. A timestamped renderer exists at `ProjectActivityTab.tsx:134-143` but only in the `profile==="team"` branch (`ProjectDetailView.tsx:998-1005`), which no host sets        |
| **F19** Chat row navigates to the Run cockpit       | click row → run                                                             | handler correct, rows never render                     | ✅ `ProjectsRoute.tsx:657` + `App.tsx:1069`                                    | ❌ none                                                                                                                    | nav target real — `app.py:433,468`                                                                                            | nav target real — `runtime_api/http/routes.py:592,606`; **row source** 404s and is swallowed at `ProjectsRoute.tsx:462-465`                                                                               | **GAP** — corrected to MISSING (dead end-to-end), not merely HOST_GAP                                                                                                                        |
| **F20** Project files section                       | second stacked `.rowlist`                                                   | permanent "coming soon"                                | `files` prop deliberately omitted — `ProjectsRoute.tsx:678-684`                | ❌ no detail                                                                                                               | ❌ no files route                                                                                                             | ❌ no handler                                                                                                                                                                                             | UNVERIFIED (unanimous) — same as RC-4                                                                                                                                                        |
| **F21** File rows deliberately non-interactive      | inert `cursor: default` div                                                 | built as a clickable `ItemLink`                        | `ProjectDetailView.tsx:797` (unreachable)                                      | ❌                                                                                                                         | ❌                                                                                                                            | ❌                                                                                                                                                                                                        | **NOT A GAP** — latent extra-in-live; decide before the endpoint lands                                                                                                                       |
| **F22** Single generic file glyph                   | uniform `Icon.doc` — `copilot-app.jsx:371-375`                              | no glyph at all, and none possible                     | resolver returns `icon: null` — `App.tsx:278-285`, `ProjectsRoute.tsx:104-110` | no ItemRef resolvers registered under `apps/desktop/renderer`                                                              | ❌                                                                                                                            | ❌                                                                                                                                                                                                        | **GAP** — MISSING. The `doc` glyph _does_ exist in the icon SSOT (`icons/paths.tsx:52,184`) — unused-primitive drift                                                                         |
| **F23** Files header count decoupled from rows      | header count is a fixture                                                   | header is a hardcoded 0 over a placeholder             | `fileCount: project.counts.library_items` — `ProjectsRoute.tsx:759`            | ❌                                                                                                                         | `projects_routes.py:141`                                                                                                      | same dead counts table as F05; no `files` field in the contract at all                                                                                                                                    | **GAP** — BACKEND_STUB. A real source exists and is unwired: `GET /v1/library?filter[project_id]=` (`library/routes.py:120`, `library_routes.py:50`, `libraryApi.ts:496-497`)                |
| **F24** Stacked sections, not tabs                  | two stacked sections                                                        | correct on web (solo default)                          | `ProjectsRoute.tsx:768-776` passes no `profile` → solo                         | ❌ no detail                                                                                                               | n/a                                                                                                                           | n/a                                                                                                                                                                                                       | UNVERIFIED — mostly PARITY. Caveat: the 4-chip FilterTabs render _above_ the detail (`ProjectsRoute.tsx:824-829`) and are inert                                                              |
| **F25** Hover on cards, rows, backlink              | 3 targets (copilot.css:1601-3, 1717-20, 1734-6)                             | web covers 1 of 3, approximately; desktop 0 of 3       | `ProjectsRoute.tsx:999` (background only; design also shifts border)           | ❌ inline `CSSProperties` structurally cannot express `:hover` — `ProjectsDestination.tsx:376,456-458`                     | n/a                                                                                                                           | n/a                                                                                                                                                                                                       | **GAP** — HOST_GAP, partial                                                                                                                                                                  |
| **F26** Responsive single-column collapse           | `<900px → 1fr`                                                              | web verbatim; desktop by a different mechanism         | ✅ `ProjectsRoute.tsx:974-976`                                                 | `CardGrid.tsx:31` auto-fill, no breakpoint                                                                                 | n/a                                                                                                                           | n/a                                                                                                                                                                                                       | UNVERIFIED — intermediate widths differ                                                                                                                                                      |
| **A01** No empty state                              | design has none                                                             | live has several, and web ≠ desktop copy               | `ProjectsRoute.tsx:834-840`                                                    | `ProjectsDestination.tsx:325-334`                                                                                          | `projects_routes.py:118`                                                                                                      | real                                                                                                                                                                                                      | EXTRA (INFO)                                                                                                                                                                                 |
| **A02** No loading / error state                    | design has none                                                             | full 4-state machines                                  | `ProjectsRoute.tsx:830-833,551-613,805-820`                                    | `ProjectsDestination.tsx:202-267`                                                                                          | typed errors `projects_routes.py:556,569`                                                                                     | typed 403/404 `routes.py:265,303`                                                                                                                                                                         | EXTRA (INFO)                                                                                                                                                                                 |
| **A03** No lifecycle actions                        | design has none                                                             | live has Star/Archive/Delete                           | `ProjectsRoute.tsx:905-949`                                                    | ❌ no callbacks passed → read-only grid                                                                                    | ⚠️ **no** `/archive` and **no** `/activate` route; star/unstar return 204                                                     | create/delete/restore/star/unstar real                                                                                                                                                                    | EXTRA (INFO) **+ 3 unverified live breakages** — Archive and Activate 404; star/unstar success path throws (typed `Promise<Project>` over a 204)                                             |
| **A04** No membership editing                       | design has none                                                             | ~1000 lines built, unreachable                         | Members tab only in the `team` profile, which no host sets                     | ❌                                                                                                                         | ✅ 5 real routes `projects_routes.py:249-335`; ⚠️ admin force-transfer is **commented out** at `:358`                         | real, ACL-gated                                                                                                                                                                                           | EXTRA (INFO) — dead UI                                                                                                                                                                       |
| **A05** No file actions                             | design has none                                                             | none built                                             | ❌                                                                             | ❌                                                                                                                         | ❌                                                                                                                            | ❌                                                                                                                                                                                                        | PARITY                                                                                                                                                                                       |
| **A06** No search / filter / sort                   | design has none                                                             | 4 filter chips that do nothing                         | list branch has no filter UI; detail branch renders inert chips                | inert — no `filter`/`onFilterChange` passed                                                                                | server-side filter/sort fully real and unused                                                                                 | `routes.py:204-274`                                                                                                                                                                                       | EXTRA (INFO) — inert UI; arguably remove rather than bind                                                                                                                                    |
| **A07** No pagination                               | scroll only                                                                 | scroll only, but truncates at 50                       | `ProjectsRoute.tsx:311-318` drops `next_cursor`                                | `destinationBinders.tsx:548-561` declares the field then drops it                                                          | cursor passes through                                                                                                         | keyset pagination real                                                                                                                                                                                    | PARITY vs design + latent truncation bug                                                                                                                                                     |
| **A08** No project status / owner / timestamp       | design has none                                                             | desktop ~5 chips, web 1                                | `ProjectsRoute.tsx:886-904`                                                    | `ProjectsDestination.tsx:481-562`                                                                                          | `projects_routes.py:118`                                                                                                      | `routes.py:849-880`                                                                                                                                                                                       | EXTRA (INFO) — **asymmetric between hosts**                                                                                                                                                  |
| **A09** No route / deep link for detail             | design has none                                                             | local state, matching                                  | `useState` `ProjectsRoute.tsx:289-291`                                         | n/a                                                                                                                        | server is deep-link ready                                                                                                     | `routes.py:281`                                                                                                                                                                                           | PARITY + a real neighbouring bug: desktop `ItemLink` label is the literal "Project" (`destinations/projects/index.ts:168`) because `cacheProjectNames` is web-only (`ProjectsRoute.tsx:302`) |
| **A10** No modal / drawer / toast                   | design has none                                                             | ~1140–3000 lines of dialogs mounted by nobody          | none mounted                                                                   | none mounted                                                                                                               | template routes real `projects_routes.py:423-518`                                                                             | ⚠️ project templates have **no Postgres adapter** — in-memory only (`app.py:2063-4`, `desktop_app.py:168`), lost on restart                                                                               | EXTRA (INFO) — dead UI; also: web Delete fires with **no confirmation**                                                                                                                      |
| **A11** No real-time affordance                     | static pill only                                                            | list-level SSE on web only                             | full SSE + reducer `ProjectsRoute.tsx:340-440,200-255`                         | ❌ one-shot fetch, never `transport.subscribe`                                                                             | real byte-for-byte SSE proxy `projects_routes.py:66-115`                                                                      | ⚠️ `InMemoryProjectActivityBus` is **process-local** (`projects/sse.py:167-190`) — lossy behind >1 web replica                                                                                            | EXTRA (INFO) + HOST_GAP (desktop never updates)                                                                                                                                              |

## Refuted — do not re-report these

- **F02 / F10.** "The desktop card name link is dead." It is **not**: the `project`
  ItemRef resolver self-registers at `destinations/projects/index.ts:166-174`, so a
  real `<a>` renders. The defect is that the route it emits (`{kind:"workspace"}`,
  `:171`) is one no host router handles.
- **F07.** "The subtitle cannot be fixed without a registry change." False —
  `topbarLeaf` is a public `ChatShell` prop (`ChatShell.tsx:97`) already exercised
  end-to-end (`ChatShell.test.tsx:221-226`).
- **F11.** "Desktop would have to reimplement the back affordance." Half wrong — the
  close _seam_ is already shared and unit-tested (`ProjectsDestination.tsx:127-131,313-325`;
  `ProjectsDestination.test.tsx:265-278`).
- **F12.** "The transfer trigger is design-absent chrome on web." It does **not**
  render in the solo profile — gated on `canManage` (`ProjectDetailView.tsx:471`),
  derived from `viewer_role === "owner"` (`ProjectsRoute.tsx:763`), which is null in solo.
- **F12.** "The detail title size drifts." It matches: `var(--font-size-xl)` = 18px
  (`design-system/src/styles.css:68`) == `copilot-app.jsx:356`. The real drift is the tile.
- **F15.** The project-lifecycle status pill (`ProjectDetailView.tsx:422-437`,
  `ProjectsDestination.tsx:481`) is present and tested. It is a different entity from
  chat run status.
- **F22.** "No doc glyph asset exists." It does (`icons/paths.tsx:52,184`) — this is
  unused-primitive drift, not a missing asset.
- **F04 / A01 / A02 / A05 / A07 / A09.** Live meets or exceeds the design. Not gaps.

---

# Part 3 — Remediation

Constraint honoured: **no bandaids, only architectural solutions.** Every item below
names the layer that should own the behaviour and the seam that makes it own it once.

Ordered by (user-visible impact × blast radius). **‖** marks work that can run in parallel.

## R1 — P0 · Collapse Projects onto ONE implementation in `chat-surface`

_Fixes RC-1, and unblocks every other RC. Not parallelizable — do it first._

**Owner layer:** `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx`
— the declared single source of truth for both hosts.

**The seam** (this is the whole fix, and it is one prop plus one binder):

1. Add `readonly onOpenProject?: (id: ProjectId) => void;` to `ProjectsDestinationProps`
   (`ProjectsDestination.tsx:94-135` — verified: the props already model
   `renderDetail` / `focusedProjectId` / `onCloseDetail`, so this is the missing
   sibling, not a new pattern). When supplied, the card body at `:456-462` renders as
   a `<button>` instead of an inert `<article>` — matching `copilot-app.jsx:396-401`.
2. **Web** (`apps/frontend/src/features/projects/ProjectsRoute.tsx`): delete the
   bespoke scaffold at `:840-960` **and** the entire `PROJECTS_GRID_CSS` string at
   `:965-1050`. Mount `<ProjectsDestination>` for the list, not only for the detail.
   The route keeps its data/mutation logic and becomes a pure binder.
3. **Desktop** (`apps/desktop/renderer/destinationBinders.tsx:563-568`): pass
   `onOpenProject`, `focusedProjectId`, `renderDetail`, `onCloseDetail`, the four
   lifecycle callbacks, and call `cacheProjectNames(result)` (currently web-only at
   `ProjectsRoute.tsx:302`, which is why every desktop `ItemLink` is labelled the
   literal "Project"). Remove the stale comment at `:542-544`.

**Do not** copy the web grid into desktop. The asymmetry exists precisely because
the web host forked the component; forking it again in the other direction doubles
the drift surface.

**Blast radius:** closes F02, F03, F10, F11, F12, F25 on desktop; collapses F01, A08
host asymmetry; makes RC-2/3/6/8 single-site fixes instead of four-site fixes.

## R2 ‖ — P0 · Complete the `_shared` primitive set and use it

_Fixes RC-5, RC-7, RC-9. Cross-surface: also repairs Activity, Chats, Skills._

`packages/chat-surface/src/destinations/_shared/` (verified by `ls`) ships
`PageLead`, `SectionHeader`, `RowList`, `Row` — and no `Page`. Three moves:

- **Add `_shared/Page.tsx`** (`max-width: 960px; padding: 20px 24px 40px`) and export it
  from `_shared/index.ts` next to the other four. Migrate `ProjectsRoute.tsx:796-802`
  and `ProjectDetailView.tsx:848-857` first, then the four conforming-but-divergent
  siblings (`ActivityDestination.tsx:611-620`, `ChatsArchive.tsx:147-156`,
  `SkillsDestination.tsx:108`, `RoutineDetail.tsx:1068`). This is the primitive whose
  absence caused `.pg` to be copy-pasted six times with five paddings.
- **Mount `PageLead`** in `ProjectsDestination`'s header region (above the FilterTabs
  at `:294-311`). The component and its tokens are already correct — this is one import
  and one copy string.
- **Migrate `SectionHeader.tsx:37-46` onto `.ui-mono-caps`.** The recipe already ships
  at `packages/design-system/src/styles.css:1098-1104` and is byte-for-byte the design
  rule (`--font-mono` + `--font-size-3xs` 9px + `--tracking-mono-caps` 0.12em + uppercase).
  This also deletes the last magic-number `letterSpacing: "0.12em"` that the
  `--tracking-*` scale was introduced to eliminate — the same cleanup the UI-kit
  consolidation applied everywhere else.

## R3 ‖ — P1 · Extract `_shared/ProjectIconTile.tsx`

_Fixes RC-2 (four divergent copies)._

New component in `packages/chat-surface/src/destinations/_shared/`, consumed by
`ProjectsDestination.tsx:394-404` and `ProjectDetailView.tsx:268-292`; after R1 the
web copy at `ProjectsRoute.tsx:866-876,1000-1013` is deleted rather than migrated.
Canonical geometry: 32×32 / radius 8 / 13px / weight 600 (copilot.css:1698-1710) —
`--radius-md` and the existing size ladder cover it, no new tokens needed.

**Requires a product decision, not a blind fix.** The design's rendered tile is
neutral only because `.proj-ic` overrides the fixture colour with `!important`.
The live per-project hue is almost certainly the better product. Decide once, encode
the answer in the shared component, and stop measuring it as drift either way.

## R4 — P1 · Route the project chat list through `_shared/RowList` + `_shared/Row`

_Fixes RC-3, F14–F18. **Blocked on R5** for the data; the component move can land first._

The row must move **into the package**, not stay in `apps/frontend`. Today
`ProjectDetailView.tsx:966` delegates the whole Chats section to the host via
`renderCrossDestinationTab`, and the only implementer is host-local markup at
`ProjectsRoute.tsx:639-670` — which is structurally why desktop can never share it.
`_shared/Row.tsx:102-115` already uses the correct `--font-size-2xs` +
`--color-text-subtle` + `--font-mono` triple, so adopting it fixes RC-6 for the row
family for free.

## R5 ‖ — P1 · Give conversations a real `project_id` (backend)

_Fixes F13, F15, F16, F17, F18, F19 in one move. Fully independent of all UI work._

**Owner layer:** `services/ai-backend`. `ConversationRecord`
(`runtime_api/schemas/conversations.py:114-175`) has no `project_id`; the create-time
value is written only into an audit-log `context` blob
(`agent_runtime/api/conversation_coordinator.py:173`). Add the column + a `project_id`
filter axis to `list_conversations` (`runtime_api/http/routes.py:113-130`), then a
facade passthrough (`backend_facade/app.py:410-415`, which today accepts only
`limit`/`include_archived`/`include_deleted`).

This is the correct architecture _instead of_ the `/v1/projects/{id}/activity`
endpoint the client currently calls: an activity log is a poor substitute for a
relation, and it is why a chat with no recent activity row would never appear even
if the route existed. Once the filter lands, the project detail can reuse the Chats
row wholesale — status pill, live-run treatment, model attribution and relative time
all already exist in `ChatsArchive.tsx:435-452,459-481` and become free.

## R6 ‖ — P1 · Build the counts projector, or delete the table

_Fixes F05, F13 count, F23. Independent of R5 and of all UI work._

`services/backend/src/backend_app/projects/schema.sql:175-176` names a projector at
`backend_app/projects/activity_projector.py` that does not exist. `upsert_counts`
(`store.py:604`, `:1115`) is not even part of the `ProjectStore` Protocol
(`store.py:326-328`) and has one caller in the entire repo — a live-DB test.
`append_activity` is orphaned the same way. Result: every card and every section
header reads `0` forever, and `members` — the one live number — is zeroed on every
write path (`routes.py:381,475,701,765`) and then whole-item-replaced into the web
list (`ProjectsRoute.tsx:242-243`).

Two honest options: **write the projector** (and add `upsert_counts` to the Protocol
so it cannot silently rot again), or **delete the denormalized table and derive
counts live**. What must not ship is a third state where the UI renders a number the
backend guarantees is wrong.

## R7 ‖ — P1 · Wire project files to the library, do not build a new endpoint

_Fixes RC-4, F20, F22, F23 rows. Independent._

`GET /v1/library?filter[project_id]=` **already works end to end** — backend
`library/routes.py:120` (+ ACL via `readable_project_ids`, `library/store.py:417-418`),
facade `library_routes.py:50`, client `libraryApi.ts:496-497`, already consumed by
`LibraryRoute.tsx`. Adding a parallel `/v1/projects/{id}/files` would be a second
source of truth for the same artifacts. Bind the existing one and pass `files` at
`ProjectsRoute.tsx:679`; `ProjectFilesTab`'s four-state machine
(`ProjectDetailView.tsx:567-690`) is already written for it. Supply the `doc` glyph
through the `library_file` ItemRef resolver (`destinations/library/index.ts:90-101`,
which hardcodes `icon: null`) using the existing `icons/paths.tsx:52,184` path —
one resolver change fixes the glyph on every surface at once.

## R8 — P2 · Retire the four bespoke sub-line style objects

_Fixes RC-6 wherever R4 does not already._

`--color-text-subtle: #64646d` (`design-system/src/styles.css:178`) is byte-identical
to the design's `--mut2`, and `--font-size-2xs` (11.2px, `:63`) is the nearest rung to
11px. **The tokens already exist and the live code is simply not using them.** Route
`ProjectsRoute.tsx:1020-1032`, `ProjectsDestination.tsx:414-428` and
`ProjectDetailView.tsx:441-452` through `Row`'s `sub`/`meta` slots rather than
re-declaring the triple a fourth time.

## R9 — P2 · Decide the accent-link policy, then encode it as a recipe

_Fixes RC-8, the styling half of F11._

`packages/chat-surface/src/refs/ItemLink.tsx:72` paints `var(--color-accent)` on
every ref link in every destination — the widest-blast-radius single line in this
audit. The design paints navigation targets in `--tx` and reserves accent for
elsewhere. For the backlink specifically the correct rule **already exists in the
tree**: `apps/frontend/src/styles.css:9042-9063` `.loginx-back` is a faithful port of
design `.backlink` (mono, 11px, `--color-text-muted`, hover → `--color-text`,
`margin-bottom: 14px`) with the comment "design .backlink" at `:9055`. Promote it to
a `.ui-backlink` recipe in `packages/design-system/src/styles.css` beside
`.ui-mono-caps`, and render it from a package-level back affordance so both hosts
inherit it.

## R10 — P2 · Stop styling interactive chrome with inline `CSSProperties`

_Fixes RC-25 / F25 at the root._

Hover is not missing on desktop by oversight — it is **structurally impossible**:
`ProjectsDestination.tsx:376,456-458` builds a `CSSProperties` object, and a style
object cannot express a pseudo-class. Same for the web backlink
(`ProjectsRoute.tsx:691-699`) and chat rows (`:658-666`). The architectural answer is
class recipes in `packages/design-system/src/styles.css` (`.ui-card--interactive`,
`.ui-row`) applied via `className`, matching how `.ui-button--primary:hover`
(`styles.css:468`) already works. Inline objects stay for pure layout only.

## R11 — P3 · Three one-line token decisions

- **`--font-size-body`.** `body` is 13.6px (`design-system/src/styles.css:356-378`)
  against the design's 13px, and the in-file comment already documents this as a
  conscious approximation because the rem ladder has no 13px rung. Either mint
  `--font-size-body: 0.8125rem` or accept it — but decide, because it silently
  offsets every inheriting element and inflates a third of the MEDIUM rows.
- **`--color-bg`.** `#09090b` (`:168`) vs the design's `#050506` (copilot.css:105).
  Affects every surface.
- **`--tracking-snug`.** Exists at `:87` (`-0.01em`); add it to
  `ProjectDetailView.tsx:333-341`.

## Do NOT change

- **Per-project accent colour (F04).** The design's neutral tile is an `!important`
  override of its own fixture data — very likely a mock leftover. Live colour is the
  better product; make it a decision, not a revert.
- **The lifecycle footer, filter tabs, status/owner/member pills, empty and error
  states (A01–A03, A06, A08).** Extra-in-live is real product capability, not drift.
  The design's card is a bare hit area because the mock had nothing to manage.
- **`ProjectMembersTab`, `ProjectEditor`, `TemplateGallery`/`TemplateEditor` and the
  three dialogs (A04, A10).** ~3,000 lines mounted by nobody, but backed by real,
  working facade + backend routes. **Mount them or flag them — do not delete them.**
  Deleting throws away implemented capability the server already serves.
- **The design's "Files · 12" over 4 rendered rows.** A fixture artifact
  (`copilot-app.jsx:369-371`), not a spec. Do not reproduce the inconsistency.
- **File-row interactivity (F21).** Live plans a clickable `ItemLink` where the design
  is deliberately inert. Decide before the endpoint lands; do not blindly make it inert.
- **The `<900px` collapse on web** (`ProjectsRoute.tsx:974-976`) — verbatim correct.

## Adjacent defects surfaced in passing (own tickets, not parity)

Not design drift, but found with hard evidence and worth filing:

1. **Archive and Activate 404 on web.** `projectsApi.ts:123-145` calls
   `POST /v1/projects/{id}/archive` and `/activate`; neither route exists on the
   facade or the backend (the real un-archive route is `/restore`). Of the four
   visible lifecycle buttons, only Delete works.
2. **Star/unstar success path throws.** The facade returns 204
   (`projects_routes.py:379-380,400-401`) but the client is typed `Promise<Project>`
   and immediately calls `toSummary(updated)` (`ProjectsRoute.tsx:526-529`).
3. **Project templates have no Postgres adapter** (`app.py:2063-2064`,
   `desktop_app.py:168`) — every saved template is lost on restart, though the table
   exists at `projects/schema.sql:307`.
4. **Admin force-transfer is commented out** at `projects_routes.py:358` with the
   handler body still live at `:359-375` as dead code.
5. **`GET /v1/agent/runs` does not exist** (`runtime_api/http/routes.py:634` is
   POST-only), so `liveness/service.py:111-117` — which raises on ≥400 — permanently
   errors. Breaks the archive-409 modal and any run-count consumer.
6. **Both hosts silently truncate at 50 projects**, discarding a `next_cursor` the
   backend returns (`ProjectsRoute.tsx:311-318`; `destinationBinders.tsx:548-561`
   declares the field then drops it).
7. **The projects SSE bus is process-local** (`projects/sse.py:167-190`) — correct for
   single-process desktop, silently lossy behind more than one web replica.
8. **Web Delete fires with no confirmation** (`ProjectsRoute.tsx:939-948`) while a
   built `archive-blocked-dialog.tsx` sits unmounted.

## Confidence

**High** on: RC-1 through RC-12 (each is a computed-style measurement plus a file I
opened); the 16 refuted-and-surviving feature gaps; and every "already exists in
`packages/design-system`" claim in Part 3 — `--color-text-subtle`, `--font-size-2xs`,
`--font-size-3xs`, `--tracking-snug`, `--tracking-mono-caps` and `.ui-mono-caps` were
all read directly out of `styles.css` for this report, as were `_shared/index.ts`,
`SectionHeader.tsx`, the `ProjectsDestinationProps` interface, the `showingDetail`
gate and the ProjectsBinder body.

**Medium** on the rows marked UNVERIFIED in Part 2 (F01, F08, F20, F24, F26, A03) —
lens consensus, not adversarially refuted. F20 and A03 in particular deserve a second
look before anyone acts on them; they are the two with the largest claimed impact.

**Low / not assessed:** anything in the "could not be measured" list — hover states,
`chip--warn`/`chip--off`, the desktop detail DOM (which does not exist), the
templates/members/editor surfaces (no design side), and all responsive behaviour
below 1440×900.
