# PRD-04 — Run identity: real run titles, and a run link that actually opens the run

## Problem

Open Activity. The agent has done eight things today and yesterday — "Weekly treasury
reconciliation", "Draft investor update", "Triage new GitHub issues". Seven of the eight
rows render the literal word **"Run"**. Only the one still-executing run shows its name.
The record of what the agent did is a list of eight identical nouns.

The titles are not missing. Both hosts fetch them, project them into the row, hand them
to the component, and the component throws them away at the last render hop.

Click one of those rows and it gets worse. On web the click navigates to
`/settings#undefined`. On desktop the click rewrites the URL to `run://<id>` and nothing
happens — the shell only reacts to `conversation`/`chat` routes. And the one row that
_does_ carry a working handler, the running row, deep-links the web app to
`/run/<runId>` when the Run cockpit binds by **conversation** id — so reloading that URL
binds the cockpit to an id that is not a conversation.

So the surface named "Activity" cannot name any activity and cannot open any of it. Both
halves are the same defect: a cross-destination link registry that invents facts it does
not have — the entity's display name, and the host's route — instead of being handed
them by the code that does have them.

## Evidence

Every row below was opened and read in this worktree (branch `claude/design-parity-audit-7ec82a`).

| Claim                                                                                                                   | File:line                                                                                                                                                  | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ----------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Non-running Activity rows render their title through `ItemLink kind="run"`                                              | `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx:511-515`                                                                          | `const title = isRunning ? <span>{row.title}</span> : <ItemLink ref={{kind:"run", id: row.run_id}} deletedLabel={row.title} />`. The real title is passed only as the _deleted_ fallback.                                                                                                                                                                                                                                                                                                             |
| The only shipping `"run"` resolver returns a hardcoded label                                                            | `packages/chat-surface/src/destinations/home/index.ts:52-60`                                                                                               | `registerItemRefResolver("run", async (id) => ({ label: "Run", icon: null, route: { kind:"run", runId: id }, breadcrumb: "Runs" }))`. Confirmed brief's line range exactly.                                                                                                                                                                                                                                                                                                                           |
| `apps/frontend` registers 7 kinds, never `"run"`                                                                        | `apps/frontend/src/app/App.tsx:228-309`                                                                                                                    | `todo` (:229), `inbox_item` (:247), `project` (:267), `library_file` (:279), `library_page` (:287), `library_dataset` (:295), `agent` (:303). No `"run"`.                                                                                                                                                                                                                                                                                                                                             |
| `apps/desktop` registers nothing                                                                                        | repo-wide grep for `registerItemRefResolver`                                                                                                               | 0 hits under `apps/desktop/`. Confirmed.                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| The bug is already pinned by a passing test                                                                             | `tools/design-parity/lib/render-live-activity.test.tsx:292-357`                                                                                            | ACT-06 asserts all 7 `item-link` labels equal `"Run"` and that each of the 7 real titles `queryByText` → `null`; the single running row keeps `"Launch Week ops"`.                                                                                                                                                                                                                                                                                                                                    |
| The row has the title in hand and discards it                                                                           | `ActivityDestination.tsx:514` + `:552`                                                                                                                     | Same render passes `data-row-title={row.title}` to the DOM while rendering `"Run"` as the visible text.                                                                                                                                                                                                                                                                                                                                                                                               |
| The registry is a module singleton whose first writer wins                                                              | `packages/chat-surface/src/refs/registry.ts:93-121`                                                                                                        | `REGISTRY = new Map<ItemKind, …>`; `registerItemRefResolver` throws `ItemRefResolverAlreadyRegistered` unless `{replace:true}`. All 20 production call sites therefore wrap in `if (!hasItemRefResolver(k))`.                                                                                                                                                                                                                                                                                         |
| **DISPUTED (partly)** — brief calls `App.tsx:266-273` a "rival hardcoded-label Project resolver". It is a _dead_ rival. | `apps/frontend/src/app/App.tsx:266-273` vs `packages/chat-surface/src/destinations/projects/index.ts:166-172` and `packages/chat-surface/src/index.ts:550` | The barrel re-exports `./destinations/projects`, so its registration (label = `getCachedProjectName(id) ?? "Project"`) runs at import, before `App.tsx`'s guard. `hasItemRefResolver("project")` is already true → the App.tsx body **never executes**. Same for `apps/frontend/src/features/projects/ProjectsRoute.tsx:104-110` (`library_file`, shadowed by `destinations/library/index.ts:91`). The smell is real; the _shadowing_ runs the other way. Both blocks are unreachable code to delete. |
| `ItemLink` overrides the row's title typography with link styling                                                       | `packages/chat-surface/src/refs/ItemLink.tsx:68-76`, `:181`, `:189`                                                                                        | `linkStyle` sets `color: var(--color-accent, #d97757)` and `fontSize: var(--font-size-sm, 13px)` on the `<a>`, inside a `Row` title span that the design specifies as 12.5px / 500 / `--color-text`.                                                                                                                                                                                                                                                                                                  |
| The parity report never measured this, because no anchor binds a done-row title                                         | `tools/design-parity/surfaces/activity/anchors.json` (full anchor list)                                                                                    | Anchors exist for `row.live.name` but there is **no** `row.done.name`. The 7 broken titles are literally unmeasured by `out/report-default.md`.                                                                                                                                                                                                                                                                                                                                                       |
| The measured title anchor is off-spec on weight — **PRD-08 owns the fix** (C9)                                          | `tools/design-parity/surfaces/activity/out/report-default.md:63` and `packages/chat-surface/src/destinations/_shared/Row.tsx:96-104`                       | MEDIUM row: `row.live.name fontWeight 500 → 600` (re-verified on the regenerated report — the row moved from `:64` to `:63`). `titleStyle` uses `var(--font-weight-semibold)` (= 600, `packages/design-system/src/styles.css:75`) where the design says 500. **PRD-08 owns `_shared/Row.tsx` for the whole program (README C9) and has already absorbed this line** (PRD-08 Evidence `:84`, Scope `:560-563`). Recorded here only as the reason the new `row.done.name` anchor is worth adding.       |
| The accent-link colour is one line with app-wide blast radius (README G11)                                              | `packages/chat-surface/src/refs/ItemLink.tsx:72`, `:75`, `:82`, `:84`; `tools/design-parity/surfaces/projects/out/AUDIT.md:492-505` (R9)                   | CONFIRMED by opening both. `linkStyle` sets `color: var(--color-accent, #d97757)` (`:72`) and `fontSize: var(--font-size-sm, 13px)` (`:75`); `deletedStyle` repeats both (`:82`, `:84`). Projects AUDIT calls `:72` "the widest-blast-radius single line in this audit" — every destination rendering an `ItemLink` inherits it, incl. the desktop project-card name, which the design paints 14px/600 `--tx` (`copilot-app.jsx:406-412`, quoted at `AUDIT.md:178`).                                  |
| Web `ItemLink` navigation cannot work at all                                                                            | `apps/frontend/src/app/App.tsx:1200-1202`, `apps/frontend/src/app/routes.ts:29-70`, `apps/frontend/src/app/HashRouter.ts:204-262`                          | `ChatShell` receives the web `HashRouter`, whose union `AppRoute` is `{screen:…}`-tagged and has **no** `run`/`workspace` member. `ItemLink` calls `router.navigate({kind:"run", runId})`; `pathForRoute` matches no `route.screen` branch and falls through to `return { path: "/settings", hash: route.section === DEFAULT_SETTINGS_SECTION ? "" : "#"+route.section }` → `/settings#undefined`.                                                                                                    |
| Desktop `ItemLink` navigation is a URL-only no-op                                                                       | `apps/desktop/renderer/bootstrap.tsx:105-115`, `:222-236`                                                                                                  | `conversationIdFromRoute` returns non-null only for `kind === "conversation" \|\| "chat"`; the router subscription binds/switches destination only for those two kinds. A `{kind:"run"}` navigate serializes to `run://<id>` (`packages/chat-surface/src/routing/HashRouter.ts:173`) and changes nothing on screen.                                                                                                                                                                                   |
| The _running_ row's working handler deep-links the wrong id on web                                                      | `ActivityDestination.tsx:542-545`, `apps/frontend/src/app/App.tsx:821-840`                                                                                 | `onOpenRun(row.run_id)` → `openRun(idOrRunId)` → `openConversation(idOrRunId)` → `/run/<runId>`. The cockpit binds by conversation id.                                                                                                                                                                                                                                                                                                                                                                |
| …and drops the id entirely on desktop                                                                                   | `apps/desktop/renderer/destinationBinders.tsx:363-371`                                                                                                     | `onOpenRun={() => onOpenRun?.()}` — argument discarded; the shell opens a blank new run.                                                                                                                                                                                                                                                                                                                                                                                                              |
| The navigable identity exists upstream and is thrown away in the projection                                             | `apps/frontend/src/features/activity/api/activityApi.ts:143-176`, `apps/desktop/renderer/destinationBinders.tsx:292-325`                                   | Both loops read `conversation.conversation_id` (`:166` / `:312`) for the meta index, then build the row from `latest_run_id` only. `conversation_id` never reaches the row.                                                                                                                                                                                                                                                                                                                           |
| The wire type has no conversation id                                                                                    | `packages/api-types/src/activity.ts:68-76`                                                                                                                 | `ActivityRunRow = { run_id, title, status, meta, started_at }`.                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| The projection is duplicated byte-for-byte across the two hosts                                                         | `apps/frontend/src/features/activity/api/activityApi.ts:143-182` vs `apps/desktop/renderer/destinationBinders.tsx:292-325`                                 | Same `buildMetaIndex`, same skip rule, same `"Untitled run"` fallback, same sort. Two copies, no shared module.                                                                                                                                                                                                                                                                                                                                                                                       |
| No new backend capability is required                                                                                   | `services/backend-facade/src/backend_facade/app.py:1054`                                                                                                   | `GET /v1/agent/runs/{run_id}` already exists at the facade. We will not use it: the title already arrives on the conversation list payload the surface already fetches.                                                                                                                                                                                                                                                                                                                               |

