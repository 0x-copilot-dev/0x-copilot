# PRD-10 — Projects surface: one list implementation, desktop detail, page chrome

> **Wave 4** (`README.md` — _Corrected implementation order_). Lands after PRD-03, PRD-07
> and PRD-08; PRD-13 lands after this one and computes its orphan-waiver list against the
> post-PRD-10 tree. Ownership of the hot files, per the README's serialisation table:
> `ProjectsDestination.tsx` **02 → 03 → 07 → 10 owns**; `ProjectDetailView.tsx`
> **07 → 10 owns the markup** (C16); `packages/design-system/src/styles.css`
> **01 → 02 → 08 → 11 → 10** (PRD-10 is the last writer).

## Problem

Open Projects on the web app and on the desktop app side by side and you are looking at two
different products. The web grid has 12px-radius cards with a Star / Archive / Delete footer
strip and a coloured monogram tile. The desktop grid has 8px-radius cards with no footer, no
role chip, and a tile that is the same 📁 folder emoji on every single row — because the
server stamps `icon_emoji` with a `DEFAULT '📁'` and the desktop card renders that field with
no fallback. Neither grid matches the mock: the design's card is one 8px-radius button with
13px padding in a 10px-gap 3-column grid, and its tile is a 32px monogram of the project's
first letter.

Click a project on desktop and nothing happens — there is no project detail view at all. The
component exists, is tested, and is unreachable, because the desktop binder never passes the
two props that gate it.

On web, clicking through _does_ work, and the detail page is where the drift is worst. The
back control is a 13px semibold accent-blue sans button reading "← All projects" where the
design draws a quiet 11px mono link with a 13×13 chevron. The identity tile jumps from the
32px it should be to 44px. The Chats list is a bare `<ul>` of accent-blue text buttons — no
icon, no status chip, no preview sub-line, no timestamp — sitting inside no card at all,
while the shared `RowList`/`Row` primitives that encode exactly that anatomy sit unused two
directories away. The Files section says "coming soon".

Above all of it, both hosts paint a 22px "Projects" page title that the design does not have
— the window topbar already says "Projects · group chats, files & context" — and neither
host renders the 12px muted lead paragraph that the design opens with. The shared `PageLead`
component that exists for exactly this, and whose own header comment says _"the rail already
labels the screen, so there is NO 22px page title"_, is mounted by Chats and Activity and not
by Projects.

## Evidence

Every row opened and verified in this working tree at `claude/design-parity-audit-7ec82a`.

