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
| The measured title anchor is off-spec on weight                                                                         | `tools/design-parity/surfaces/activity/out/report-default.md:64` and `packages/chat-surface/src/destinations/_shared/Row.tsx:96-104`                       | MEDIUM row: `row.live.name fontWeight 500 → 600`. `titleStyle` uses `var(--font-weight-semibold)` (= 600, `packages/design-system/src/styles.css:75`) where the design says 500.                                                                                                                                                                                                                                                                                                                      |
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
   `text-decoration`, nothing at 13px. `--color-accent` (`#5fb2ec` default,
   `styles.css:180`) never appears on a row name in the design.
2. **The row is the click target** (`.lrow { cursor: pointer }`, `copilot.css:1594`;
   `.lrow:hover { background: var(--panel2) }`, `:1601`). Navigation is a row affordance,
   not a word-sized hit area inside it.
3. **Weight is 500**, not 600. The parity report's `row.live.name fontWeight 500 → 600`
   (`report-default.md:64`) is the same rule measured on the one row we render correctly.

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

## Scope

**`packages/api-types`**

- `src/activity.ts` — add `conversation_id: ConversationId` to `ActivityRunRow`; import the brand.
- `src/activity.test.ts` — shape test gains the field.

**`packages/chat-surface`**

- `src/refs/registry.ts` — delete `label` from `ItemRefResolved`; replace the async resolver API with the sync `registerItemRoute` / `resolveItemRoute` / `hasItemRoute` / `unregisterItemRoute` / `__resetItemRouteRegistryForTests`; keep the `AlreadyRegistered` / `NotRegistered` error classes.
- `src/refs/ItemLink.tsx` — required `label`; drop `deletedLabel`, the effect, the skeleton and the error state; unregistered kind → plain `<span>`; remove `linkStyle`'s `fontSize` and `color` overrides so the title inherits `Row`'s typography.
- `src/refs/ItemLink.test.tsx`, `src/refs/index.ts`, `src/index.ts` — export surface + tests follow the rename.
- `src/destinations/{home,inbox,tools,memory,projects,library,routines,team,todos}/index.ts` — delete the 11 `registerItemRefResolver` blocks (this is where `label: "Run"` dies).
- `src/destinations/activity/ActivityDestination.tsx` — plain-text title for all rows; `onOpenRun` signature; every row activates; drop the `ItemLink` import.
- `src/destinations/activity/activityProjection.ts` **(new)** — hoisted `buildMetaIndex` + `projectActivityRows`, stamping `conversation_id`.
- `src/destinations/activity/activityProjection.test.ts` **(new)** — projection unit tests incl. the `conversation_id` stamp and the `"Untitled run"` fallback.
- `src/destinations/activity/ActivityDestination.test.tsx` — replace the ItemLink-navigation test (`:334`) with a row-activation test.
- `src/destinations/_shared/Row.tsx` — `titleStyle.fontWeight` → `var(--font-weight-medium)` (see Dependencies: yields to a sibling PRD if one owns this file's typography).
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
  site. Making their row wire-types carry a display name is a separate PRD.
- **No deletion of `projects/projectNameCache.ts`.** With Seam A it stops being a resolver
  hack and becomes an ordinary call-site helper for the `project_id`-only sites above.
- **No run-detail surface.** Every Activity row will open the Run cockpit bound to its
  conversation. A dedicated read-only completed-run view is out of scope.
- **No status-chip work.** `StatusPill`'s divergence from `.chip` is the largest cluster in
  `surfaces/activity/out/report-default.md` (HIGH-1) and belongs to its own PRD.
- **No Activity pagination / server-side `GET /v1/activity`.** The client-side compose of
  conversations + audit stays as-is; only where it _lives_ changes.
- **No change to `ArtifactRoute` or either host's route union.**
- **No backend, facade, or database change of any kind.**

## Risks & rollback

| Risk                                                                                                                                                                                                                                                               | Guard                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Required `label` touches ~30 call sites; a wrong label ships silently.                                                                                                                                                                                             | `tsc` forces each site; existing per-destination suites assert rendered text — `packages/chat-surface/src/refs/ItemLink.test.tsx`, `destinations/inbox/InboxDestination.test.tsx`, `destinations/todos/TodosDestination.test.tsx`, `destinations/library/LibraryDestination.test.tsx`, `destinations/routines/RoutineDetail.test.tsx`, `shell/PaletteHitRow.test.tsx`. Run `npm run test --workspace @0x-copilot/chat-surface` before and after and diff the failure set. |
| Moving registration to hosts means a kind nobody registers renders inert text instead of throwing. That is intentional but silences a class of wiring bug.                                                                                                         | Add `hasItemRoute` coverage: a chat-surface test that asserts the registry is **empty** after importing the barrel (registration is host-only now), plus one test per host asserting its table covers every `ItemKind` the host's surfaces emit.                                                                                                                                                                                                                          |
| Web `openRun` currently accepts the run id; changing the argument could break the ⌘K blank-run path.                                                                                                                                                               | `apps/frontend/src/app/App.tsx:833-840` keeps its `undefined` → `openNewRun()` branch; `apps/frontend/src/features/palette/__tests__/PaletteHost.test.tsx` guards it (known-flaky under Node 25/jsdom — record the pre-change result).                                                                                                                                                                                                                                    |
| Hoisting the projection changes desktop behaviour subtly (the web copy sorts with `localeCompare` on meta labels; both sort rows by `startedAtMs` with an `Number.isNaN` guard the desktop copy lacks — `activityApi.ts:126-129` vs `destinationBinders.tsx:324`). | `activityProjection.test.ts` pins the web copy's behaviour (the stricter one, with the NaN guard) as canonical; desktop gains the guard.                                                                                                                                                                                                                                                                                                                                  |
| `Row.tsx` `fontWeight` is shared by Chats / Projects / Library / Tools rows.                                                                                                                                                                                       | The design uses one `.lrow__name` recipe for all of them (`copilot.css:1635`), so 500 is right everywhere; if a sibling PRD owns this file, drop the line (Dependencies).                                                                                                                                                                                                                                                                                                 |
| Rollback                                                                                                                                                                                                                                                           | Three independent reverts: Seam C alone restores the old `onOpenRun`; Seam A+B are one commit touching only `refs/` + registration sites and revert cleanly because no data model changed. Nothing is persisted, migrated, or feature-flagged.                                                                                                                                                                                                                            |

## Definition of Done

1. `grep -rn 'label: "' packages/chat-surface/src/destinations/*/index.ts` returns **0 lines**, and `grep -rn 'registerItemRefResolver' packages apps --include='*.ts' --include='*.tsx'` returns **0 lines** outside `apps/frontend/src/app/itemRoutes.ts` and `apps/desktop/renderer/itemRoutes.ts`.
2. `packages/chat-surface/src/refs/registry.ts` exports no symbol named `ItemRefResolved` carrying a `label` field: `grep -n 'label' packages/chat-surface/src/refs/registry.ts` returns 0 lines.
3. `packages/chat-surface/src/refs/ItemLink.test.tsx` contains a test asserting that `<ItemLink ref={{kind:"run", id:"run_x"}} label="Weekly treasury reconciliation" />` renders that exact text when **no** route is registered for `"run"`, and that the rendered node is a `<span>` (not an `<a>`) with no `onClick`.
4. `packages/chat-surface/src/destinations/activity/ActivityDestination.test.tsx` asserts that `screen.getAllByTestId("activity-row-title").map(e => e.textContent)` equals the 8 fixture titles in order, and that `screen.queryAllByTestId("item-link")` has length **0**.
5. `packages/chat-surface/src/destinations/activity/ActivityDestination.test.tsx` asserts that activating a `status="done"` row calls `onOpenRun` once with `{ conversationId: <that row's conversation_id>, runId: <that row's run_id> }`.
6. `packages/chat-surface/src/destinations/activity/activityProjection.test.ts` asserts that a conversation with `latest_run_id` set projects a row whose `conversation_id === conversation.conversation_id` and whose `run_id === conversation.latest_run_id` (i.e. the two are distinct fields), and that a conversation with `title: "   "` projects `title: "Untitled run"`.
7. `apps/desktop/renderer/destinationBinders.test.tsx` asserts that activating an Activity row calls the binder's `onOpenRun` with the row's `conversation_id` — the argument is no longer discarded (guards `destinationBinders.tsx:367`).
8. **Regression guard (the inverted ACT-06).** In `tools/design-parity/lib/render-live-activity.test.tsx`, the `ACT-06` describe block is rewritten to render `ActivityDestination` with **no** `"run"` route registered and assert `linkLabels` is empty **and** that all 7 previously-hidden titles (`"Weekly treasury reconciliation"`, `"Draft investor update"`, `"Rebalance LP positions"`, `"Triage new GitHub issues"`, `"Summarize Discord AMA"`, `"Vendor invoice batch"`, `"Competitor launch digest"`) are each found by `screen.getByText`. The old assertions `expect(linkLabels).toEqual(Array.from({length:7}, () => "Run"))` and the `queryByText(title) === null` loop are deleted, not skipped.
9. **Design value pinned numerically.** `tools/design-parity/surfaces/activity/anchors.json` gains anchor `row.done.name` mapping design `.rowlist .lrow:nth-child(2) .lrow__name` ↔ live `section[data-day-key="2026-07-16"] li:nth-child(2) [data-testid="row-title"]`, and after re-running the harness (`node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs lib/render-live-activity.test.tsx`, then extract + `node lib/compare.mjs …` per `tools/design-parity/SKILL.md`) the regenerated `surfaces/activity/out/report-default.md` shows, for `row.done.name`, **`fontSize` 12.5px design vs 12.48px live**, **`fontWeight` 500 on both sides**, and **no `color` row** — i.e. 0 HIGH and 0 MEDIUM rows for `row.done.name` other than the known global `+0.6px` root-font-size skew.
10. The same regenerated report shows `row.live.name | fontWeight` **absent** from the MEDIUM table (was `500 → 600` at `report-default.md:64`).
11. `npm run typecheck --workspace @0x-copilot/api-types` passes.
12. `npm run typecheck --workspace @0x-copilot/chat-surface && npm run lint --workspace @0x-copilot/chat-surface` passes.
13. `npm run typecheck --workspace @0x-copilot/frontend && npm run typecheck --workspace @0x-copilot/desktop` passes.
14. `npm run test --workspace @0x-copilot/chat-surface`, `npm run test --workspace @0x-copilot/api-types`, `npm run test --workspace @0x-copilot/frontend`, `npm run test --workspace @0x-copilot/desktop` pass, with the sole permitted exception of failures recorded as pre-existing in the same PR description (capture the baseline on `origin/main` first).
15. `grep -rn 'ItemLink' packages/chat-surface/src/destinations/activity/` returns **0 lines**.
16. A chat-surface test asserts `hasItemRoute(k) === false` for every `ItemKind` after importing `@0x-copilot/chat-surface` alone — proving the package no longer registers routes on import.

## Dependencies

**Must land first:** none. This PRD is self-contained (no backend, facade, or schema
change) and does not consume any sibling PRD's output.

**Coordinate with:**

- Any sibling PRD that owns `packages/chat-surface/src/destinations/_shared/Row.tsx`
  typography (the `fontWeight 500 → 600` MEDIUM at `report-default.md:64` and the
  `Row.tsx:96-104` `titleStyle`). If one exists, **drop the `Row.tsx` line from this PRD's
  Scope and DoD 10**; keep everything else.
- Any sibling PRD that hoists the duplicated Activity projection out of
  `activityApi.ts` / `destinationBinders.tsx`. If one lands first, this PRD reduces to
  adding `conversation_id` to the already-shared module (Scope's
  `activityProjection.ts` bullet collapses; DoD 6 still applies).
- The Activity status-chip PRD (`StatusPill` vs `.chip`, HIGH-1 in
  `surfaces/activity/out/report-default.md`) also edits `ActivityDestination.tsx` — expect
  a merge in `ActivityRow`. Land whichever is ready first; the conflict is textual, not
  semantic.

**This unblocks:**

- Wire denormalization for id-only `ItemRef` call sites (the Non-goal above) — that PRD
  becomes purely additive once `label` is caller-owned.
- Any surface that wants a real cross-destination link on web: today every `ItemLink`
  click on web lands on `/settings#undefined`, so Seam B is a precondition for Chats,
  Projects, Library and Todos cross-links being usable at all.
- Deep-linkable completed runs (`/run/<conversationId>` from Activity), which a
  read-only run-detail surface would build on.