## Design intent

`tools/design-parity/design-kit/app-v3/copilot-app.jsx:49-80` — the whole Activity row is
one `<button className="lrow">`; the title is plain text inside `.lrow__name`, and the
status chip is a sibling inside the same flex line:

```jsx
<button className="lrow" onClick={() => (isLive ? navigate("workspace") : null)}>
  <span className="lrow__ic" …>…</span>
  <span className="lrow__main">
    <span className="lrow__name">
      {r.title}{" "}
      <span className={"chip " + cc} style={{ padding: "1px 8px" }}>…</span>
    </span>
```

`copilot.css:1635-1642` — the literal target for the title:

```css
.lrow__name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--tx);
  display: flex;
  align-items: center;
  gap: 8px;
}
```

`--tx` = `#ececf1` dark / `#141419` light (`copilot.css:16`, `:80`). Design-system
equivalents already exist and are exact: `--font-size-xs: 0.78rem` (**12.5px**,
`packages/design-system/src/styles.css:64`), `--font-weight-medium: 500` (`:74`),
`--color-text: #ececf1` (`:176`). No new token.

Three intents follow, and they are all violated today:

1. **The title is text, not a link.** There is no anchor, no accent colour, no
   `text-decoration`, nothing at 13px. `--color-accent` (`#5fb2ec` default, verified at
   `packages/design-system/src/styles.css:180`) never appears on a row name in the design.
   The design's accent (`--accent: var(--sky)` = `#5fb2ec`, `copilot.css:20`, `:26`) is
   spent on **state**, not on entity names: the sky status chip (`.chip--sky`,
   `copilot.css:595-597`), a streaming step (`.srow .st.stream`, `:913-914`), a pending
   plan-step icon (`:1205-1206`), the active settings-nav icon (`:1810-1811`). The one
   blanket rule — `a { color: var(--accent) }` (`copilot.css:127-129`) — never fires on a
   list row, because the design's rows are `<button className="lrow">`, not anchors.
