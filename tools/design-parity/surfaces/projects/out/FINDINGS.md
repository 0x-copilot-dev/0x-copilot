# Projects — design-parity findings

Design baseline: vendored Claude Design app-v3 `ProjectsSurface`
(`design-kit/app-v3/copilot-app.jsx` + `copilot.css`), measured at 1440x900.
Live: the real shipping components, rendered by `lib/render-live-projects.test.tsx`.

Measured states and their reports:

| State                 | Live implementation                                                                   | Report                          | HIGH | MED | LOW |
| --------------------- | ------------------------------------------------------------------------------------- | ------------------------------- | ---- | --- | --- |
| `default`             | WEB host's own grid — `apps/frontend/src/features/projects/ProjectsRoute.tsx:840-960` | `report-default.md`             | 9    | 28  | 29  |
| `detail`              | `chat-surface` `ProjectDetailView` via the web host's `renderDetail` slot             | `report-detail.md`              | 26   | 42  | 48  |
| `default-chatsurface` | DESKTOP host's list — `chat-surface` `ProjectsDestination` CardGrid                   | `report-default-chatsurface.md` | 13   | 26  | 35  |

Raw totals: **HIGH 48 · MED 96 · LOW 112**. Those are per-property rows, not
distinct defects — they collapse into the **12 root causes** below. Read
"Measurement caveats" before quoting any single row: 11 of the 48 HIGH rows are
comparator artifacts, and totals are inflated by `default.page.container` being
re-measured in every state.

---

## RC-1 (HIGH) — Two hosts, two different Projects lists, neither is the design's

**anchors: every `default` vs `default-chatsurface` row (~60)**

The web host does NOT mount the shared destination for the list. It renders a
bespoke scaffold — `ProjectsRoute.tsx:840-960` with a scoped `<style>` string at
`ProjectsRoute.tsx:965-1050` — and mounts `<ProjectsDestination>` only once a
project is focused (`ProjectsRoute.tsx:820-829`). The desktop host mounts the
shared `<ProjectsDestination>` bare (`apps/desktop/renderer/destinationBinders.tsx:567`).
One design state, two live implementations that disagree with each other and with
the design:

| Property     | Design (`.card.proj-card`, copilot.css:737-742 + 1711-1716) | Web (`ProjectsRoute.tsx:977-999`)           | Desktop (`ProjectsDestination.tsx:378-387`) |
| ------------ | ----------------------------------------------------------- | ------------------------------------------- | ------------------------------------------- |
| element      | single `<button>` (whole card is the hit area)              | `<div>` + inner `<button>` + footer `<div>` | `<article>` (name link only)                |
| borderRadius | `var(--r)` = 8px                                            | 12px                                        | `var(--radius-md)` = 8px                    |
| padding      | `var(--pad)` = 13px                                         | 0 on card, `14px 14px 10px` on button       | 14px                                        |
| grid gap     | 10px (`.grid3`, copilot.css:1672-1682)                      | 12px (`ProjectsRoute.tsx:969`)              | 12px                                        |

Second-order HIGH: the desktop binder passes no `focusedProjectId` /
`renderDetail` (`destinationBinders.tsx:563-568`), and `ProjectsDestination.tsx:283`
gates the detail pane on both — so the entire `detail` design state is
**unreachable on desktop**. The component is built; the desktop binding is not.

Fix site: `apps/frontend/src/features/projects/ProjectsRoute.tsx:840-960` (delete
the bespoke grid, mount `<ProjectsDestination>` for the list too) and
`apps/desktop/renderer/destinationBinders.tsx:567` (pass the detail slot).
Everything below assumes this collapse to one implementation; fixing RC-2..RC-8
in two places otherwise re-drifts.

## RC-2 (HIGH) — The monogram tile exists in four divergent copies

**anchors: `default.card.icon` (5 props), `detail.icon` (7), desktop `default.card.icon` (5)**

There is no shared `ProjectIconTile`:

| Source                                                | size   | radius | font-size                 | weight  | colours                                                                              |
| ----------------------------------------------------- | ------ | ------ | ------------------------- | ------- | ------------------------------------------------------------------------------------ |
| Design `.proj-ic` (copilot.css:1698-1710)             | 32     | 8      | 13px                      | 600     | `--panel3` / `--tx2`, forced with `!important`                                       |
| Web card (`ProjectsRoute.tsx:1000-1013` + `:866-876`) | 32     | 8      | **14px**                  | **700** | inline `hsl(h 60% 28% / .45)` bg, `hsl(h 60% 50% / .55)` border, `hsl(h 70% 82%)` fg |
| Desktop card (`ProjectsDestination.tsx:394-404`)      | **28** | **6**  | `--font-size-lg` **16px** | 400     | solid `hsl(h 60% 28%)`, `--color-text`                                               |
| Detail header (`ProjectDetailView.tsx:268-292`)       | **44** | **10** | `--font-size-xl` **18px** | **700** | same hsl triple as the web card                                                      |

Size/radius/weight drift is unambiguous. The COLOUR difference needs a product
decision, not a blind fix: the design's JSX sets a per-project colour inline
(`copilot-app.jsx:403`) but `.proj-ic` overrides it with
`background: var(--panel3) !important; color: var(--tx2) !important`, so the
rendered design tile is neutral — the colour exists only in the fixture. Live
renders the colour. Decide whether the `!important` is intent or a mock leftover.

Fix site: extract one `ProjectIconTile` into
`packages/chat-surface/src/destinations/_shared/`, consumed by all three call sites.

## RC-3 (HIGH) — The detail chat list is hand-rolled; the design's row anatomy is absent

**anchors: 5 `missing-in-live` + 10 drift rows**

`ProjectsRoute.tsx:641-673` renders the project's chats as a bare
`<ul style={{listStyle:none,margin:0,padding:0}}>` of `<li style={{padding:"8px 0"}}>`
each containing one accent-coloured `<button>`. Measured consequences:

- `detail.chatrow.icon`, `.chip`, `.sub`, `.sub .mono`, `.time` → missing-in-live (5 HIGH)
- `detail.rowlist.chats` → background `--panel` → transparent, border 1px `--line` → 0, radius 8px → 0, `flex/column` → `block`
- `detail.chatrow` → padding `11px 14px` → `8px 0`, gap 12px → normal, `align-items:center` → normal, no hairline separator

The shared primitives that encode exactly this anatomy exist and are unused here:
`_shared/Row.tsx:35-51` (icon / chip / sub / meta slots, 28x28 icon box, 12.5px
title, mono meta) and `_shared/RowList.tsx:28-42` (1px `--color-border`,
`--radius-md`, `--color-surface` card + per-row hairlines).
`_shared/index.ts:1-3` states the intent verbatim: "The design row anatomy
(`.pg-lead` / `.sect-h` / `.rowlist` / `.lrow`) defined once, so Activity / Chats /
Projects can't drift."

Content-level too: the live row title is `a.preview` (`ProjectsRoute.tsx:670`)
where the design's title is the chat NAME with the preview on the sub-line
(`copilot-app.jsx:255-286`).

Fix site: `apps/frontend/src/features/projects/ProjectsRoute.tsx:641-673`.

## RC-4 (HIGH) — Project files have no backend, so the design's Files list cannot render

**anchors: `detail.filerow`, `.filerow.name`, `.filerow.sub` missing-in-live + `detail.rowlist.files` (4 props)**

`GET /v1/projects/{id}/files` does not exist. The facade enumerates every projects
route it proxies in `services/backend-facade/src/backend_facade/projects_routes.py:20-32`
(list, get, create, patch, delete, restore, members x4, transfer, star/unstar) —
no files route; a repo-wide grep for a projects-files handler in
`services/backend/src/backend_app/` returns nothing.

The client is honest about it: `ProjectsRoute.tsx:679-684` deliberately omits the
`files` prop and `ProjectDetailView.tsx:592-606` degrades to the "Project files
coming soon" `EmptyState` rather than a stuck skeleton. So this is a MISSING
CAPABILITY, not a CSS defect — but the design's Files section (bordered `.rowlist`
of `.lrow` rows with mono `.lrow__sub` meta) is unreachable until the endpoint
lands. Note the design's own Files header is internally inconsistent ("Files · 12"
from the fixture over 4 rendered rows, `copilot-app.jsx:369-371`), so do not treat
its row count as spec.

Fix site: `services/backend` + `services/backend-facade/src/backend_facade/projects_routes.py`
first; then pass `files` at `ProjectsRoute.tsx:679`.

## RC-5 (HIGH) — `.pg-lead` missing on both hosts although the shared primitive exists

**anchors: `default.page.lead` missing-in-live (web + desktop)**

