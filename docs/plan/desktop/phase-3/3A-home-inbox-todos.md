# Phase 3.A: dest-home-inbox-todos

## Vision

Three leaf destinations in the Atlas shell — Home (landing dashboard with
pinned chats, recent runs, favorite tools), Inbox (notification list with
filters: All / Mentions / Approvals / Errors), Todos (todo list with
filters: Open / Done / All). Read-mostly views that fetch through the
`Transport` port handed in by the shell and navigate through the
`Router<ArtifactRoute>` port. Both ports are frozen — destinations only
consume them.

DRY principles applied:

- **Single transport.** All three destinations call `transport.request(...)`
  via the existing `useTransport()` hook. No ad-hoc fetch wrappers, no
  per-page client. The shell injects the Transport via `TransportProvider`.
- **Single router.** "Open this card" / "open this notification" go through
  `useRouter<ArtifactRoute>().navigate(...)` — never through bare URL
  manipulation. Mapping rules are deliberately identical across destinations
  (home, library, projects all use the same workspace/run/skill/etc. mappings)
  so a future `ArtifactRoute` extension is a mechanical search/replace.
- **One state machine per destination.** A small `'loading' | 'error' |
'ready'` view-state, derived inline from the fetch result. No
  `useEffect`-derived state, no redux, no global store.
- **One filter shape.** Inbox and Todos both express their filter set as a
  `readonly` tuple of `{ slug, label, emptyTitle, emptyHint }` descriptors
  so the tab-bar component and the empty-state copy stay in lockstep.
- **Inline styles only.** Consistent with `AppRail`, `ContextPanel`,
  `CommandPalette`, and sibling destinations `projects` / `library` —
  design-system tokens for fonts/colours are not yet wired into
  chat-surface inline-style consumers.
- **No comments by default; functional components only; no `any`.**

## Status