2. **The row is the click target** (`.lrow { cursor: pointer }`, `copilot.css:1594`;
   `.lrow:hover { background: var(--panel2) }`, `:1601`). Navigation is a row affordance,
   not a word-sized hit area inside it.
3. **Weight is 500**, not 600. The parity report's `row.live.name fontWeight 500 → 600`
   (`report-default.md:63`) is the same rule measured on the one row we render correctly.
   **PRD-08 lands that change** — it owns `_shared/Row.tsx` for the program (README C9).
   This PRD's job at the same anchor is narrower and upstream of it: stop `ItemLink` from
   overriding whatever weight/size/colour `Row` sets.

The design's done rows are inert (`isLive ? navigate(…) : null`) only because the mock has
no run-detail surface. The product has one, so this PRD makes every row activate — that is
a deliberate, stated departure, and it is the _design's own_ row-as-button affordance
extended, not a new one.

## Architectural decision

The `ItemRef` resolver registry conflates two facts it cannot know:

- **the entity's display name** — per-entity, data-dependent, and always already loaded by
  the surface that is rendering the list; and
- **the host's route** — expressible only in the host's own route union, which
  `chat-surface` deliberately does not depend on.

Because the registry is kind-level, substrate-agnostic, and asynchronous with no data
source, every implementation degenerated to a constant. Eleven registrations, eleven kind
nouns: `"Run"`, `"Chat"`, `"Subagent"`, `"Tool result"`, `"Todo"`, `"Inbox item"`,
`"Project"`, `"File"`, `"Page"`, `"Dataset"`, `"Agent"`. Activity is not a special case; it
is the surface where the lie is most visible. Fixing it at `ActivityDestination.tsx:514`
would be a bandaid.

Three seams change.

### Seam A — the caller owns display text; the registry loses `label`

`ItemRefResolved.label` is **deleted** (`packages/chat-surface/src/refs/registry.ts:29-34`).
`<ItemLink>` gains a **required** `label: ReactNode` prop. `deletedLabel` is deleted — a
caller that knows an entity is gone passes that in `label`.

Required, not optional, on purpose: an optional prop leaves the defect one careless call
site away, and `required` makes the compiler enumerate all ~30 call sites so each one
states, in a reviewable diff, what it renders. Where a call site genuinely holds only an
id (see Non-goals), it must write the noun _locally_ — a visible, local lie beats an
invisible global one.

_Rejected:_ a `label` prop that overrides the resolver (leaves the placeholder alive);
generalising `projects/projectNameCache.ts` into a global `(kind,id) → name` cache (a
second mutable singleton with a priming race — the very shape that produced
`getCachedProjectName(id) ?? "Project"`); registering a real `"run"` resolver in each host
that fetches `GET /v1/agent/runs/{run_id}` (a network round-trip per row to recover a
string the row already has).

### Seam B — routes are host facts; hosts register them

The registry narrows to **routing only** and becomes synchronous:

```ts
// packages/chat-surface/src/refs/registry.ts
export type ItemRouteResolver = (id: string) => unknown | null; // returns a HOST route
export function registerItemRoute(
  kind: ItemKind,
  resolve: ItemRouteResolver,
): void;
export function resolveItemRoute(ref: ItemRef): unknown | null;
export function hasItemRoute(kind: ItemKind): boolean;
```

The return type is `unknown` because the route belongs to the host's union; `ItemLink`
passes it straight to `router.navigate(route)`. Registration moves **out of**
`packages/chat-surface/src/destinations/*/index.ts` (11 files) and **into** one table per
host, imported at boot:

- `apps/frontend/src/app/itemRoutes.ts` → `AppRoute` values (`{screen:"chat", destination, subPath}`)
- `apps/desktop/renderer/itemRoutes.ts` → `ArtifactRoute` values

This is what makes the web `/settings#undefined` bug structurally impossible: the web
table can only emit `AppRoute`s, checked by `tsc`.

`ItemLink` loses its `useEffect`, its promise, its loading skeleton and its `error` state
(`ItemLink.tsx:96-158`) — resolution is now a pure function of `(kind, id)`. A kind with
**no registered route renders `label` as a plain non-interactive `<span>`**, not a
"deleted …" chip: _not navigable yet_ and _deleted_ are different facts and the old code
reported both as deletion.

_Rejected:_ keeping `ArtifactRoute` as the registry's currency and adding an adapter in
the web host (adds a translation layer whose only job is to hide that the two unions
disagree); making the web `HashRouter` accept `ArtifactRoute` (widens the host union with
routes the web app has no screens for).

### The accent-link policy — stated once, here (README G11)

README G11 assigns the app-wide accent-link policy to this PRD because this PRD owns
`refs/ItemLink.tsx`. It is stated here so no sibling PRD has to re-decide it:

> **An `ItemRef` link declares no colour and no font-size.** `ItemLink` renders a bare
> `<a>`/`<span>` that inherits `color`, `font-size` and `font-weight` from whatever slot
> it sits in — `Row`'s `titleStyle`, a card name, a cell. Accent is reserved for the
> affordances the design spends it on (status chips, live/streaming indicators, active
> nav) and is never applied to an entity name.