| Claim                                                                  | File:line                                                                                                                                                                                                                                                               | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web renders its own grid markup, not the shared destination            | `apps/frontend/src/features/projects/ProjectsRoute.tsx:843-844`, `:965-1050`                                                                                                                                                                                            | CONFIRMED. `<style>{PROJECTS_GRID_CSS}</style>` + `<div className="projects-grid3">`, backed by an 86-line scoped CSS string starting at `:965`.                                                                                                                                                                                                                                                                                                                                                                            |
| Web mounts `ProjectsDestination` only when a project is focused        | `apps/frontend/src/features/projects/ProjectsRoute.tsx:821-829`                                                                                                                                                                                                         | CONFIRMED. `focusedProjectId !== null ? <ProjectsDestination …/> : …` — the destination is used purely as a `renderDetail` host.                                                                                                                                                                                                                                                                                                                                                                                            |
| Desktop renders the shared `CardGrid` destination                      | `apps/desktop/renderer/destinationBinders.tsx:563-567`                                                                                                                                                                                                                  | CONFIRMED. `<ProjectsDestination items={result} onRetry={retry} />` — no `focusedProjectId`, no `renderDetail`, no filter/create/star/archive callbacks.                                                                                                                                                                                                                                                                                                                                                                    |
| The documented reason for the web fork is STALE                        | `ProjectsRoute.tsx:18-23` vs `ProjectsRoute.tsx:302` and `packages/chat-surface/src/destinations/projects/index.ts:168`                                                                                                                                                 | CONFIRMED STALE. The comment says the scaffold exists "because the card name is a `<ItemLink kind=\"project\">` whose stub resolver renders the literal label \"Project\"". The **same file** primes the cache at `:302` (`cacheProjectNames(state.items)`) and the resolver reads `getCachedProjectName(id) ?? "Project"`. The stub was fixed; the fork was not removed.                                                                                                                                                   |
| Desktop never primes the name cache                                    | `grep -rn cacheProjectNames apps/desktop` → no hits; only `ProjectsRoute.tsx:52,302`                                                                                                                                                                                    | CONFIRMED. So on desktop the resolver _does_ still fall back to "Project" — the stale reason is true on the host that does not use the scaffold, and false on the host that does. PRD-03 owns the fix.                                                                                                                                                                                                                                                                                                                      |
| Detail pane is gated on both props                                     | `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx:283`                                                                                                                                                                                           | CONFIRMED. `const showingDetail = renderDetail !== undefined && focusedProjectId !== null;` → desktop can never reach the detail branch at `:314-322`.                                                                                                                                                                                                                                                                                                                                                                      |
| Desktop card renders `icon_emoji` with no fallback                     | `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx:469`                                                                                                                                                                                           | CONFIRMED. `{project.icon_emoji}` and nothing else.                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `icon_emoji` is server-defaulted to 📁 for every project               | `services/backend/migrations/0043_projects.sql:39`; `src/backend_app/projects/schema.sql:39`; `projects/store.py:94`; `projects/service.py:366`, `:1117`                                                                                                                | CONFIRMED at four layers. `icon_emoji TEXT NOT NULL DEFAULT '📁'` in both the migration and the shipped `schema.sql`; `icon_emoji: str = "📁"` on the store record; `icon_emoji=validated.get("icon_emoji", "📁")` in the service create path. So the desktop tile is identical on every card unless the user edited it — and no host ships an editor (below).                                                                                                                                                              |
| Detail tile already renders the initial, deliberately                  | `packages/chat-surface/src/destinations/projects/ProjectDetailView.tsx:258-268`                                                                                                                                                                                         | CONFIRMED, and the comment says so: "the header tile is the project colour + the name's first letter — NOT the emoji".                                                                                                                                                                                                                                                                                                                                                                                                      |
| "THREE separate per-project hue-ramp implementations"                  | see next four rows                                                                                                                                                                                                                                                      | **DISPUTED — the code is worse.** There are **four distinct ramp formulas across seven call sites** in `destinations/projects/` alone, plus a fifth in `destinations/agents/`.                                                                                                                                                                                                                                                                                                                                              |
| ramp A — alpha triple                                                  | `ProjectsRoute.tsx:873-875`; `ProjectDetailView.tsx:271-278`                                                                                                                                                                                                            | CONFIRMED. bg `hsl(h 60% 28% / 0.45)`, border `1px solid hsl(h 60% 50% / 0.55)`, fg `hsl(h 70% 82%)`.                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ramp B — solid, no border                                              | `ProjectsDestination.tsx:398`                                                                                                                                                                                                                                           | CONFIRMED. bg `hsl(h, 60%, 28%)`, fg `var(--color-text)`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ramp C — 55/35                                                         | `ProjectEditor.tsx:282`; `TemplateGallery.tsx:154`; `TemplateEditor.tsx:170`; `fork-from-template-dialog.tsx:261`                                                                                                                                                       | CONFIRMED. bg `hsl(h, 55%, 35%)` in all four.                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ramp D — light                                                         | `packages/chat-surface/src/destinations/agents/AgentDetailView.tsx:122`                                                                                                                                                                                                 | CONFIRMED. `hsl(h, 60%, 90%)`. Out of this PRD's scope but proves the pattern generalises.                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Tile geometry diverges three ways                                      | `ProjectsRoute.tsx:1000-1008`; `ProjectsDestination.tsx:394-403`; `ProjectDetailView.tsx:280-289`                                                                                                                                                                       | CONFIRMED. Web card 32/8px-radius/**14px**/**700**; desktop card **28**/**6**/`--font-size-lg` (**16px**)/400; detail **44**/**10**/`--font-size-xl` (**18px**)/**700**. Design is 32/8/13px/600 everywhere.                                                                                                                                                                                                                                                                                                                |
| The design forcibly neutralises the tile with `!important`             | `tools/design-parity/design-kit/app-v3/copilot.css:1698-1710`                                                                                                                                                                                                           | CONFIRMED. `.proj-ic{…background:var(--panel3)!important;color:var(--tx2)!important}` overriding the inline `style={{background:p.color}}` set at `copilot-app.jsx:353` and `:403`.                                                                                                                                                                                                                                                                                                                                         |
| Design-absent 22px PageHeader on both states                           | `ProjectsDestination.tsx:295-303`; `packages/chat-surface/src/shell/PageHeader.tsx:49`                                                                                                                                                                                  | CONFIRMED. `<PageHeader title="Projects" subtitle=… />` with `fontSize: "var(--font-size-2xl, 22px)"` (`--font-size-2xl` = 1.4rem = 22.4px, `packages/design-system/src/styles.css:69`). Rendered in the loading (`:211`), error (`:232`), unavailable (`:256`) and ready (`:295`) branches.                                                                                                                                                                                                                                |
| The topbar already carries the title + subtitle                        | `tools/design-parity/design-kit/app-v3/copilot-app.jsx:600`, `:739`, `:817-818`                                                                                                                                                                                         | CONFIRMED. `TITLES.projects = ["Projects", "group chats, files & context"]`, rendered by the `.topbar` when `dest !== "workspace" && dest !== "settings"`.                                                                                                                                                                                                                                                                                                                                                                  |
| `PageLead` exists, is used by siblings, and is unused by Projects      | `_shared/PageLead.tsx:1-45`; `ChatsArchive.tsx:300`; `ActivityDestination.tsx:315`; `grep PageLead destinations/projects` → 0                                                                                                                                           | CONFIRMED. Its header comment at `PageLead.tsx:4-5` states the decision verbatim: "the rail already labels the screen, so there is NO 22px page title (README decision 1)".                                                                                                                                                                                                                                                                                                                                                 |
| Back-link is an accent 13px semibold sans button, host-owned           | `apps/frontend/src/features/projects/ProjectsRoute.tsx:686-701`                                                                                                                                                                                                         | CONFIRMED. `color: "var(--color-accent)"`, `fontSize: 13`, `fontWeight: 600`, `padding: "0 0 12px"`, text `← All projects`, no svg. It lives in the **host**, so desktop would have to re-hand-roll it.                                                                                                                                                                                                                                                                                                                     |
| Measured: back-link drift                                              | `tools/design-parity/surfaces/projects/out/report-detail.md:15-18`                                                                                                                                                                                                      | CONFIRMED. `fontFamily mono → sans`, `fontSize 11px → 13px`, `color rgb(152,152,159) → rgb(95,178,236)`.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Detail chat list is a bare `<ul>` of accent text buttons               | `apps/frontend/src/features/projects/ProjectsRoute.tsx:641-673`                                                                                                                                                                                                         | CONFIRMED. `<ul style={{listStyle:"none",margin:0,padding:0}}>` → `<li style={{padding:"8px 0"}}>` → `<button … color:"var(--color-accent)", fontSize:13>` whose label is `a.preview`, i.e. the preview text is used as the **title**.                                                                                                                                                                                                                                                                                      |
| `Row` / `RowList` encode the design anatomy and are unused here        | `_shared/Row.tsx:1-24`, `:35-51`; `_shared/RowList.tsx:28-42`; `_shared/index.ts:1-3`                                                                                                                                                                                   | CONFIRMED. `index.ts:2-3` states the intent verbatim: "The design row anatomy (`.pg-lead` / `.sect-h` / `.rowlist` / `.lrow`) defined once, so Activity / Chats / Projects can't drift."                                                                                                                                                                                                                                                                                                                                    |
| No `GET /v1/projects/{id}/files` anywhere                              | `services/backend-facade/src/backend_facade/projects_routes.py:118-441`; `grep files services/backend/src/backend_app/projects/routes.py` → 0 hits                                                                                                                      | CONFIRMED. The facade enumerates list/get/create/patch/delete/restore/members×4/transfer/star/unstar and the template routes; no files route on either side. **PRD-07 owns this**; PRD-10 only binds the result.                                                                                                                                                                                                                                                                                                            |
| `.pg` page shell is copy-pasted with divergent padding                 | `ProjectsRoute.tsx:796-802` (`padding: 24`, no max-width); `ProjectsDestination.tsx:190-199` and `ProjectDetailView.tsx:848-857` (both `maxWidth: 1000`, `padding: "24px 28px 48px"`); `ActivityDestination.tsx:611-615` (`maxWidth: 960`, `padding: "16px 20px 32px"`) | CONFIRMED. Four call sites, three different geometries, none equal to the design's `padding: 20px 24px 40px; max-width: 960px`.                                                                                                                                                                                                                                                                                                                                                                                             |
| Detail title drops the design's tracking                               | `ProjectDetailView.tsx:333-335`                                                                                                                                                                                                                                         | CONFIRMED. `fontSize: "var(--font-size-xl)"` (1.125rem = 18px — correct vs the design's `h2 style={{fontSize:18}}`), `fontWeight: 600`, and **no** `letterSpacing`, where `copilot.css:113-121` applies `-0.01em` to `h1-h4`. `--tracking-snug: -0.01em` exists at `styles.css:87`.                                                                                                                                                                                                                                         |
| `CardGrid` is `auto-fill`, the design is a fixed 3-up                  | `packages/chat-surface/src/shell/CardGrid.tsx:25-32`                                                                                                                                                                                                                    | CONFIRMED. `repeat(auto-fill, minmax(260px, 1fr))`, `gap = 12`. Design `.grid3` is `repeat(3, 1fr)`, `gap: 10px`, collapsing to `1fr` under `@media (max-width: 900px)` (`copilot.css:1672-1682`). A `minmax` grid cannot express a 3→1 collapse with no 2-up stop.                                                                                                                                                                                                                                                         |
| `ProjectDetailView` defaults `profile` to `"solo"`; no host passes it  | `ProjectDetailView.tsx:810`; `grep -rn "profile=" apps/frontend apps/desktop` → 0 hits on this component                                                                                                                                                                | CONFIRMED. `profile = "solo"` is a prop default, not a binding. The eight-tab team model at `:998-1000` is therefore dead in both hosts.                                                                                                                                                                                                                                                                                                                                                                                    |
| A `DeploymentProfile` port already exists and is used elsewhere        | `packages/chat-surface/src/providers/DeploymentProfileProvider.tsx:15-20`; `settings/SettingsSurface.tsx:263`; `shell/ChatShell.tsx:162`                                                                                                                                | CONFIRMED. `type DeploymentProfile = "single_user_desktop" \| "team"`, consumed via `useDeploymentProfile()` / `useOptionalDeploymentProfile()`. The Projects detail is the one team-gated surface that ignores it.                                                                                                                                                                                                                                                                                                         |
| "TemplateGallery / TemplateEditor / fork dialog mounted by no host"    | `packages/chat-surface/src/index.ts:530-550`; `apps/frontend/src/app/App.tsx:129-131`, `:942-958`; `apps/frontend/src/features/project-templates/TemplateGalleryRoute.tsx`                                                                                              | **DISPUTED, and worse than stated.** The package components are not merely unmounted — they are **not exported** from `chat-surface/src/index.ts` (only `ProjectFilterChip`, `ProjectsDestination`, `ProjectsPanel` and the name-cache helpers are). Meanwhile web ships a **parallel 403-line hand-rolled** `TemplateGalleryRoute`, lazily routed at `App.tsx:129-131` and mounted at `:942-958`. This is the same fork as the list, one directory over.                                                                   |
| "ProjectFilterChip mounted by no host"                                 | `packages/chat-surface/src/destinations/library/LibraryPanel.tsx:189`; `SaveToLibraryPopover.tsx:390`                                                                                                                                                                   | **DISPUTED.** It is live, in two Library surfaces, and it renders `icon_emoji` at `ProjectFilterChip.tsx:279, 329, 346, 363`. It is not dead and must not be deleted.                                                                                                                                                                                                                                                                                                                                                       |
| `ProjectEditor` / `transfer-ownership` / `archive-blocked` unreachable | `grep -rn "ProjectEditor\|TransferOwnership\|ArchiveBlocked" packages apps` outside `destinations/projects/` → 0                                                                                                                                                        | CONFIRMED. Not exported, no consumer. `ProjectDetailView` exposes only an `onRequestTransferOwnership` **callback** (`:214`) — the dialog itself is never rendered by anyone.                                                                                                                                                                                                                                                                                                                                               |
| `ProjectsPanel` exported, zero consumers                               | `packages/chat-surface/src/index.ts:533`; `ProjectsPanel.tsx:1-19`                                                                                                                                                                                                      | CONFIRMED. Its own header says it ships "#1, #6 from the 6-section spec" and lists four unimplemented sections. Its two features (status filter, New-project CTA) are both already on `ProjectsDestination`.                                                                                                                                                                                                                                                                                                                |
| Live has a create affordance the design lacks — and nobody wires it    | `ProjectsDestination.tsx:295-303`, `:349-354`; `ProjectsBinder` at `destinationBinders.tsx:567`; `ProjectsRoute.tsx:821-829`                                                                                                                                            | CONFIRMED **and it is inert**. `onCreateProject` is an optional prop; neither host passes it, so the "New project" button never renders. The design's `ProjectsSurface` (`copilot-app.jsx:386-425`) has no create control at all.                                                                                                                                                                                                                                                                                           |
| Measured HIGH counts                                                   | `report-default.md:8`, `report-detail.md:8`, `report-default-chatsurface.md:8` vs `FINDINGS.md:12-14`                                                                                                                                                                   | **DISPUTED — FINDINGS.md is stale; the reports win.** The committed reports read `HIGH 8 · MEDIUM 28` (default), `HIGH 23 · MEDIUM 42` (detail), `HIGH 9 · MEDIUM 26` (desktop list). `FINDINGS.md`'s table still says 9 / 26 / 13 HIGH — it predates the harness fix that stopped `lib/compare.mjs` emitting phantom `borderColor` HIGH rows for borderless elements. **No DoD item in this PRD may quote any of these six numbers**; the gate items below are expressed as absolute-zero targets plus a merge-base delta. |