- Status: in-progress
- Agent slug: `dest-home-inbox-todos`
- Branch: `desktop/phase-3-home-inbox-todos`
- Worktree: `.claude/worktrees/agent-a2c9bb01b42c359f5`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-3/3A-home-inbox-todos.md` — this file.
- `packages/chat-surface/src/destinations/home/HomeDestination.tsx`
- `packages/chat-surface/src/destinations/home/HomeDestination.test.tsx`
- `packages/chat-surface/src/destinations/home/index.ts`
- `packages/chat-surface/src/destinations/inbox/InboxDestination.tsx`
- `packages/chat-surface/src/destinations/inbox/InboxDestination.test.tsx`
- `packages/chat-surface/src/destinations/inbox/index.ts`
- `packages/chat-surface/src/destinations/todos/TodosDestination.tsx`
- `packages/chat-surface/src/destinations/todos/TodosDestination.test.tsx`
- `packages/chat-surface/src/destinations/todos/index.ts`

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — orchestrator-owned. The barrel
  wiring happens at merge time.
- `packages/chat-surface/src/shell/**` — Agent 1-B / 2-A territory.
- Any other destination (`chats`, `agents`, `library`, `tools`, `projects`,
  `connectors`, `team`, `memory`).
- `Transport` / `Router` / `KeyValueStore` ports — frozen.

## Functional requirements

### Home destination

- [x] FR-H1 — `HomeDestination` is a functional React component, no props.
      On mount issues exactly one `transport.request<HomePayload>({ method:
'GET', path: '/v1/home' })`.
- [x] FR-H2 — `HomePayload = { pinned: Pinned[]; recent_runs: Run[];
favorites: Favorite[] }`. Each list renders as its own section card
      with a heading, the card list, and a per-section empty hint when the
      list is empty.
- [x] FR-H3 — Renders one of four states overall: `loading` (skeleton with
      three section cards), `error` (centered error panel with Retry that
      re-issues the request), `ready` (the three populated sections), and
      the per-section `empty` hint inside the `ready` state when a section
      list is empty.
- [x] FR-H4 — Clicking a pinned chat calls
      `router.navigate({ kind: 'chat', conversationId: pinned.conversationId })`;
      clicking a recent run calls
      `router.navigate({ kind: 'run', runId: run.runId })`; clicking a
      favorite tool calls `router.navigate({ kind: 'skill', skillId:
favorite.skillId })`. Each affordance is a `<button>` with explicit
      `aria-label`.

### Inbox destination

- [x] FR-I1 — `InboxDestination` is a functional React component, no props.
      Maintains a single `filter` state (`'all' | 'mentions' | 'approvals'
| 'errors'`) and re-fetches whenever it changes.
- [x] FR-I2 — Four tabs at top with counts derived from the server payload's
      `counts` map: `All`, `Mentions`, `Approvals`, `Errors`. Tabs use
      `role="tablist"` / `role="tab"` and `aria-selected`.
- [x] FR-I3 — Fetch shape:
      `transport.request<InboxPayload>({ method: 'GET', path: '/v1/inbox',
query: { filter } })`. `InboxPayload = { items: InboxItem[]; counts:
Record<InboxFilter, number> }`.
- [x] FR-I4 — Renders one of four states per filter: `loading` (skeleton
      with 5 placeholder rows), `error` (panel + Retry), `empty` (per-filter
      copy), `ready` (vertical list of notification rows).
- [x] FR-I5 — Each notification row shows: kind badge (Mention / Approval /
      Error), title, source, relative timestamp, and a mark-as-read button.
      Clicking mark-as-read calls
      `transport.request({ method: 'POST', path: \`/v1/inbox/{id}/read\`,
      body: {} })`and removes the row optimistically; on error the row is
restored and an inline`role="alert"` is rendered.
- [x] FR-I6 — Clicking the row title navigates via Router using the item's
      embedded artifact-route hint (the server returns one of `chat`,
      `run`, `skill`, `workspace`); falls back to `{ kind: 'workspace',
workspaceId: item.id }` when the hint is missing.

### Todos destination

- [x] FR-T1 — `TodosDestination` is a functional React component, no props.
      Maintains a single `status` state (`'open' | 'done' | 'all'`) and
      re-fetches whenever it changes.
- [x] FR-T2 — Three tabs at top: `Open`, `Done`, `All`. Tabs use
      `role="tablist"` / `role="tab"` and `aria-selected`.
- [x] FR-T3 — Fetch shape:
      `transport.request<TodosPayload>({ method: 'GET', path: '/v1/todos',
query: { status } })`. `TodosPayload = { todos: Todo[] }`.
- [x] FR-T4 — Renders one of four states per filter: `loading` (4 skeleton
      rows), `error` (panel + Retry), `empty` (per-filter copy), `ready`
      (vertical list of todo rows).
- [x] FR-T5 — Each todo row shows: a toggle checkbox (`role="switch"` is
      avoided — a native `<input type="checkbox">` carries the right
      semantics), title, optional due-date label, and source tag (e.g.
      "from run · {runId-short}"). Toggling the checkbox calls
      `transport.request({ method: 'PATCH', path: \`/v1/todos/{id}\`,
      body: { completed } })`and updates the row optimistically; on error
the row reverts and an inline`role="alert"` is rendered for the row.
- [x] FR-T6 — Clicking the row title navigates via Router using the todo's
      embedded artifact-route hint when present; falls back to
      `{ kind: 'workspace', workspaceId: todo.id }`.

### All three

- [x] FR-X1 — Render inside the destination body. Width ~600–1000 px wide,
      full height. Fill `100%` of their containing column and use
      `box-sizing: border-box`. Scroll the body when content overflows.
- [x] FR-X2 — Dark palette consistent with the shell and sibling
      destinations: backgrounds `#0F1218` / `#131722`, borders `#22252E`,
      primary text `#E4E5E9`, secondary text `#7E8492`, accent `#7B9BFF`.
- [x] FR-X3 — Substrate ports only. No `window`, `document`, `fetch`,
      `localStorage`, `EventSource`, `XMLHttpRequest`, `WebSocket`.

## Non-functional requirements

- **Accessibility.** Tabs use `role="tablist"` / `role="tab"` with
  `aria-selected` and `aria-controls`; tab panels use `role="tabpanel"`;
  empty states announce themselves via `role="status"`; error panels via
  `role="alert"`; all interactive controls are real `<button>` elements
  with explicit `aria-label`s.
- **Performance.** One network request per mount + one per Retry / filter
  change. Optimistic updates for mark-as-read and todo toggle. No retry
  loops. Skeletons render synchronously to avoid layout shift.
- **Test coverage.** Each destination has four interaction-level tests —
  skeleton (loading) → populated (ready) → empty → error. Transport is
  mocked with a colocated `makeDeferredTransport` helper that returns a
  controllable `Promise`. Tests use `@testing-library/react` with role-first
  queries and never assert on class names.

## Interfaces consumed

- `Transport`, `TypedRequest` from `@0x-copilot/chat-transport`.
- `useTransport()` from
  `packages/chat-surface/src/providers/TransportProvider.tsx`.
- `useRouter<ArtifactRoute>()` from
  `packages/chat-surface/src/providers/RouterProvider.tsx`.
- `ArtifactRoute`, `Router` from
  `packages/chat-surface/src/routing/router.ts`.

## Interfaces produced

```ts
export function HomeDestination(): React.ReactElement;
export function InboxDestination(): React.ReactElement;
export function TodosDestination(): React.ReactElement;

export type HomeArtifactKind = "chat" | "run" | "skill";
export type InboxFilter = "all" | "mentions" | "approvals" | "errors";
export type TodoStatusFilter = "open" | "done" | "all";
```

Each destination is exported via its package-local `index.ts` barrel.
`packages/chat-surface/src/index.ts` is intentionally not edited from
this branch — that wiring is the orchestrator's job (per PRD §5 row 3A
coordination note).

## Open questions

1. **Project / library / adapter `ArtifactRoute` kinds.** Same gap as 3B:
   no `project`, `library`, or `notification` route variants exist yet, so
   home/inbox/todos route via the existing union (`chat`, `run`, `skill`,
   `workspace`). When new kinds land (likely Phase 4 or 5) the
   `navigate(...)` call-sites in these destinations should be updated.
2. **Backend endpoints.** `/v1/home`, `/v1/inbox`, `/v1/inbox/{id}/read`,
   `/v1/todos`, `/v1/todos/{id}` are assumed to exist on `backend-facade`
   per the PRD; no backend-side work in this branch.
3. **`InboxItem.route` hint shape.** Inbox items reference an artifact by
   embedding an `ArtifactRoute` directly inside the payload (so the
   navigation is server-driven). This shape is local to this destination
   and not re-exported; if other destinations need the same pattern, it
   will be promoted to `api-types` in a later phase.

The agent **proceeds to implementation** with the documented placeholders
(per D21 — spec-first-then-continue).

## Done criteria

- [x] All FRs met
- [x] `npm run typecheck --workspace @0x-copilot/chat-surface` passes
- [x] `npm test --workspace @0x-copilot/chat-surface` passes
- [x] `npm run lint --workspace @0x-copilot/chat-surface` passes
- [x] No imports outside scope; no edits outside the In-scope list
- [x] No bare browser primitives (uses Transport / Router ports only)
- [x] No new third-party dependency

## Notes for orchestrator review

- Each destination's TypeScript types are local (per the prompt's "no shared
  file across destinations in this phase" rule). The three folders are
  deliberately independent.
- Optimistic updates (inbox mark-as-read, todo toggle) revert on failure and
  surface an inline `role="alert"`. This is the smallest viable error UX —
  no toast system exists in chat-surface yet and adding one is out of scope.
- Tests use a `makeDeferredTransport` helper colocated in each test file
  (same pattern as 3B). The helper returns a Transport whose `request()`
  resolves on a `controller.resolve(...)` call, which makes the `loading`
  state observable across the four-state matrix.
- The orchestrator wires the three destinations into the public barrel by
  appending exactly these three lines to `packages/chat-surface/src/index.ts`:

  ```ts
  export { HomeDestination } from "./destinations/home";
  export { InboxDestination } from "./destinations/inbox";
  export { TodosDestination } from "./destinations/todos";
  ```