The design opens with a 12px muted explainer (`.pg-lead`, copilot.css:1556-1562).
Neither host renders one. The shared component exists —
`packages/chat-surface/src/destinations/_shared/PageLead.tsx:22-28` — and sibling
destinations already use it (`ActivityDestination.tsx:315`, `ChatsArchive.tsx:300`).
A grep for `PageLead` across `packages/chat-surface/src` returns zero hits under
`destinations/projects/`. PageLead's own tokens are already right
(`--font-size-xs` 12.48px vs 12px; `--color-text-muted` == the design's `--mut`),
so this is a mount-it-and-write-the-copy fix.

Fix site: `apps/frontend/src/features/projects/ProjectsRoute.tsx:840` and the
header region of `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx`.

## RC-6 (MEDIUM) — `.lrow__sub` text: wrong colour rung, wrong size rung, mono dropped

**anchors: `default.card.desc` (2), `default.card.meta` (2), `detail.desc` (2), desktop card (5)**

Design `.lrow__sub` (copilot.css:1643-1648): 11px, `var(--mut2)` `#64646d`,
`var(--mono)` — with the DESCRIPTION overriding to body font inline while the
COUNTS line stays mono (`copilot-app.jsx:416-424`). That deliberate mono/system
pairing inside one card is lost everywhere:

| Live site                                            | size                     | colour                               | family     |
| ---------------------------------------------------- | ------------------------ | ------------------------------------ | ---------- |
| `ProjectsRoute.tsx:1020-1027` `.projects-card__desc` | 12px literal             | `--color-text-muted` `#98989f` WRONG | sans       |
| `ProjectsRoute.tsx:1028-1032` `.projects-card__meta` | 12px literal             | `--color-text-subtle` OK             | sans WRONG |
| `ProjectsDestination.tsx:414-428` desc + meta        | `--font-size-xs` 12.48px | `--color-text-muted` WRONG           | sans WRONG |
| `ProjectDetailView.tsx:441-452` description          | `--font-size-sm` 13.6px  | `--color-text-muted` WRONG           | sans       |

The correct rungs exist: `--color-text-subtle: #64646d`
(`packages/design-system/src/styles.css:178`) is byte-identical to the design's
`--mut2`, and `--font-size-2xs: 0.7rem` (11.2px, `:64`) is nearest to 11px.

Fix site: the four style objects above; ideally by routing them through
`_shared/Row.tsx`'s `sub`/`meta` slots (`Row.tsx:102-115` already uses
`--font-size-2xs` + `--color-text-subtle` + `--font-mono` — the right values, in
the component Projects doesn't use).

## RC-7 (MEDIUM, cross-surface) — `SectionHeader` hand-rolls the mono micro-label instead of the shipped `.ui-mono-caps` recipe

**anchors: `detail.secth.chats` + `detail.secth.files` (4 props)**

`packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:38-46` declares
`fontSize: var(--font-size-2xs)` (11.2px), `fontWeight: var(--font-weight-semibold)`
(600) and a LITERAL `letterSpacing: "0.12em"`. Design `.sect-h`
(copilot.css:1563-1573) is 9.5px, weight 400, .12em, colour `--mut2`, margin
`22px 0 10px`.

The canonical recipe for exactly this label shipped in the UI-kit consolidation:
`packages/design-system/src/styles.css:1097-1104` `.ui-mono-caps` =
`var(--font-mono)` + `var(--font-size-3xs)` (9px) + `var(--tracking-mono-caps)`
(0.12em) + uppercase. SectionHeader predates it and never migrated, so it also
keeps a magic-number tracking that the `--tracking-*` scale was introduced to
eliminate. Because SectionHeader is shared, this drift is NOT Projects-only —
Activity, Chats and every other `.sect-h` inherit it.

Fix site: `packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:38-46`.

## RC-8 (MEDIUM) — Navigation controls styled as accent hyperlinks; the design uses quiet chrome

**anchors: `detail.backlink` (8 props), `detail.chatrow.name` colour, desktop `default.card.name.link` colour**

| Control           | Live                                                                                              | Design                                                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| "<- All projects" | `ProjectsRoute.tsx:686-701`: 13px SANS, weight 600, `--color-accent`, no icon, `padding:0 0 12px` | `.backlink` copilot.css:1721-1739: inline-flex, gap 6, MONO 11px, `--mut`, 13x13 svg, `margin-bottom:14px`, hover -> `--tx` |
| chat row title    | `ProjectsRoute.tsx:653-668`: `--color-accent`                                                     | `.lrow__name` copilot.css:1635-1642: 12.5px/500 `--tx`                                                                      |
| desktop card name | `ItemLink.tsx:69-76`: `color: var(--color-accent)`                                                | 14px/600 `--tx` (`copilot-app.jsx:406-412`)                                                                                 |

The design never paints a Projects navigation target in the accent colour. The
`ItemLink` case has the widest blast radius (every destination rendering an
`ItemLink` inherits it).

Fix site: `ProjectsRoute.tsx:689-699`, `ProjectsRoute.tsx:658-666`, and a decision
on `packages/chat-surface/src/refs/ItemLink.tsx:72`.

## RC-9 (MEDIUM) — No shared `.pg` page shell; Projects skips the 960px content column

**anchors: `default.page.container` padding / width / flexGrow, in all three states**

Design `.pg` (copilot.css:1552-1555): `padding: 20px 24px 40px; max-width: 960px`.

- Web list: `ProjectsRoute.tsx:796-802` — `padding: 24`, NO max-width -> measured 1040px vs 960.
- Detail: `ProjectDetailView.tsx:848-857` — `maxWidth: 1000`, `padding: "24px 28px 48px"`.
- Conforming siblings: `ActivityDestination.tsx:611-620` (960, `16px 20px 32px`),
  `ChatsArchive.tsx:147-156` (960, `24px 28px 96px`), `SkillsDestination.tsx:108`,
  `RoutineDetail.tsx:1068`.

`.pg` is copy-pasted six times with five different paddings, none equal to the
design's. `_shared/` ships PageLead / SectionHeader / RowList / Row but no `Page`
— the one primitive that would have prevented this.

Fix site: add `_shared/Page.tsx` (`max-width:960; padding:20px 24px 40px`) and
migrate the six call sites, starting with `ProjectsRoute.tsx:796-802` and
`ProjectDetailView.tsx:848-857`.

## RC-10 (MEDIUM, cross-surface) — Base body size is 13.6px, not 13px

**anchors: every `+0.6px` row — page.container, grid, card, card.hitarea, rowlist.chats, rowlist.files, chatrow (9 rows across 3 states)**

`packages/design-system/src/styles.css:356-378` sets
`body { font-size: var(--font-size-sm) }` = `0.85rem` = 13.6px. The design's body
is a literal 13px (`copilot.css:105-112`). The in-file comment already documents
the trade: the rem ladder has no 13px rung, so `--font-size-sm` was reused "rather
than mint a new value or an ad-hoc px". This is a KNOWN, DELIBERATE approximation,
not a defect — but it is why a third of the MEDIUM rows exist and it silently
offsets every element that inherits rather than declares a size. Decide once: mint
`--font-size-body: 0.8125rem` (13px), or accept it and stop counting it as drift.

## RC-11 (LOW, cross-surface) — App canvas is `#09090b`; the design's is `#050506`

**anchor: desktop `default.page.container` backgroundColor**

`--color-bg: #09090b` (`packages/design-system/src/styles.css:168`) vs
`body { background: #050506 }` (`copilot.css:105`). Only surfaced on the
`default-chatsurface` state because the chat-surface destination root paints
`--color-bg` explicitly (`ProjectDetailView.tsx:841`) while the design's `.pg` is
transparent over `body`. Affects every surface.

## RC-12 (LOW) — Detail title drops the design's -0.01em tracking

**anchor: `detail.title` letterSpacing `-0.18px` -> `normal`**

`ProjectDetailView.tsx:333-341` sets `--font-size-xl` + weight 600 (both correct)
but no `letterSpacing`; the design's global `h1-h4` rule (`copilot.css:113-121`)
applies `-0.01em`. The token exists: `--tracking-snug: -0.01em`
(`packages/design-system/src/styles.css:87`).

---

## Live capability with no design counterpart — do NOT delete

Reported as `extra-in-live`, not drift:

- Per-card lifecycle footer — Star / Archive / Delete (`ProjectsRoute.tsx:905-950`,
  `.projects-card__actions` at `:1041`) and the member role chip (`:895-903`).
  The design's card is a single hit area with no actions.
- Filter tabs (all / active / archived / starred) + counts, page header + item
  count, status pill, owner + member-count pills (`ProjectDetailView.tsx:389-460`,
  `ProjectsDestination.tsx` header). The design's rail already labels the screen.
- Eight surfaces with no `?dest=projects` counterpart, so nothing to diff:
  `TemplateGallery`, `TemplateEditor`, `ProjectEditor`, `ProjectMembersTab`,
  `ProjectActivityTab`, `transfer-ownership-dialog`, `fork-from-template-dialog`,
  `archive-blocked-dialog` — all backed by real facade routes
  (`projects_routes.py:20-32`, `/v1/project-templates*`).

## Design capability missing from live

- `.pg-lead` explainer (RC-5)
- The whole chat-row anatomy: icon slot, status chip, preview+model sub-line, mono relative time (RC-3)
- The Files rowlist (RC-4 — blocked on a backend endpoint)
- The project detail view ON DESKTOP (RC-1)

---

## Measurement caveats — read before quoting a row

1. **11 of the 48 HIGH rows are comparator artifacts.** When an element has
   `border-style: none`, `getComputedStyle().borderColor` returns `currentColor`,
   so a `borderColor` row appears that merely restates the adjacent `color` row.
   Affected: `default.card.hitarea`, `default.card.desc`, `detail.desc`,
   `detail.backlink`, `detail.chatrow.name`, `detail.rowlist.chats`,
   `detail.rowlist.files`, `detail.chatrow`, and desktop `card.name.link` / `.desc`
   / `.meta`. Fix the colour and the borderColor row disappears with it.
2. **`default.card.hitarea backgroundColor --panel -> transparent` is structural,
   not a colour bug.** The live card paints `--color-surface` (`#111114`, identical
   to the design's `--panel`) on the wrapper `div` while the inner `button` is
   transparent; `default.card backgroundColor` did NOT drift. The surface is
   correct — only the element carrying it moved (RC-1).
3. **`detail.secth` text "Chats · 3" -> "Chats" is an anchor artifact.** The count
   IS rendered, in a sibling `[data-testid="section-header-count"]`
   (`SectionHeader.tsx:76-84`); the anchor targets the `<h2>` label only.
4. **Neither side actually loads JetBrains Mono.** The design declares the family
   with no `@font-face` (`copilot.css:39`); the live `ds.css:11-20` `@font-face`
   404s in this harness. Both fall back to system monospace, so mono METRICS are
   symmetric and the `fontFamily` STRING comparison (mono vs sans) is still valid.
5. **`default.page.container` is re-measured in every state**, so its 4 MEDIUM +
   4 LOW rows are triple-counted in the raw totals.

## What could not be measured, and why

- **Hover / focus / active states** — `.card.proj-card:hover` (copilot.css:1717-1720),
  `.lrow:hover`, `.backlink:hover`. The extractor reads static computed styles only.
- **`chip--warn` / `chip--off`** — the Launch Week fixture exercises only
  `chip--ok`, and the live side has no chip at all (RC-3): nothing on either side.
- **The desktop project detail** — unreachable (RC-1); no live DOM exists.
- **Templates / members / activity / editor / dialog surfaces** — the design
  harness exposes only `?dest=projects&state=default|detail`; no design side.
- **The <900px single-column collapse** (copilot.css:1678-1682 vs
  `ProjectsRoute.tsx:974-976`) — everything was measured at 1440x900 only. Both
  declare the same breakpoint, but that is a code read, not a measurement.

## Reproduce

```bash
cd tools/design-parity && (python3 -m http.server 8112 --bind 127.0.0.1 &)
# design side (use surfaces/projects/anchors-desktop.json for the desktop state)
node lib/extract-playwright.mjs --url "http://127.0.0.1:8112/design-kit/app-v3/index.html?dest=projects&state=default" \
  --anchors surfaces/projects/anchors.json --side design --delay 1500 --out surfaces/projects/out/design-default.json
node lib/extract-playwright.mjs --url "http://127.0.0.1:8112/surfaces/projects/live/default.html" \
  --anchors surfaces/projects/anchors.json --side live --out surfaces/projects/out/live-default.json
node lib/compare.mjs surfaces/projects/out/design-default.json surfaces/projects/out/live-default.json \
  --anchors surfaces/projects/anchors.json --out surfaces/projects/out/report-default.md --state default
```

Verified reproducible: a clean re-run of all three states produced reports
content-identical to the committed ones (only prettier's table-separator padding
differs).

No vendored design file contained text addressed to the agent.