## Design intent

Literal values from `tools/design-parity/design-kit/app-v3/`.

**Page shell** — `copilot.css:1552-1562`:

```css
.pg {
  padding: 20px 24px 40px;
  max-width: 960px;
}
.pg-lead {
  font-size: 12px;
  color: var(--mut);
  margin: -2px 0 18px;
  max-width: 72ch;
  line-height: 1.6;
}
```

`--mut: #98989f` (`copilot.css:18`) is byte-identical to `--color-text-muted` (`packages/design-system/src/styles.css:177`).

**List** — `copilot-app.jsx:386-425`. No page title, no filter tabs, no create button. The
surface opens with the lead paragraph:

> "Group related chats, files, and context. Open a project to see its conversations and
> working files."

then `<div className="grid3">` of `<button className="card proj-card">`. Grid
(`copilot.css:1672-1682`): `repeat(3, 1fr)`, `gap: 10px`, `@media (max-width:900px) → 1fr`.
Card = `.card` (`copilot.css:737-742`) `background: var(--panel)` `#111114`,
`border: 1px solid var(--line)`, `border-radius: var(--r)` = **8px** (`:40`),
`padding: var(--pad)` = **13px** (`:43`) — plus `.card.proj-card` (`:1711-1716`)
`cursor:pointer; text-align:left; font:inherit; color:inherit`. Hover
(`:1717-1720`): `border-color: var(--line2); background: var(--panel2)`.

Card contents, in order: a flex row (`gap:12, align-items:center`) of `.proj-ic` + a
`fontFamily:var(--disp); fontWeight:600; fontSize:14` name (`copilot-app.jsx:402-414`); then
the description as `.lrow__sub` with `fontFamily:var(--body)` and `marginTop:10`
(`:416-421`); then the counts as a plain `.lrow__sub` with `marginTop:10` (`:422+`) — i.e.
**mono for the counts, body font for the description**, inside the same card.

`.lrow__sub` (`copilot.css:1643-1648`): `font-size: 11px; color: var(--mut2); margin-top: 1px;
font-family: var(--mono)`. `--mut2: #64646d` (`:19`) is byte-identical to
`--color-text-subtle` (`styles.css:178`).

**Identity tile** — `copilot.css:1698-1710`:

```css
.proj-ic {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  font-weight: 600;
  flex: none;
  font-size: 13px;
  background: var(--panel3) !important; /* #1d1d23 */
  color: var(--tx2) !important; /* #d4d4db */
  font-family: var(--body);
}
```

Glyph is `p.name[0]` on both the card (`copilot-app.jsx:354`) and the detail header (`:404`).
The inline per-project colour is set (`style={{background: p.color}}`) and then shadowed by
the `!important`.

**Detail** — `copilot-app.jsx:337-384`:

```jsx
<button className="backlink" onClick={() => setSel(null)}><Icon.back /> All projects</button>
<div style={{display:"flex", gap:13, alignItems:"center", marginBottom:4}}>
  <span className="proj-ic" style={{background:p.color}}>{p.name[0]}</span>
  <div>
    <h2 style={{fontSize:18}}>{p.name}</h2>
    <div className="lrow__sub" style={{fontFamily:"var(--body)"}}>{p.desc}</div>
  </div>
</div>
<div className="sect-h">Chats · {chats.length}</div>
<div className="rowlist">{chats.map(c => <ChatRow …/>)}</div>
<div className="sect-h">Files · {p.files}</div>
<div className="rowlist">{PROJECT_FILES.map(f => …)}</div>
```

Same 32px `.proj-ic` as the card — the detail tile is **not** a size class up. `h2` picks up
`letter-spacing: -0.01em` from the global `h1-h4` rule (`copilot.css:113-121`).

`.backlink` (`copilot.css:1721-1739`): `display:inline-flex; align-items:center; gap:6px;
font-family: var(--mono); font-size: 11px; color: var(--mut); background: transparent;
border: 0; padding: 0; margin-bottom: 14px`, `svg { width: 13px; height: 13px }`,
`:hover { color: var(--tx) }`.

`.rowlist` (`copilot.css:1576-1581`): `flex column; border: 1px solid var(--line);
border-radius: var(--r); overflow: hidden; background: var(--panel)`.
`.lrow` (`:1582-1600`): `flex; align-items:center; gap:12px; padding:11px 14px;
border-bottom: 1px solid var(--line)`, last row `border-bottom: 0`.
`.lrow__name` (`:1635-1642`): `12.5px / 500 / var(--tx)`, flex row with `gap:8` for the chip.
`.lrow__time` (`:1655-1660`): mono `10.5px`, `var(--mut2)`.
`.lrow__ic` (`:1617-1626`) is the 28×28 leading icon slot — exactly what `Row.tsx` implements.

`.sect-h` (`copilot.css:1563-1573`): mono `9.5px`, `.12em`, uppercase, `var(--mut2)`,
`margin: 22px 0 10px`, `:first-child { margin-top: 0 }`. **Owned by PRD-01**, not this PRD.

## Architectural decision

### D1 — There is exactly one Projects list, and it lives in `chat-surface`

Delete the web scaffold (`ProjectsRoute.tsx:843-960` markup + `PROJECTS_GRID_CSS` at
`:965-1050`) and mount `<ProjectsDestination>` for the list on both hosts. This is the seam
the package exists to be; the recorded justification for the fork
(`ProjectsRoute.tsx:18-23`) was invalidated by the name-cache landing in the same file at
`:302`.

What web **keeps**, by passing the props the destination already declares: filter +
counts (`filter`, `counts`, `onFilterChange`), star/unstar (`onStarProject`,
`onUnstarProject`), archive/activate (`onArchiveProject`, `onActivateProject`), retry
(`onRetry`). What web **loses from the scaffold and regains in the shared card**: the
`viewer_role` chip (data already on `ProjectSummary`; render it in the shared card, still
conditioned on `viewer_role !== null` so `single_user_desktop` shows no empty strip) and
Delete (add `onDeleteProject?: (id) => void` — the one genuinely new prop). What web loses
outright: nothing.

Rejected — _"keep two lists, sync the CSS"_: that is the state we are in, and it re-drifted
within one release of the reason for the fork being fixed. Rejected — _"move
`ProjectsDestination` into `apps/frontend` and have desktop import it"_: forbidden
(`apps/*` → `apps/*`). Rejected — _"pass a `variant` flag to render the web card vs the
desktop card"_: a flag on a wrong abstraction; both variants are wrong against the design.