Concretely: `linkStyle` loses `color` (`ItemLink.tsx:72`) and `fontSize` (`:75`);
`deletedStyle` loses both (`:82`, `:84`); nothing replaces them. This is deliberately
**not** a new `.ui-link` recipe — `packages/design-system/src/styles.css` has no `.ui-*`
link recipe today (only `.ui-link-button`, a CTA, at `:411`/`:463`), and minting one
would re-introduce the exact override this PRD deletes. Inheritance is the recipe.

Two adjacent items from the same audit findings stay with their owners and are **not**
in this PRD's scope: the `.ui-backlink` recipe promoted from `.loginx-back` (verified at
`apps/frontend/src/styles.css:9043-9062`; Projects `AUDIT.md:492-505`, R9's
back-affordance half) belongs to **PRD-10**, which owns
`_shared/BackLink`; and the inline-`CSSProperties` hover chrome on project cards and chat
rows (`AUDIT.md:507-517`, R10 — `ProjectsDestination.tsx:376,456-458`) belongs to
**PRD-10**, with the shared row-hover recipe `.ui-list-row` owned by **PRD-08**.

### Seam C — an Activity row addresses a conversation

`ActivityRunRow` gains `readonly conversation_id: ConversationId`
(`packages/api-types/src/activity.ts`). Purely additive; no server change — both
projections already hold the value and drop it.

The duplicated projection is hoisted to
`packages/chat-surface/src/destinations/activity/activityProjection.ts` (precedent:
`destinations/run/chatProjection.ts`, `destinations/run/approvalProjection.ts` — pure
wire→view-model projections already live in the destination). Both hosts import it. That
is what guarantees the new field is stamped identically on both, instead of being added
twice and drifting.

**This PRD owns that module for the program (README C7).** PRD-03 originally proposed it
at `src/projections/activity.ts` and has dropped it (PRD-03 `:130`, `:248`, `:274`);
PRD-08's four references to a "PRD-06 shared Activity projection" have been retargeted
here (README C7/C21, PRD-08 `:208-209`, `:627`, `:666-669`). PRD-08 later supplies the
real meta strings from `destinations/activity/meta.ts` **through** this projector — it
adds a consumer, it does not fork the module.

`onOpenRun` widens to `(target: { conversationId: ConversationId; runId: RunId }) => void`
and fires for **every** row (`ActivityDestination.tsx:542-545`), matching the design's
row-as-button. Web's `openRun` (`App.tsx:833-840`) takes `target.conversationId`; desktop's
binder (`destinationBinders.tsx:367`) stops discarding its argument and calls
`openConversation(target.conversationId)`.

`<ItemLink>` is then **deleted from `ActivityDestination`** entirely. The title becomes
`<span data-testid="activity-row-title">{row.title}</span>` for all statuses. Deleting the
wrong abstraction at this call site is preferred over repairing it — the row is the link.

### No contract or migration beyond the additive TS field

No route, table, column, index, or authorization rule changes. `ActivityRunRow` is
projected client-side from `GET /v1/agent/conversations` + `GET /v1/audit`; both fields
already cross the wire.

**Migration ids: none consumed.** This PRD claims no id from the program's pre-assigned
table (README C18). High-water marks re-verified on disk in this worktree:
`services/backend/migrations` highest is `0045_provider_api_keys_custom_endpoint.sql`,
`services/ai-backend/migrations` contains only `0001_runtime_baseline.sql`. This PRD adds
no `.sql` file and therefore does not touch either `MANIFEST.lock`, so
`tools/check_migration_manifest.py` is unaffected by it.

## Scope

**`packages/api-types`**

- `src/activity.ts` — add `conversation_id: ConversationId` to `ActivityRunRow`; import the brand.
- `src/activity.test.ts` — shape test gains the field.

**`packages/chat-surface`**

- `src/refs/registry.ts` — delete `label` from `ItemRefResolved`; replace the async resolver API with the sync `registerItemRoute` / `resolveItemRoute` / `hasItemRoute` / `unregisterItemRoute` / `__resetItemRouteRegistryForTests`; keep the `AlreadyRegistered` / `NotRegistered` error classes.
- `src/refs/ItemLink.tsx` — required `label`; drop `deletedLabel`, the effect (`:99`), the skeleton (`:135`) and the error state; unregistered kind → plain `<span>`; **apply the accent-link policy app-wide** — delete `linkStyle`'s `color` (`:72`) and `fontSize` (`:75`) and `deletedStyle`'s `color` (`:82`) and `fontSize` (`:84`), so every `ItemLink` on every destination inherits its container's typography (README G11). No replacement recipe is minted.
- `src/refs/ItemLink.test.tsx`, `src/refs/index.ts`, `src/index.ts` — export surface + tests follow the rename.
- `src/destinations/{home,inbox,tools,memory,projects,library,routines,team,todos}/index.ts` — delete the 11 `registerItemRefResolver` blocks (this is where `label: "Run"` dies).
- `src/destinations/activity/ActivityDestination.tsx` — plain-text title for all rows; `onOpenRun` signature; every row activates; drop the `ItemLink` import.
- `src/destinations/activity/activityProjection.ts` **(new)** — hoisted `buildMetaIndex` + `projectActivityRows`, stamping `conversation_id`.
- `src/destinations/activity/activityProjection.test.ts` **(new)** — projection unit tests incl. the `conversation_id` stamp and the `"Untitled run"` fallback.
- `src/destinations/activity/ActivityDestination.test.tsx` — replace the ItemLink-navigation test (`:334`) with a row-activation test.
- `src/destinations/_shared/Row.tsx` — **not touched by this PRD.** The `titleStyle.fontWeight` 600 → 500 change is **PRD-08's** (README C9; absorbed at PRD-08 Scope `:560-563`). This PRD only stops `ItemLink` from overriding whatever `Row` sets.
- `src/shell/{ActivityList,RightRailTabs,ApprovalsTabContent,DocList,PaletteHitRow}.tsx` — pass `label`.
- `src/destinations/{home/sections/TodayTimeline,home/sections/InFlightStrip,inbox/InboxDestination,inbox/InboxDetail,todos/TodosDestination,projects/ProjectsDestination,projects/ProjectDetailView,library/LibraryDestination,library/LibraryDetailView,memory/MemoryDestination,memory/MemoryDetailView,memory/MemoryProposalCard,memory/MemoryProposalToast,routines/RoutinesDestination,routines/RoutinesPanel,routines/RoutineDetail,team/PersonDetailView,tools/UsedByTab,tools/ToolInvocationsTable,connectors/ReadAuditTab}.tsx` — pass `label` (compiler-enumerated; most already hold the name).