### D2 — The card is a single `<button>` hit area, matching `.card.proj-card`

Today web nests a `<button>` inside a `<div>` with a sibling footer, and desktop uses an
`<article>` whose only interactive element is the name `ItemLink`. Both are replaced by one
`<button className="ui-card ui-card--proj">` per the design, so the whole tile navigates.
The lifecycle actions (star/archive/delete) cannot nest inside it — they move to a
**hover/focus-revealed overlay** positioned in the card's top-right, outside the button in DOM
order, `position:absolute` inside a `position:relative` card wrapper. The card wrapper carries
no chrome; the `<button>` carries the border/radius/padding so the measured anchor
`default.card` and `default.card.hitarea` collapse onto the same element (see report caveat 2).

**Consequence**: the card name is a plain `<span>`, not an `<ItemLink>`. That is required
anyway — a link inside a button is invalid — and it independently removes the
`default.card.name.link color → --color-accent` HIGH row without touching
`packages/chat-surface/src/refs/ItemLink.tsx` (a cross-surface primitive this PRD does not
own).

### D3 — One `ProjectIconTile`, in `_shared/`, with one ramp and the design's geometry

New `packages/chat-surface/src/destinations/_shared/ProjectIconTile.tsx`:

```
props: { name: string; colorHue?: number; size?: 32 }   // 32 is the only size
glyph: (name.trim()[0] ?? "?").toUpperCase()            // never icon_emoji
geometry: 32×32, borderRadius var(--radius-md) /*8px*/, font-size 13px,
          font-weight var(--font-weight-semibold) /*600*/, font-family var(--font-sans)
tinted (colorHue !== undefined):
          background hsl(H 60% 28% / 0.45)
          border     1px solid hsl(H 60% 50% / 0.55)
          color      hsl(H 70% 82%)
neutral (colorHue === undefined):
          background var(--color-surface-elevated) /* #1d1d23 = the design's --panel3 */
          border     1px solid var(--color-border)
          color      var(--color-text-strong)      /* #d4d4db = the design's --tx2 */
```

**Token correction (README C10).** An earlier draft of this PRD specified
`var(--color-surface-muted)` and called it "the design's `--panel3` rung". That is wrong,
and both lines were opened to confirm it: `packages/design-system/src/styles.css:171` is
`--color-surface-muted: #16161a`, which is the design's `--panel2` — i.e. the row/card
**hover** ground (`copilot.css:1601-1603`, `:1717-1720`), so a tile painted with it would
vanish on hover. `styles.css:201` is `--color-surface-elevated: #1d1d23`, byte-identical to
the design's `--panel3` (`copilot.css:12`, applied to `.proj-ic` at `:1698-1710`). This PRD
adopts PRD-08 D5's mapping, which is already correct, so the tile and the Activity/Chats row
icon slot land on the same token. The foreground follows the same rule:
`--color-text-strong: #d4d4db` (`styles.css:203`) is the design's `--tx2`, not
`--color-text-muted` `#98989f` (`--mut`).

Consumed by the card (`ProjectsDestination`), the detail header (`ProjectDetailView`, which
loses its local copy at `:258-302`), `ProjectEditor.tsx:282`, `TemplateGallery.tsx:154`,
`TemplateEditor.tsx:170`, `fork-from-template-dialog.tsx:261`. Four ramps → one; three
geometries → one.

Two decisions inside this, taken deliberately:

- **The glyph is the monogram, never `icon_emoji`.** Design uses `p.name[0]`; the server
  defaults every project to 📁 (`0043_projects.sql:39`), so rendering the field produces an
  identical wall of folders. `icon_emoji` is not orphaned by this — `ProjectFilterChip`
  renders it in the Library surfaces (`ProjectFilterChip.tsx:279, 329, 346, 363`), so the
  field and its editor stay truthful. This also fixes the desktop's missing fallback by
  construction: there is nothing to fall back from.
- **We keep the per-project hue; the design's `!important` neutralisation is a mock
  leftover, not intent.** Evidence: the mock's own JSX sets `style={{background:p.color}}`
  at `copilot-app.jsx:353` and `:403` and its fixture defines a colour per project — a
  designer who wanted neutral tiles deletes the inline style rather than shadowing it from
  the stylesheet. Against that, live persists `color_hue` and ships a hue picker
  (`ProjectEditor.tsx:365`). Rendering neutral would make that picker inert — the exact
  defect class PRD-01 is fixing for the accent swatches. **Recorded divergence**: the three
  `default.card.icon` / `detail.icon` colour rows (`color`, `backgroundColor`,
  `borderColor`) will remain non-zero against the mock, by decision, and are downgraded to
  INFO by an **`expectDivergence`** string on those anchors in
  `surfaces/projects/anchors.json` + `anchors-desktop.json`. The key is `expectDivergence`
  — one word, camelCase — read at `tools/design-parity/lib/compare.mjs:172`; an earlier
  draft wrote `expected-divergence`, which the comparator ignores, leaving the rows HIGH
  forever. Every geometry row (`fontSize`, `width`, `height`, `borderRadius`, `fontWeight`)
  must still go to zero.

### D4 — A `_shared/Page` primitive, and Projects stops painting a page title

New `packages/chat-surface/src/destinations/_shared/Page.tsx` — `max-width: 960px;
padding: 20px 24px 40px; width: 100%; box-sizing: border-box`, i.e.
`copilot.css:1552-1555` verbatim. `_shared/` already ships PageLead / SectionHeader /
RowList / Row; `Page` is the one member of that set that was never written, which is why
`.pg` is copy-pasted four ways.

**`Page` is LEFT-ALIGNED — no `margin: 0 auto` (README G6, decided here).** An earlier
draft of this PRD hard-coded `margin: 0 auto`, copying `ChatsArchive.tsx:150`. Opened and
confirmed: the design's `.pg` (`copilot.css:1552-1555`) declares **only** `padding` and
`max-width` — no margin — and its parent `.main` (`copilot.css:381-388`) is a plain
`flex-direction: column` block with no `align-items`, so the 960px column sits flush against
the rail on any viewport wider than 960 + rail. Centring is therefore a live-app invention,
not the design. Since a shared primitive is exactly the wrong place to institutionalise an
undecided divergence, `Page` ships design-faithful and **no `expectDivergence` is recorded**
— the row is expected to reach zero rather than be excused. Consequence to accept
knowingly: web's current Chats screen shifts left when PRD-09 migrates `ChatsArchive` onto
`Page`; that is the point, and PRD-09's G6 non-goal is closed by this decision rather than
by an anchor annotation. If a later PRD wants centring, it changes the design source or the
`Page` primitive once, in one place — not per surface, and not via a `variant` flag.

`ProjectsDestination` and `ProjectDetailView` adopt `<Page>` and **drop `<PageHeader>`
entirely** from all four branches (`:211, :232, :256, :295`) — the topbar owns the title
(`copilot-app.jsx:600, :817`) and `PageLead.tsx:4-5` already records the decision. In its
place, `<PageLead>` with the design's copy from `copilot-app.jsx:391-394`.

Migrating `ChatsArchive` / `SkillsDestination` / `RoutineDetail` onto `<Page>` is **out of
scope here** (those surfaces have their own PRDs); `Page` is introduced with Projects as its
first consumer and the others follow. **`ActivityDestination` is `Page`'s named second
consumer** (README G5): PRD-08 migrates it off the hand-rolled `maxWidth: 960 / padding:
"16px 20px 32px"` shell at `ActivityDestination.tsx:611-615` and separately owns the
`.lrow` `padding: 11px 14px` fix (`copilot.css:1582-1600`). PRD-10 ships the primitive and
does not edit `ActivityDestination`.

**The create affordance survives the PageHeader deletion.** It moves to the filter row as a
right-aligned quiet control (`marginInlineStart: auto`, existing `.ui-button` recipe at the
small size). This is a **deliberate divergence** from the mock, which has no create control:
a shipping product needs a way to make its first project, and the design's fixture simply
pre-populates three. Both it and `FilterTabs` are elements the design has no counterpart
for, so they carry **no design anchor at all** — an anchor whose `design` side is unmatched
and whose `live` side is unmatched is skipped by `compare.mjs`, and one with only a live
side is annotated with a `note` (`compare.mjs:172` reads `a.expectDivergence || l.note`).
Record them as anchors with a `note` explaining "extra in live, deliberate"; do **not**
invent an `extra-in-live` key — no such key exists in the comparator.