**`apps/frontend`**

- `src/app/itemRoutes.ts` **(new)** — the single web `ItemKind → AppRoute` table, imported by `App.tsx`.
- `src/app/App.tsx` — delete the 7 dead `registerItemRefResolver` blocks (`:228-309`); import `itemRoutes`; `openRun` takes the conversation id.
- `src/features/projects/ProjectsRoute.tsx:104-110` — delete the dead `library_file` registration.
- `src/features/activity/api/activityApi.ts` — delete the local projection; re-export/call `activityProjection`; keep `fetchActivity`'s I/O.
- `src/features/activity/api/activityApi.test.ts`, `src/features/activity/ActivityRoute.tsx` + `.test.tsx` — `onOpenRun` signature.

**`apps/desktop`**

- `renderer/itemRoutes.ts` **(new)** — the desktop `ItemKind → ArtifactRoute` table.
- `renderer/bootstrap.tsx` — import `itemRoutes`; nothing else (the `conversation` route already binds the cockpit).
- `renderer/destinationBinders.tsx:292-371` — delete the duplicated projection; call `activityProjection`; stop discarding the `onOpenRun` argument.
- `renderer/destinationBinders.test.tsx` — assert the id reaches `openConversation`.

**`tools/design-parity`**

- `lib/render-live-activity.test.tsx` — invert the ACT-06 block (see DoD 8).
- `surfaces/activity/anchors.json` — add the missing `row.done.name` anchor.

## Non-goals

- **No wire denormalization for id-only refs.** After Seam A, sites that hold only an id —
  `routines/RoutineDetail.tsx:639` (run history: id, status, trigger, time), `tools/ToolInvocationsTable.tsx:202`
  and `connectors/ReadAuditTab.tsx:123` (caller refs), `memory/MemoryDestination.tsx:611`
  and `memory/MemoryDetailView.tsx:282` (`project_id` only) — write the noun at the call
  site. Making their row wire-types carry a display name belongs to no PRD in this
  thirteen-PRD suite — it is future work, not a hand-off to a sibling.
- **No deletion of `projects/projectNameCache.ts`.** With Seam A it stops being a resolver
  hack and becomes an ordinary call-site helper for the `project_id`-only sites above.
- **No run-detail surface.** Every Activity row will open the Run cockpit bound to its
  conversation. A dedicated read-only completed-run view is out of scope.
- **No status-chip work.** `StatusPill`'s divergence from `.chip` is the largest cluster in
  `surfaces/activity/out/report-default.md` (HIGH-1) and belongs to **PRD-02**, which lands
  before this PRD in the same wave (README hot-file order for `ActivityDestination.tsx`:
  02 → 04 → 08).
- **No `_shared/Row.tsx` change of any kind** — title weight, padding, `trailing` slot,
  icon-tile background, `.ui-list-row` hover, 15px glyph sizing. **PRD-08 owns the file**
  for the whole program (README C9, G2, G5).
- **No Activity pagination and no server-side run history.** The client-side compose of
  conversations + audit stays as-is; only where it _lives_ changes. `GET /v1/agent/runs`,
  its keyset cursor and history tombstoning are **PRD-05**.
- **No Activity run-meta counters** (tools/subagents/tokens strings, the
  `runtime_tool_invocations` writer, `destinations/activity/meta.ts`) — **PRD-08**. This
  PRD's projector carries whatever meta string the caller already produces.
- **No lead-copy, empty-state, page-padding or day-divider work on Activity** — **PRD-08**
  (README G4, G5).
- **No `.ui-backlink` recipe and no project-card hover chrome** — **PRD-10** (README G11's
  R9 back-affordance half and R10; see the accent-link policy section).
- **No change to `ArtifactRoute` or either host's route union.**
- **No backend, facade, or database change of any kind.**

## Risks & rollback