### D5 — The back-link belongs to `ProjectDetailView`, not the host

Today it is `ProjectsRoute.tsx:686-701` — host-owned, which is precisely why desktop, when
it gets a detail view, would have to write a second one. `ProjectDetailView` gains
`onBack?: () => void` and renders a new `_shared/BackLink.tsx`:

```
inline-flex, align-items:center, gap: 6px
font-family var(--font-mono), font-size 11px  (see note below)
color var(--color-text-muted); hover var(--color-text)
background transparent, border 0, padding 0, margin-bottom 14px
leading chevron svg 13×13
```

11px has no exact rung, and the whole existing ladder was checked before concluding that —
`--font-size-3xs` = 0.5625rem = **9px** (`styles.css:62`), `--font-size-2xs` = 0.7rem =
**11.2px** (`:63`), `--font-size-xs` = 0.78rem = **12.5px** (`:64`), `--font-size-mono-10` =
0.625rem = **10px** (`:71`). `--font-size-2xs` is the nearest at 0.2px, below the LOW
threshold. **Use it; do not mint a rung.** (This is the "use what exists" rule: the failure
mode this program keeps hitting is a component picking `--font-size-2xs` where a _closer_
existing rung was available — here there is none closer, so 2xs is the right pick, not the
lazy one.) Colour is `--color-text-muted` `#98989f` (`styles.css:177`), byte-identical to
the design's `--mut` (`copilot.css:18`).

### D6 — Detail sections render through `RowList` / `Row`

`ProjectsRoute.tsx:641-673`'s hand-rolled `<ul>` is deleted. The Chats section becomes
`<SectionHeader count>` + `<RowList>` of `<Row icon chip sub meta onActivate>`:

| slot    | value                        | design ref                     |
| ------- | ---------------------------- | ------------------------------ |
| `icon`  | chat glyph in the 28×28 slot | `.lrow__ic` `copilot.css:1617` |
| `title` | the chat **name**            | `.lrow__name` `:1635-1642`     |
| `chip`  | run status                   | `.chip` inline after the name  |
| `sub`   | preview + model              | `.lrow__sub` `:1643-1648`      |
| `meta`  | relative time, mono          | `.lrow__time` `:1655-1660`     |

Today the _preview_ is used as the title (`ProjectsRoute.tsx:670`) with no other slot filled.
The data that fills `title` / `chip` / `sub` / `meta`, and the Files section's rows, come from
**PRD-07**; PRD-10 owns the binding and the markup and ships against whatever PRD-07's
`SectionResult` shapes deliver. If PRD-07 has not landed, the Chats rows render with
`title`/`meta` only and the Files section keeps its "coming soon" `EmptyState`
(`ProjectDetailView.tsx:592-606`) — no skeleton that never resolves.