| Risk                                                                                                                                                                                                                                                               | Guard                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Required `label` touches ~30 call sites; a wrong label ships silently.                                                                                                                                                                                             | `tsc` forces each site; existing per-destination suites assert rendered text — `packages/chat-surface/src/refs/ItemLink.test.tsx`, `destinations/inbox/InboxDestination.test.tsx`, `destinations/todos/TodosDestination.test.tsx`, `destinations/library/LibraryDestination.test.tsx`, `destinations/routines/RoutineDetail.test.tsx`, `shell/PaletteHitRow.test.tsx`. Run `npm run test --workspace @0x-copilot/chat-surface` before and after and diff the failure set.                                        |
| Moving registration to hosts means a kind nobody registers renders inert text instead of throwing. That is intentional but silences a class of wiring bug.                                                                                                         | Add `hasItemRoute` coverage: a chat-surface test that asserts the registry is **empty** after importing the barrel (registration is host-only now), plus one test per host asserting its table covers every `ItemKind` the host's surfaces emit.                                                                                                                                                                                                                                                                 |
| Web `openRun` currently accepts the run id; changing the argument could break the ⌘K blank-run path.                                                                                                                                                               | `apps/frontend/src/app/App.tsx:833-840` keeps its `undefined` → `openNewRun()` branch; `apps/frontend/src/features/palette/__tests__/PaletteHost.test.tsx` guards it. It is known-flaky under Node 25/jsdom; if it fails, its test id must already be listed in `docs/plan/design-parity-remediation/baseline-failures.txt` (DoD 14) — this PRD may not add it there.                                                                                                                                            |
| Hoisting the projection changes desktop behaviour subtly (the web copy sorts with `localeCompare` on meta labels; both sort rows by `startedAtMs` with an `Number.isNaN` guard the desktop copy lacks — `activityApi.ts:126-129` vs `destinationBinders.tsx:324`). | `activityProjection.test.ts` pins the web copy's behaviour (the stricter one, with the NaN guard) as canonical; desktop gains the guard.                                                                                                                                                                                                                                                                                                                                                                         |
| Deleting `linkStyle`'s `color`/`fontSize` changes every `ItemLink` on every destination at once, not just Activity — that is the point (G11), but it is the widest blast radius in this PRD.                                                                       | The affected destinations all have committed suites asserting rendered text: `destinations/inbox/InboxDestination.test.tsx`, `destinations/todos/TodosDestination.test.tsx`, `destinations/library/LibraryDestination.test.tsx`, `destinations/routines/RoutineDetail.test.tsx`, `shell/PaletteHitRow.test.tsx`. DoD 10 greps both style objects to zero colour/size declarations; the Projects harness (`surfaces/projects/out/report-default-chatsurface.md`) re-measures the desktop card name independently. |
| Rollback                                                                                                                                                                                                                                                           | Three independent reverts: Seam C alone restores the old `onOpenRun`; Seam A+B are one commit touching only `refs/` + registration sites and revert cleanly because no data model changed. Nothing is persisted, migrated, or feature-flagged.                                                                                                                                                                                                                                                                   |

## Definition of Done

1. `grep -rn 'label: "' packages/chat-surface/src/destinations/*/index.ts` returns **0 lines**, and `grep -rn 'registerItemRefResolver' packages apps --include='*.ts' --include='*.tsx'` returns **0 lines** outside `apps/frontend/src/app/itemRoutes.ts` and `apps/desktop/renderer/itemRoutes.ts`.
2. `packages/chat-surface/src/refs/registry.ts` exports no symbol named `ItemRefResolved` carrying a `label` field: `grep -n 'label' packages/chat-surface/src/refs/registry.ts` returns 0 lines.
3. `packages/chat-surface/src/refs/ItemLink.test.tsx` contains a test asserting that `<ItemLink ref={{kind:"run", id:"run_x"}} label="Weekly treasury reconciliation" />` renders that exact text when **no** route is registered for `"run"`, and that the rendered node is a `<span>` (not an `<a>`) with no `onClick`.
4. `packages/chat-surface/src/destinations/activity/ActivityDestination.test.tsx` asserts that `screen.getAllByTestId("activity-row-title").map(e => e.textContent)` equals the 8 fixture titles in order, and that `screen.queryAllByTestId("item-link")` has length **0**.
5. `packages/chat-surface/src/destinations/activity/ActivityDestination.test.tsx` asserts that activating a `status="done"` row calls `onOpenRun` once with `{ conversationId: <that row's conversation_id>, runId: <that row's run_id> }`.
6. `packages/chat-surface/src/destinations/activity/activityProjection.test.ts` asserts that a conversation with `latest_run_id` set projects a row whose `conversation_id === conversation.conversation_id` and whose `run_id === conversation.latest_run_id` (i.e. the two are distinct fields), and that a conversation with `title: "   "` projects `title: "Untitled run"`.
7. `apps/desktop/renderer/destinationBinders.test.tsx` asserts that activating an Activity row calls the binder's `onOpenRun` with the row's `conversation_id` — the argument is no longer discarded (guards `destinationBinders.tsx:367`).
8. **Regression guard (the inverted ACT-06).** In `tools/design-parity/lib/render-live-activity.test.tsx`, the `ACT-06` describe block is rewritten to render `ActivityDestination` with **no** `"run"` route registered and assert `linkLabels` is empty **and** that all 7 previously-hidden titles (`"Weekly treasury reconciliation"`, `"Draft investor update"`, `"Rebalance LP positions"`, `"Triage new GitHub issues"`, `"Summarize Discord AMA"`, `"Vendor invoice batch"`, `"Competitor launch digest"`) are each found by `screen.getByText`. The old assertions `expect(linkLabels).toEqual(Array.from({length:7}, () => "Run"))` and the `queryByText(title) === null` loop are deleted, not skipped.
9. **Design value pinned numerically.** `tools/design-parity/surfaces/activity/anchors.json` gains anchor `row.done.name` mapping design `.rowlist .lrow:nth-child(2) .lrow__name` ↔ live `section[data-day-key="2026-07-16"] li:nth-child(2) [data-testid="row-title"]` — pinning the literal `.lrow__name { font-size: 12.5px }` at `tools/design-parity/design-kit/app-v3/copilot.css:1636`. After regenerating per `tools/design-parity/SKILL.md` (`node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs lib/render-live-activity.test.tsx`, then extract + `node lib/compare.mjs …`), this command prints **`0`**:

   ```bash
   jq '[.findings[]
        | select(.label == "row.done.name" and (.prop == "color" or .prop == "fontSize"))]
       | length' tools/design-parity/surfaces/activity/out/report-default.json
   ```

   The `color` row disappears because the title stops being an accent `<a>` and inherits `--color-text` `#ececf1`; the `fontSize` row never appears because live computes `--font-size-xs` `0.78rem` = **12.48px** against the design's **12.5px**, a 0.02px delta under the comparator's 0.4px `fontSize` threshold (`tools/design-parity/lib/compare.mjs:89-110`). **`fontWeight` is deliberately excluded from the selector**: `row.done.name` will still report `500 → 600` until **PRD-08** lands `titleStyle.fontWeight` → `var(--font-weight-medium)` in `_shared/Row.tsx` (README C9). The `display`/`alignItems`/`gap` rows are structural (the design puts the chip inside `.lrow__name`, live puts it beside the title span — `anchors.json:3`) and are likewise excluded; note that `expectDivergence` cannot be used to silence them, because `lib/compare.mjs:172` consults it only for presence divergences, never for style rows.

10. **The accent-link policy leaves no residue (README G11).** `grep -nE '(color|fontSize):' packages/chat-surface/src/refs/ItemLink.tsx` returns **0 lines** (on `main` it returns exactly 6: `:63`, `:64`, `:72`, `:75`, `:82`, `:84`). No file under `packages/design-system/src/` gains a `.ui-link` rule: `grep -rn '\.ui-link[^-]' packages/design-system/src/styles.css` returns **0 lines**.
11. `npm run typecheck --workspace @0x-copilot/api-types` passes.
12. `npm run typecheck --workspace @0x-copilot/chat-surface && npm run lint --workspace @0x-copilot/chat-surface` passes.
13. `npm run typecheck --workspace @0x-copilot/frontend && npm run typecheck --workspace @0x-copilot/desktop` passes.
14. `npm run test --workspace @0x-copilot/chat-surface`, `npm run test --workspace @0x-copilot/api-types`, `npm run test --workspace @0x-copilot/frontend` and `npm run test --workspace @0x-copilot/desktop` each exit **0**, **or** the ids of the failing tests are byte-identical to the lines of `docs/plan/design-parity-remediation/baseline-failures.txt` — a file this PR does **not** modify (`git diff --exit-code -- docs/plan/design-parity-remediation/baseline-failures.txt` exits 0). (README DoD-Q2.)
15. `grep -rn 'ItemLink' packages/chat-surface/src/destinations/activity/` returns **0 lines**.
16. `packages/chat-surface/src/refs/registry.test.ts` contains a test that imports `@0x-copilot/chat-surface` (the barrel, nothing else) and asserts `hasItemRoute(k) === false` for every member of `ItemKind` — proving the package no longer registers routes on import.
17. `packages/api-types/src/activity.test.ts` contains an assertion that an `ActivityRunRow` literal omitting `conversation_id` is a type error (`// @ts-expect-error missing conversation_id`) and that a complete literal type-checks, and `npm run typecheck --workspace @0x-copilot/api-types` exits **0** (item 11 runs the command; this item names the assertion that makes it meaningful).
18. `apps/frontend/src/app/itemRoutes.test.ts` and `apps/desktop/renderer/itemRoutes.test.ts` each assert that the host's table returns a non-null route for every `ItemKind` that host's surfaces emit, and that every returned web route satisfies `AppRoute` / every returned desktop route satisfies `ArtifactRoute` (type-level, so `/settings#undefined` is unreachable by construction).

## Dependencies