**Ownership split with PRD-07 (README C16).** PRD-07's draft extracted
`destinations/chats/ChatsSection.tsx` out of `ChatsArchive.tsx:351-403` and mounted it here.
That is withdrawn: **PRD-10 owns the project-detail chat-row markup**, composed from
`_shared/SectionHeader` + `RowList` + `Row` as tabulated above, and PRD-07 supplies only the
data. Consequences to hold onto: PRD-07 drops the extraction, its risk row about regressing
`ChatsArchive.test.tsx` disappears with it, and PRD-10 does **not** touch
`ChatsArchive.tsx` at all (that file is PRD-09's, per the README serialisation table).
`Row.tsx` itself is **PRD-08's file** (README C9) — PRD-10 consumes the post-PRD-08 props
(`trailing`, `iconTone`, the 16px trailing reserve, `.ui-list-row`) and adds none of its own.

### D7 — `CardGrid` gets the design's `.grid3`, as a kit recipe

`CardGrid` is `auto-fill minmax(260px, 1fr)` (`CardGrid.tsx:31`), which cannot express the
design's 3→1 collapse (it would stop at 2-up). A media query cannot be written in an inline
style object, so the rule belongs in the kit. Add to `packages/design-system/src/styles.css`,
transcribing `copilot.css:1672-1682`:

```css
.ui-grid3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-grid-gap);
}
@media (max-width: 900px) {
  .ui-grid3 {
    grid-template-columns: 1fr;
  }
}
```

with `--space-grid-gap: 0.625rem` (10px) added to the density block alongside the existing
`--space-row-gap` (`styles.css:126`) / `--space-card-pad` (`:127`) aliases and their compact
overrides (`:134-135`) — the established pattern, not a parallel one. **PRD-10 owns
`--space-grid-gap` and `.ui-grid3` outright** (README, _Wrong or unsupportable declared
dependencies_): `grep -n "space-grid-gap\|grid3" docs/plan/design-parity-remediation/PRD-01-design-tokens.md`
returns zero — PRD-01 does not mint it, so the earlier "if PRD-01 introduces
`--space-grid-gap`, use theirs" hedge is deleted. PRD-10 is also the **last** writer of
`styles.css` in the program (order `01 → 02 → 08 → 11 → 10`), so these two entries are
appended, never merged against a later claimant. `CardGrid` gains `variant?: "auto-fill" | "grid3"` (default
`"auto-fill"`, so no existing consumer changes) which, when `"grid3"`, emits
`className="ui-grid3"` and no inline `gridTemplateColumns`/`gap`.

Card padding: the design's `--pad` is 13px; `--space-card-pad` resolves to `--space-md`
= 0.75rem = 12px (`styles.css:102, 127`). Use the token. 1px is below the LOW band and the
token is density-aware; minting a 13px rung to chase it would be the bandaid.

### D8 — `profile` binds to the `DeploymentProfile` port, and the untethered default is deleted

`ProjectDetailView.tsx:810`'s `profile = "solo"` prop default is replaced by
`useOptionalDeploymentProfile()`: `"team"` → the eight-tab view (`:998-1000`),
`"single_user_desktop"` or no provider → the v3 solo sections. The explicit `profile` prop is
kept **for tests only** as an override and documented as such. This makes
`ProjectMembersTab`, `ProjectActivityTab` and the eight-tab model reachable in team
deployments instead of dead in all of them, without a flag.

### D9 — Wire-or-delete ledger

| Surface                                                          | Decision                       | Reason                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------------------------------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ProjectFilterChip`                                              | **KEEP, no change**            | Live in `LibraryPanel.tsx:189` and `SaveToLibraryPopover.tsx:390`. The audit's "unmounted" claim is wrong. It is also the only surface rendering `icon_emoji`, which D3 relies on.                                                                                                                                                                                                                                                                                                                         |
| `ProjectEditor`                                                  | **WIRE (this PRD)**            | It is the create/edit sheet that `onCreateProject` was always meant to open, and it is backed by `POST /v1/projects` + `PATCH /v1/projects/{id}` (`projects_routes.py:156, :173`). Wiring it makes the create button (D4) real and the hue picker (D3) truthful. Export from `index.ts`; both hosts open it from `onCreateProject`.                                                                                                                                                                        |
| `ProjectMembersTab`, `ProjectActivityTab`                        | **KEEP, gated (D8)**           | Team-profile surfaces. Deleting them would delete the `team` deployment's project admin. D8 makes them reachable.                                                                                                                                                                                                                                                                                                                                                                                          |
| `transfer-ownership-dialog`                                      | **WIRE (this PRD)**            | `ProjectDetailView:214` already emits `onRequestTransferOwnership` and `POST /v1/projects/{id}/transfer` exists (`projects_routes.py:335`). The callback fires into nothing today — a dangling half-feature. Render it from the detail view under `profile === "team" && canManage`.                                                                                                                                                                                                                       |
| `archive-blocked-dialog`                                         | **WIRE (this PRD)**            | It is the error surface for archive; `ProjectsDestination` already has `onArchiveProject`. Backed by `services/backend/tests/test_projects_archive_blocked.py`. Rendering it is ~10 lines at the archive call site.                                                                                                                                                                                                                                                                                        |
| `ProjectsPanel`                                                  | **DELETE**                     | Zero consumers, not required by the design (v3 Projects has no right rail), and its own header (`ProjectsPanel.tsx:3-16`) says it ships 2 of 6 planned sections. Both shipped sections (status filter, New-project CTA) now live on the destination. Deleting a wrong abstraction beats keeping a 30%-complete one behind a flag. Remove from `index.ts:533, 548` and delete the test.                                                                                                                     |
| `TemplateGallery`, `TemplateEditor`, `fork-from-template-dialog` | **WIRE — but NOT in this PRD** | Not a dead-code question: web ships a **parallel** 403-line `TemplateGalleryRoute` (`App.tsx:129-131, :942-958`) while the 466-line package components sit unexported. Same fork as D1, one directory over, and it deserves its own PRD with its own parity measurement (the design harness exposes no templates state, so there is nothing to diff yet). This PRD only routes their tiles through `ProjectIconTile` (D3) so the ramp count still collapses to one. Recorded, owned, not silently dropped. |
| `ProjectDetailView` eight-tab team profile                       | **KEEP, gated (D8)**           | See above.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

### Contracts

No backend route, no migration, no `api-types` change. Every prop this PRD adds
(`onDeleteProject`, `onBack`, `CardGrid.variant`) is optional and additive; every field it
reads (`viewer_role`, `color_hue`, `counts`) is already on the wire.

**Migration ids: PRD-10 claims none.** Verified on disk in this working tree —
`ls services/backend/migrations` tops out at `0045_provider_api_keys_custom_endpoint.sql`
and `ls services/ai-backend/migrations` contains only `0001_runtime_baseline.sql`, matching
the README's stated high-water marks. The reserved ids in the README's assignment table
(`backend` `0046` → PRD-06, `0047` → PRD-07; `ai-backend` `0002` → PRD-05, `0003` → PRD-07,
`0004` → PRD-09) are **not** PRD-10's, and PRD-10 must not run
`tools/check_migration_manifest.py --write`, because it adds no row to either
`MANIFEST.lock`.

## Scope

### `packages/design-system`

| File             | Reason                                                                                                                                                                                                                                          |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/styles.css` | Add `--space-grid-gap` to the density block (`:126-136`) and the `.ui-grid3` recipe (D7). **Append only** — PRD-10 is the last of five writers (order `01 → 02 → 08 → 11 → 10`); it must not restate or re-edit any token PRD-01/02/08/11 land. |

### `packages/chat-surface`

| File                                                               | Reason                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/destinations/_shared/Page.tsx` (new) + `.test.tsx`            | The `.pg` shell: 960px / `20px 24px 40px`, **left-aligned, no `margin: 0 auto`** (D4, README G6). PRD-08 migrates Activity onto it as the second consumer.                                                                                          |
| `src/destinations/_shared/BackLink.tsx` (new) + `.test.tsx`        | The `.backlink` control, owned by the package so both hosts get it (D5).                                                                                                                                                                            |
| `src/destinations/_shared/ProjectIconTile.tsx` (new) + `.test.tsx` | One tile, one ramp, one geometry (D3).                                                                                                                                                                                                              |
| `src/destinations/_shared/index.ts`                                | Export the three new primitives.                                                                                                                                                                                                                    |
| `src/shell/CardGrid.tsx` + `.test.tsx`                             | `variant?: "auto-fill" \| "grid3"` (D7).                                                                                                                                                                                                            |
| `src/destinations/projects/ProjectsDestination.tsx`                | Card → `.card.proj-card` anatomy; `<Page>` + `<PageLead>`; drop `<PageHeader>`; `ProjectIconTile`; role chip; `onDeleteProject`; create moved to the filter row (D1, D2, D3, D4, D7).                                                               |
| `src/destinations/projects/ProjectsDestination.test.tsx`           | Assertions for the above, incl. the design geometry numbers.                                                                                                                                                                                        |
| `src/destinations/projects/ProjectDetailView.tsx`                  | `<Page>`; `<BackLink onBack>`; local `ProjectIconTile` deleted (`:258-302`); title `--tracking-snug`; `profile` from the `DeploymentProfile` port; Chats/Files via `RowList`/`Row`; render transfer + archive-blocked dialogs (D3, D5, D6, D8, D9). |
| `src/destinations/projects/ProjectDetailView.test.tsx`             | Back-link, tile geometry, row anatomy, profile-from-port.                                                                                                                                                                                           |
| `src/destinations/projects/ProjectEditor.tsx`                      | Tile via `ProjectIconTile` (drops ramp C at `:282`).                                                                                                                                                                                                |
| `src/destinations/projects/TemplateGallery.tsx`                    | Tile via `ProjectIconTile` (`:154`).                                                                                                                                                                                                                |
| `src/destinations/projects/TemplateEditor.tsx`                     | Tile via `ProjectIconTile` (`:170`).                                                                                                                                                                                                                |
| `src/destinations/projects/fork-from-template-dialog.tsx`          | Tile via `ProjectIconTile` (`:261`).                                                                                                                                                                                                                |
| `src/destinations/projects/ProjectsPanel.tsx` + `.test.tsx`        | **Deleted** (D9).                                                                                                                                                                                                                                   |
| `src/destinations/projects/index.ts`                               | Export `ProjectEditor`, `TransferOwnershipDialog`, `ArchiveBlockedDialog`; drop `ProjectsPanel`.                                                                                                                                                    |
| `src/index.ts`                                                     | Same at the package boundary (`:530-550`).                                                                                                                                                                                                          |

### `apps/frontend`

| File                                           | Reason                                                                                                                                                                                                                                                                                                             |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/features/projects/ProjectsRoute.tsx`      | Delete the scaffold (`:843-960`), `PROJECTS_GRID_CSS` (`:965-1050`), the back button (`:686-701`) and the hand-rolled chat `<ul>` (`:641-673`); mount `<ProjectsDestination>` for the list with the full callback set incl. `onCreateProject` and `onDeleteProject`; rewrite the stale header comment at `:18-23`. |
| `src/features/projects/ProjectsRoute.test.tsx` | Retarget the scaffold test-ids onto the shared card; keep the star/archive/delete behavioural tests.                                                                                                                                                                                                               |

### `apps/desktop`

| File                                   | Reason                                                                                                                                                               |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `renderer/destinationBinders.tsx`      | `ProjectsBinder` (`:563-567`) consumes PRD-03's shared binder: focus state, detail fetch, `renderDetail`, `onBack`, `cacheProjectNames`, and the mutation callbacks. |
| `renderer/destinationBinders.test.tsx` | Regression: clicking a card reaches the detail view on desktop.                                                                                                      |

### `tools/design-parity`

| File                                                     | Reason                                                                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `lib/render-live-projects.test.tsx`                      | Update selectors; keep all three states so convergence is _provable_ rather than asserted.                                                                                                                                                                                                                                                 |
| `surfaces/projects/anchors.json`, `anchors-desktop.json` | Retarget live selectors onto the shared card; add **`expectDivergence`** (exact key, `lib/compare.mjs:172`) on the three tile-colour rows and a live-side `note` on the create/filter chrome. Do **not** edit `tools/design-parity/vitest.config.mjs` — it globs `lib/render-live*.test.tsx` and is a merge point for every PRD in flight. |

## Addendum — Activity adopts `_shared/Page` (README O2)

Reconciliation left this owned by NOBODY: PRD-08 deferred it to "PRD-10's wave" and
PRD-10 disclaimed editing `ActivityDestination`, so the swap vanished and README G5's
second half stayed open. **PRD-10 owns it**, because PRD-10 ships the primitive and a
primitive whose first sibling caller is never migrated is just more dead code — exactly
what this program exists to delete.

Scope addition:
| File | Why |
| --- | --- |
| `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx` | Replace the hand-rolled `.pg` page shell with `<Page>`; no other change. |
| `packages/chat-surface/src/destinations/activity/ActivityDestination.test.tsx` | Pin the swap. |

DoD addition (numbered continuing this PRD's list):

- `grep -c 'maxWidth' packages/chat-surface/src/destinations/activity/ActivityDestination.tsx`
  prints `0`, and the file imports `Page` from `../_shared/Page`.
- `ActivityDestination.test.tsx` asserts the rendered root carries the `Page` primitive's
  `data-page` attribute, so a future hand-rolled shell fails the test.
- The `activity` parity report is regenerated and `git diff` shows **no line added under
  any `## HIGH` heading** (this is a shell swap, not a visual change).

## Non-goals

- **`GET /v1/projects/{id}/files`, project counts, and the chat-row payload** — PRD-07. This
  PRD renders whatever those deliver and degrades honestly when they are absent.
- **The shared Projects data binder and the desktop name cache** — PRD-03. PRD-10 assumes
  `focusedProjectId` / `renderDetail` / detail props arrive on both hosts and specifies only
  what is drawn.
- **`--font-size-sm` 13.6px → 13px, `SectionHeader`'s 11.2px/600/`0.12em`, `--color-bg`
  `#09090b` → `#050506`, `--color-scrim`** — PRD-01. Roughly a third of the Projects MEDIUM
  rows are these four and will clear when PRD-01 lands, not here.
- **`ItemLink`'s accent colour** (`refs/ItemLink.tsx:72`) and the app-wide accent-link
  policy — **PRD-04**, which owns `refs/ItemLink.tsx` (README G11; the earlier "the refs
  PRD" was an unresolved reference). D2 removes `ItemLink` from the Projects card, which
  clears the Projects rows without touching the primitive, but the one-place statement of
  the policy — including the inline-`CSSProperties` interactive chrome flagged as Projects
  R9/R10 — is PRD-04's to make.
- **Converging web's `TemplateGalleryRoute` onto the package `TemplateGallery`** — decided
  (WIRE) and scoped out with a reason in D9.
- **Migrating Activity / Chats / Skills / RoutineDetail onto `<Page>`** — `Page` ships with
  Projects as its first consumer; the sibling migrations belong to their surfaces' PRDs.
  Named owners: **PRD-08** for `ActivityDestination` (README G5, which also gives PRD-08 the
  `.lrow` `11px 14px` padding), **PRD-09** for `ChatsArchive`.
- **`_shared/Row.tsx` itself** — **PRD-08** owns the file (README C9). PRD-10 consumes it and
  adds no props to it; if a project chat row needs a slot `Row` lacks, raise it against
  PRD-08 rather than editing the file in PRD-10's wave.
- **`ChatsDestination` / `ChatsSidebar` deletion and the orphan-destination CI guard** —
  **PRD-13** (README C17), which lands after PRD-10 and computes its waiver list against the
  post-PRD-10 tree. PRD-10's only deletion is `ProjectsPanel` (D9).
- **Per-project deep-link routing** (`/projects/{id}`). Detail remains destination-local
  state on both hosts, as today.
- **Hover / focus / active parity** — the extractor reads static computed styles only
  (FINDINGS "What could not be measured"). `.card.proj-card:hover` and `.backlink:hover` are
  implemented to the design values but are not gate-verifiable.

## Risks & rollback

| Risk                                                                                                      | Guard                                                                                                                                                                                                                                                                                                                                       |
| --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deleting the web scaffold silently drops star / archive / activate / delete.                              | `apps/frontend/src/features/projects/ProjectsRoute.test.tsx` already covers all four via the `projects-route-{star,archive,activate,delete}` test-ids; retarget the ids, keep the assertions **unedited** so the four cases still fail if a callback is dropped. DoD 12 requires `npm run test --workspace @0x-copilot/frontend` to exit 0. |
| The role chip leaks under `single_user_desktop`.                                                          | Server returns `viewer_role: null` there; the render stays conditioned on `viewer_role !== null`. `ProjectsDestination.test.tsx` gets an explicit null-role case (DoD 6).                                                                                                                                                                   |
| Moving the card actions out of DOM flow into a hover overlay makes them keyboard-unreachable.             | They keep tab order (rendered after the button, `position:absolute` only visually) and are revealed on `:focus-within`, not only `:hover`. DoD 7 pins a keyboard-reachability assertion.                                                                                                                                                    |
| `.ui-grid3` in `styles.css` changes layout for a surface that did not ask for it.                         | `CardGrid.variant` defaults to `"auto-fill"`; the class is opt-in and applies to nothing until a caller passes `variant="grid3"`. `CardGrid.test.tsx` asserts the default is unchanged.                                                                                                                                                     |
| Binding `profile` to the port flips team deployments from the solo detail to the eight-tab view.          | That is the intent (D8), and it is the behaviour the eight-tab tests at `ProjectDetailView.test.tsx:143` already describe. Web/desktop both run `single_user_desktop` today, so the shipped default does not move.                                                                                                                          |
| `ProjectsPanel` deletion breaks an external consumer.                                                     | `grep -rn ProjectsPanel packages apps` returns only its own file, its test, and the two export lines. Typecheck catches anything missed.                                                                                                                                                                                                    |
| Deleting `PageHeader` from Projects removes the only visible page label in a host whose topbar is hidden. | The topbar renders for every destination except `workspace` and `settings` (`copilot-app.jsx:739`), and both hosts already render it — `render-live-projects.test.tsx:476, :497` assert it. If a host is found without one, the fix is to render the topbar, not to reinstate a 22px title.                                                 |

**Rollback**: the change is additive-props + deletions in seven files, no schema and no wire
contract. Reverting the PRD's commits restores both lists byte-for-byte; the two new
design-system entries (`--space-grid-gap`, `.ui-grid3`) are inert once no caller passes
`variant="grid3"` and can be left in place safely.

## Definition of Done

1. `grep -n "PROJECTS_GRID_CSS\|projects-grid3\|projects-card__" apps/frontend/src/features/projects/ProjectsRoute.tsx` returns **zero** matches.
2. `grep -rn "hsl(\${" packages/chat-surface/src/destinations/projects/` returns **zero** matches — every per-project colour is produced inside `packages/chat-surface/src/destinations/_shared/ProjectIconTile.tsx`.
3. `packages/chat-surface/src/destinations/_shared/ProjectIconTile.test.tsx` asserts the **design values numerically**: rendered style has `width: 32`, `height: 32`, `borderRadius: "var(--radius-md)"`, `fontSize: 13`, `fontWeight: "var(--font-weight-semibold)"` (all four tokens verified present: `styles.css:109` `--radius-md: 0.5rem`, `:75` `--font-weight-semibold: 600`) — and that the rendered text for `name="Launch Week"` is `"L"`, for **any** `icon_emoji` on the record. The same test pins README C10: with **no** `colorHue`, the tile's `backgroundColor` is `"var(--color-surface-elevated)"` and its `color` is `"var(--color-text-strong)"`, and `grep -c "color-surface-muted" packages/chat-surface/src/destinations/_shared/ProjectIconTile.tsx` returns **0**.
4. `packages/chat-surface/src/destinations/_shared/BackLink.test.tsx` asserts `fontFamily: "var(--font-mono)"`, `fontSize: "var(--font-size-2xs)"`, `color: "var(--color-text-muted)"`, `gap: 6`, `marginBottom: 14`, and that the leading `<svg>` is `13`×`13`.
5. `packages/chat-surface/src/destinations/_shared/Page.test.tsx` asserts `maxWidth: 960`, `padding: "20px 24px 40px"`, and — the G6 decision, so a future edit cannot quietly re-centre the column — that the rendered element's `style.margin`, `style.marginLeft` and `style.marginRight` are all `""` (no `auto`).
6. `packages/chat-surface/src/destinations/projects/ProjectsDestination.test.tsx` asserts, in the ready state: (a) no element matching `[data-testid="page-header"]` exists in **any** of the four branches; (b) `[data-testid="page-lead"]` renders with the design copy; (c) exactly one `<button>` per project is the card, and it carries `borderRadius: "var(--radius-md)"` and `padding: "var(--space-card-pad)"`; (d) the role chip renders for `viewer_role: "owner"` and does **not** render for `viewer_role: null`.
7. The same file asserts the lifecycle actions are keyboard-reachable: `userEvent.tab()` from the card button lands on the Star control without a pointer event.
8. `packages/chat-surface/src/shell/CardGrid.test.tsx` asserts `variant="grid3"` emits `className="ui-grid3"` with no inline `gridTemplateColumns`, and that the **default** render still emits `repeat(auto-fill, minmax(260px, 1fr))`.
9. **Regression guard for the desktop-detail bug**: `apps/desktop/renderer/destinationBinders.test.tsx` renders `<ProjectsBinder/>` with a stubbed transport returning one project, clicks the card, and asserts `[data-testid="project-detail-name"]` appears. This test fails on `main` today.
10. **Regression guard for the emoji-wall bug**: `ProjectsDestination.test.tsx` renders three projects all carrying `icon_emoji: "📁"` with distinct names and asserts the three tiles' text content is `["L","T","G"]`, not `["📁","📁","📁"]`.
11. `packages/chat-surface/src/destinations/projects/ProjectDetailView.test.tsx` asserts: `[data-testid="project-detail-icon"]` has `width: 32` (not 44); the `h1/h2` name style includes `letterSpacing: "var(--tracking-snug)"`; a `<BackLink>` renders when `onBack` is supplied and calls it on click; and with `<DeploymentProfileProvider value="team">` and **no** `profile` prop the eight-tab bar renders, while with `value="single_user_desktop"` it does not.
12. `npm run test --workspace @0x-copilot/chat-surface`, `npm run test --workspace @0x-copilot/frontend` and `npm run test --workspace @0x-copilot/desktop` each **exit 0**, _or_ the failing test ids are byte-identical to `docs/plan/design-parity-remediation/baseline-failures.txt`, which this PR does not modify. `npm run typecheck --workspace <w>` exits 0 for `@0x-copilot/chat-surface`, `@0x-copilot/frontend`, `@0x-copilot/desktop`, `@0x-copilot/design-system` and `@0x-copilot/api-types`. (Verified against the manifests: `design-system` has **only** a `typecheck` script — no `test` — so no test invocation is claimed for it.)
13. `grep -rn "ProjectsPanel" packages apps` returns **zero** matches (file, test, and both export lines gone).
14. `grep -c "ProjectEditor\|TransferOwnershipDialog\|ArchiveBlockedDialog" packages/chat-surface/src/index.ts` returns **3**; `grep -c "ProjectEditor" apps/frontend/src/features/projects/ProjectsRoute.tsx` returns **≥1**; `grep -c "ProjectEditor" apps/desktop/renderer/destinationBinders.tsx` returns **≥1**. (Named files rather than a tree grep with a "non-test" qualifier, which no single command can express.)
15. **Two hosts, one implementation — as a command.** Regenerate all three projects reports (procedure: `tools/design-parity/SKILL.md`; reproduce block at the end of `tools/design-parity/surfaces/projects/out/FINDINGS.md`), then from the repo root:

    ```bash
    cd tools/design-parity/surfaces/projects/out
    diff <(awk '/^## .*HIGH/{f=1} /^## .*MEDIUM/{f=0} f' report-default.md) \
         <(awk '/^## .*HIGH/{f=1} /^## .*MEDIUM/{f=0} f' report-default-chatsurface.md)
    ```

    **Expected output: nothing, exit status 0.** No state-name normalisation is needed and none is permitted: both `anchors.json` and `anchors-desktop.json` already label every row with the same `default.*` prefix (verified — `anchors-desktop.json` and `anchors.json` agree on `default.page.container`, `default.page.lead`, `default.grid`, `default.card`, `default.card.icon`, `default.card.name`, `default.card.desc`). The one label the two files disagree on today is `default.card.hitarea`, present only in `anchors.json`; D2 collapses the hit area onto the card element, so **this PR deletes that anchor** and the two label sets become equal. If the diff is non-empty, the two hosts are still rendering different markup and the PRD is not done.

16. In the regenerated `report-default.md`, `awk '/^## .*HIGH/{f=1} /^## .*MEDIUM/{f=0} f' report-default.md | grep -c '| Project card |'` returns **3**, and those three rows are exactly `default.card.icon` × {`color`, `backgroundColor`, `borderColor`} — the divergence recorded via `expectDivergence` in D3. `grep -c 'default.page.lead .*missing-in-live' report-default.md` returns **0**.
17. In the regenerated `report-detail.md`: `awk '/^## .*HIGH/{f=1} /^## .*MEDIUM/{f=0} f' report-detail.md | grep -c '| Detail header |'` returns **3**, and those three rows are exactly `detail.icon` × {`color`, `backgroundColor`, `borderColor`}; and `grep -cE 'detail\.chatrow\.(icon|chip|sub|time).*missing-in-live' report-detail.md` returns **0**.
18. `grep -cE '`detail\.rowlist\.chats`.*(backgroundColor|borderColor)' report-detail.md` returns **0** — i.e. neither the `--panel` background nor the `--line` border row survives, which is only true once the Chats list is a `RowList` card rather than the bare `<ul>` at `ProjectsRoute.tsx:641-673`. (Today both rows are HIGH; see `report-detail.md`'s `detail.rowlist.chats` entries.)
19. **No frozen counts.** `grep -nE '\b(8|9|13|23|26|28|42) HIGH\b' docs/plan/design-parity-remediation/PRD-10-projects-surface.md` returns **0** outside the Evidence table's DISPUTED row — this PRD's gate is expressed as absolute zeros and a two-report diff, never as a parity count captured before PRD-01/02/06/08 moved it.

## Dependencies

Per the README's corrected order, PRD-10 is **Wave 4** and its hard predecessors are
**PRD-03, PRD-07 and PRD-08** (PRD-01 lands in Wave 0, so it is upstream by construction).

**Must land first**

- **PRD-03 (host binding contract + Projects binder + name cache)** — supplies the shared
  data binder that gives the desktop host `focusedProjectId`, `renderDetail`, the detail
  payload, and `cacheProjectNames`. DoD 9 cannot pass without it. Note PRD-03's scope was
  cut by README C3/C5/C6: it no longer touches `ConnectorsDestination` and no longer ships
  the connector-access migration, but the shell/projects binding-totality work PRD-10 relies
  on is untouched by those cuts.
- **PRD-08 (Activity surface)** — **owns `packages/chat-surface/src/destinations/_shared/Row.tsx`**
  (README C9) and lands the `trailing` slot, the `iconTone` prop, the icon-slot background
  (`--color-surface-elevated`, D5) and the `.ui-list-row` recipe. PRD-10's D6 composes those
  props; landing PRD-10 first would mean adding them twice. PRD-08 also fixes the 15px row
  glyph (README G2) and the `.lrow` `11px 14px` padding (G5), both of which show up in
  `report-detail.md`'s Chat-row group.
- **PRD-07 (project data)** — promoted from "soft" to a hard predecessor: the README puts it
  in Wave 2 ahead of PRD-10, it owns `agent_conversations.project_id` and the project-scoped
  chats/files reads that fill `Row`'s `title` / `chip` / `sub` / `meta` slots, and it is the
  earlier writer of both `ProjectsDestination.tsx` (`02 → 03 → 07 → 10`) and
  `ProjectDetailView.tsx` (`07 → 10`). DoD 17's `detail.chatrow.*` rows cannot reach zero
  without its payload. Per README C16, PRD-07 supplies **data only** here — it does not
  extract `ChatsSection.tsx` and does not render project-detail rows.
- **PRD-01 (design tokens)** — Wave 0. Owns `--font-size-sm` → 13px, `SectionHeader`'s
  size/weight/tracking (applied to the **label element**, not the `.sect-h` wrapper — README
  C13) and `--color-scrim`. It does **not** change `--color-bg`: PRD-01 decision F ruled
  README G3 INVALID — `--color-bg` already equals the design's `--ink`, and `#050506` is the
  mock's stage colour, not the app canvas (0 report rows reference it). It does **not**
  mint `--space-grid-gap` — PRD-10 owns that token (README, _Wrong or unsupportable declared
  dependencies_), and the earlier "if PRD-01 introduces it, use theirs" hedge is deleted.

**This PRD blocks**

- **PRD-13 (dead code + orphan guard)** — Wave 4, immediately after PRD-10. Its orphan-waiver
  list and its parity-delta DoD are only computable against the post-PRD-10 tree, and PRD-10
  deletes `ProjectsPanel` while PRD-13 deletes `ChatsDestination` / `ChatsSidebar`
  (README C17) — the two deletions must not be duplicated.

**This PRD unblocks**

- The templates convergence PRD (D9) — it inherits `ProjectIconTile`, `Page`, and the
  precedent that a host binder does not fork package markup.
- Any surface adopting `<Page>` / `<BackLink>` — **PRD-08** migrates `ActivityDestination`
  as `Page`'s named second consumer (README G5); Chats (PRD-09), Skills and Routines follow.
- Team-deployment project administration, which D8 makes reachable for the first time.