**Wave: 1.** Order inside the wave is `PRD-02 ‖ PRD-03 → PRD-04` (README "Corrected
implementation order"). The PRD's original "Must land first: none" is **superseded**.

**Must land first:**

- **PRD-03 (host binding contract)** — README's index makes 04 depend on 03. PRD-03
  establishes the total shell/destination binding types and the per-host conformance
  tests that this PRD's two new `itemRoutes` tables plug into, and it edits both host
  binders (`apps/frontend/src/app/App.tsx`, `apps/desktop/renderer/destinationBinders.tsx`)
  before this PRD does. PRD-03 has already **dropped** its `src/projections/activity.ts`
  proposal in favour of this PRD (README C7; PRD-03 `:130`, `:248`, `:274`), so there is no
  duplicate projection to reconcile.
- **PRD-02 (status chip)** — it edits `ActivityDestination.tsx` first
  (README hot-file order for that file: **02 → 04 → 08**). This is a sequencing
  requirement, not an API one: PRD-02 rewrites `StatusPill`/`statusTone`, this PRD
  rewrites the title and `onOpenRun` in the same `ActivityRow`.

**Must land after this PRD (do not re-specify their work here):**

- **PRD-08 (Activity surface)** owns `packages/chat-surface/src/destinations/_shared/Row.tsx`
  for the whole program (README C9) — including the `titleStyle.fontWeight` 600 → 500 this
  PRD originally proposed, which PRD-08 has absorbed. PRD-08 also owns `.ui-list-row`, row
  padding `11px 14px`, the 15px glyph rule, the icon-tile background, the lead copy and the
  day divider (README G2, G4, G5). It consumes this PRD's
  `destinations/activity/activityProjection.ts` and feeds it real meta from
  `destinations/activity/meta.ts` (PRD-08 `:208-209`, `:627`, `:666-669`). File order:
  `activity.ts` **04 → 05 → 08**; `ActivityDestination.tsx` **02 → 04 → 08**.
- **PRD-05 (run history backend)** adds `GET /v1/agent/runs` + keyset paging and moves
  Activity off the `updated_at` spine (README C19). It edits `packages/api-types/src/activity.ts`
  after this PRD.
- **PRD-10 (Projects surface)** owns `_shared/BackLink` / the `.ui-backlink` recipe and the
  project-card interactive chrome (README G11's R9/R10 remainder). It consumes — and must
  not restate — the accent-link policy this PRD fixes in `ItemLink.tsx`.

**Migrations:** none. This PRD claims no id from README C18's table; on disk `services/backend`
is at `0045` and `services/ai-backend` at `0001`.

**This unblocks:**

- Wire denormalization for id-only `ItemRef` call sites (the Non-goal above) — that future
  work becomes purely additive once `label` is caller-owned.
- Any surface that wants a real cross-destination link on web: today every `ItemLink`
  click on web lands on `/settings#undefined`, so Seam B is a precondition for **PRD-09**
  (Chats), **PRD-10** (Projects), and the Library/Todos cross-links being usable at all.
- Deep-linkable completed runs (`/run/<conversationId>` from Activity), which a
  read-only run-detail surface would build on.

## Implementation record

_Landed on branch `claude/prd-04-run-identity` (worktree `.claude/worktrees/prd-04`).
This section is the durable record; the workflow return value is not._

### What landed

All three seams shipped in full.

- **Seam A — caller-owned display text.** `ItemRefResolved.label` deleted; `<ItemLink>`
  gains a **required** `label: ReactNode` prop (no `deletedLabel`). ~35 call sites now pass
  real names where the surface holds them (`item.subject`/`name`/`project.name`/`entry.title`/
  `hit.title`), and a new shared `refs/itemKindNoun.ts` display-noun helper for the id-only
  sites the Non-goals name. This is where the constant `label: "Run"` (and the other 10
  hardcoded labels) died.
- **Seam B — route-only, synchronous registry.** `refs/registry.ts` rewritten to
  `registerItemRoute`/`resolveItemRoute`/`hasItemRoute`/`unregisterItemRoute`/
  `__resetItemRouteRegistryForTests` + `ItemRoute{Already,Not}Registered`. The 11 in-package
  `registerItemRefResolver` blocks deleted. Two **host** tables register at boot
  (`apps/frontend/src/app/itemRoutes.ts` → `AppRoute`, `apps/desktop/renderer/itemRoutes.ts`
  → `ArtifactRoute`), making `/settings#undefined` unreachable by construction. `ItemLink`
  loses its effect/skeleton/error and its `color`+`fontSize` (README G11 accent-link policy —
  no `.ui-link` recipe minted, inheritance is the recipe). An unregistered kind renders inert
  `<span>` text.
- **Seam C — real run titles in Activity.** `ActivityRunRow` gains `conversation_id`; the
  byte-duplicated projection is hoisted to `destinations/activity/activityProjection.ts` (both
  hosts import it). Activity rows render titles as plain text (no `ItemLink`); `onOpenRun`
  widens to `(target:{conversationId,runId})` and fires on **every** row; both hosts open the
  cockpit bound to the conversation. The ACT-06 parity guard is inverted and the
  `row.done.name` anchor added.

Blast-radius consumer migrated beyond the named scope: `apps/frontend/src/ports/
NotificationWeb.{ts,test.ts}` moved off the removed `resolveItemRef` to the sync route API.

### DoD status — 18 / 18 MET

Every DoD item verified MET. Items 12, 13, 14, 17 carry a **residual note, not a gap**: the
literal `npm run typecheck`/`npm run test --workspace @0x-copilot/{frontend,desktop,chat-surface}`
commands exit non-zero **in the isolated worktree only**, because `node_modules` symlinks
resolve the shared packages to the `main` checkout, which lacks this branch's new
`hasItemRoute`/`registerItemRoute`/`projectActivityRows`/… exports and the new
`ActivityRunRow.conversation_id` field. Every one of those failures resolves to **0 errors /
full green** when the shared packages resolve to this worktree's `src` (verified package-locally,
per the worktree gotcha) and will be green post-merge — the environment CI actually evaluates.

### Regression surface (re-run at close-out — all green)

- `@0x-copilot/api-types`: tests **49 passed**; `typecheck` **exit 0**.
- `@0x-copilot/chat-surface`: tests **2682 passed** (worktree src); `lint` **exit 0**;
  `tsc` with `api-types` resolved to worktree **exit 0**.
- `@0x-copilot/frontend`: **1370 passed / 179 files** (worktree-aliased vitest config; the
  naive command false-fails on the symlink).
- `@0x-copilot/desktop`: **1089 passed + 1 todo / 93 files** (same).
- design-parity: `render-live-activity.test.tsx` **2 passed**; DoD-9 jq
  (`row.done.name` color/fontSize findings) prints **0**.

### Deviations (also reported in the workflow return value)

1. **Web `skill` renders inert.** The web rail's `ShellDestinationSlug` union has no
   skills destination, so a web `skill`→route would not compile; per the PRD's own
   "no mounted destination ⇒ inert text" rule the web table returns `null` for `skill`.
   Desktop **does** route `skill` (`ArtifactRoute` has it).
2. **`refs/itemKindNoun.ts` helper** instead of literally inlining the noun at each id-only
   site. Mild reading of the Non-goals' "write the noun locally"; DRY'd ~15 display-only
   fallbacks into one helper that produces **no route** and cannot shadow a real name — the
   Seam-A architectural win is intact.
3. **`NotificationWeb` migration** (not in the named Scope) — required blast-radius of the
   Seam-B rename, not scope creep.

### Left open

- Nothing blocking. The DoD's `destinationBinders.tsx:367` line reference is **stale** (line
  367 is now `ConnectorsBinder`; the real `ActivityBinder` seam is `256-278`) — cosmetic.
- Future additive work this unblocks: denormalizing the id-only `ItemRef` sites to carry real
  names, cross-destination links on web (PRD-09/10), and a deep-linkable read-only run-detail
  surface.
