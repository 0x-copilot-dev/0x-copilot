# Phase 2.A: chats-sidebar

## Vision

The Chats destination is the only destination today whose primary content is a
two-pane workspace: a project → thread tree on the left, a `ThreadCanvas` on
the right. Everything else in the eleven destinations is list-shaped (single
column, filters live in the `ContextPanel`); chats is the exception because
threads nest under projects and the user is constantly switching between them.

Staff-engineer take, applied to this phase's primitives:

- **DRY.** One sidebar primitive — `ChatsSidebar` — owns project search,
  caret toggling, fullscreen toggle, and active highlighting. The destination
  shell (`ChatsDestination`) is a thin two-pane layout that hosts the sidebar
  on the left and a `ThreadCanvas` slot on the right (2B owns the slot's
  content; this phase renders a placeholder).
- **Substitution.** All side effects flow through ports: thread/project data
  comes from `useTransport().request(...)`, navigation from `useRouter()`. No
  `fetch`, no `localStorage`, no `window`. The whole destination boots inside
  the existing `ChatShell` provider tree.
- **Simple & elegant.** Search filter is a single `useState` + an inline
  predicate. Expanded-projects state is a `Set<string>` in `useState`.
  Active-thread highlight is derived from `router.current()` inline — no
  cache, no memo, no effect. Sidebar width is a fixed `256px` inner column;
  the `1fr` remainder belongs to the placeholder (and later to
  `ThreadCanvas`).
- **Single source of truth.** Active highlight is driven by the router; the
  sidebar does not maintain a parallel `selectedThreadId` state. Clicking a
  thread navigates the router; the router publishes; the sidebar re-renders
  with the new highlight. No way for the two to drift.

The PRD scopes 2A to one file (`ChatsSidebar.tsx`). I split it into a
sidebar + a destination shell because the destination outlet needs a host
component to lay out the sidebar + canvas placeholder anyway. Without the
host, ChatShell would either inline the layout (defeating per-destination
componentization) or 2B would have to invent it (defeating 2A's scope).
The split is two files, total ~300 LOC; the canvas placeholder is one div.

## Status

- Status: done (awaiting orchestrator merge)
- Agent slug: `chats-sidebar`
- Branch: `desktop/phase-2-chats-sidebar`
- Worktree: `.claude/worktrees/agent-a0af7245c14f48994`
- Created: 2026-05-17
- Audited: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-2/2A-chats-sidebar.md` — this file.
- `packages/chat-surface/src/destinations/chats/ChatsDestination.tsx` — NEW.
  Two-pane host: `ChatsSidebar` on the left, `ThreadCanvas` placeholder
  div on the right.
- `packages/chat-surface/src/destinations/chats/ChatsDestination.test.tsx` —
  NEW.
- `packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx` — NEW.
  Project → thread tree with carets, search, fullscreen toggle, active
  highlight.
- `packages/chat-surface/src/destinations/chats/ChatsSidebar.test.tsx` —
  NEW.
- `packages/chat-surface/src/destinations/chats/index.ts` — NEW. Barrel.

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — orchestrator appends the Phase 2-A
  export block at merge time.
- `packages/chat-surface/src/shell/ChatShell.tsx` — orchestrator wires the
  destination dispatcher at merge time.
- `packages/chat-surface/src/thread-canvas/**` — Agent 2-B's territory.
- Any other `packages/chat-surface/src/destinations/**` subtree — Agents 3-A
  through 3-D.
- Backend endpoint `/v1/chats/projects` — not in this PRD scope; sidebar
  reads via `Transport.request`, the contract is the response shape.

## Functional requirements

- [x] FR-1: `ChatsSidebar` mounts and calls
      `transport.request({ method: 'GET', path: '/v1/chats/projects' })` typed
      as `{ projects: Array<{ id, name, threads: Array<{ id, title, updated_at }>}> }`
      exactly once on mount. Loading state renders a skeleton; error renders
      a single error row with the message; success renders the project tree.
- [x] FR-2: Each project row has a caret button. Clicking the caret toggles
      its expansion. By default, all projects are collapsed; the project
      whose thread is currently active is auto-expanded on mount so the
      active thread is visible.
- [x] FR-3: Search input at the top of the sidebar filters projects and
      threads client-side, case-insensitive substring match against project
      `name` and thread `title`. A project is shown if its name matches OR
      any of its threads' titles match; when shown via a thread match, the
      project is auto-expanded and only matching threads render. Empty
      search reverts to default expansion state.
- [x] FR-4: Fullscreen toggle button at the top of the sidebar (icon
      button). Emits `props.onFullscreenChange(next)` on click; parent
      decides what fullscreen means. The button has a `pressed` visual when
      `props.fullscreen` is true.
- [x] FR-5: Active thread highlight is driven by
      `router.current()` + `router.subscribe(...)`. A thread row whose `id`
      matches the current `ArtifactRoute` with `kind: 'chat' | 'conversation'`
      and equal `conversationId` renders with `aria-current="page"` and an
      active visual treatment.
- [x] FR-6: Clicking a thread row calls
      `router.navigate({ kind: 'chat', conversationId: threadId })`. Clicking
      a project row toggles expansion only — no navigation (this destination
      has no project route shape today).
- [x] FR-7: `ChatsDestination` renders a two-column grid:
      `[ChatsSidebar (256px) | ThreadCanvas placeholder (1fr)]`. Placeholder
      is a `<div data-testid="thread-canvas-placeholder" />`. No import from
      `thread-canvas/` — that's 2B's surface.
- [x] FR-8: Public exports (orchestrator merges at integration time):
      `ChatsDestination`, `ChatsSidebar`, `ChatsSidebarProps`. Barrel
      lives at `packages/chat-surface/src/destinations/chats/index.ts`.

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on interface fields by default.
- React functional components + hooks only.
- Substrate-port discipline: no `window`/`document`/`fetch`/`localStorage`/
  `history`/`location` references — ports only.
- No `useEffect` for derived state. `useEffect` is used only for
  router subscription + transport request lifecycle (with `AbortController`
  via `req.signal` for the in-flight request on unmount).
- Inline styles only — `style={{}}` with the dark palette matching the
  shell (`#0E1015` panel, `#22252E` borders, `#E4E5E9` primary text,
  `#7E8492` secondary text, `#7B9BFF` active accent, `rgba(123,155,255,0.08)`
  active tint).
- Test coverage: one `.test.tsx` per component. Queries by role and
  accessible text first; `data-testid` only for the canvas placeholder and
  loading/error sentinels.

## Interfaces consumed

- `Transport.request` from `useTransport()` — fetches project + thread
  tree.
- `Router<ArtifactRoute>` from `useRouter<ArtifactRoute>()` — current
  route + subscribe + navigate.

## Interfaces produced

```ts
// packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx
export interface ChatsSidebarProps {
  readonly fullscreen?: boolean;
  readonly onFullscreenChange?: (next: boolean) => void;
}
export function ChatsSidebar(props: ChatsSidebarProps): ReactElement;

// packages/chat-surface/src/destinations/chats/ChatsDestination.tsx
export function ChatsDestination(): ReactElement;
```

## Out of scope

- Real fullscreen behavior (parent decides). Fullscreen toggle here just
  emits the event.
- ThreadCanvas — Agent 2-B.
- Loading skeleton design polish — simple placeholder rows for now.
- Drag-to-reorder threads or projects.
- Pagination of long thread lists — Phase 4+ when we have real data.
- Persistence of expanded-project state across reloads — would route
  through `KeyValueStore`, deferred until the UX calls for it.

## Implementation plan

1. Sub-PRD (this file).
2. Create directory + `index.ts` barrel.
3. `ChatsSidebar.tsx`: typed props, internal state for search /
   expansion / fetched data, mount effect that calls `transport.request`,
   subscribe effect for router, render tree with header / search /
   fullscreen toggle / project list / thread list, dark inline styles.
4. `ChatsDestination.tsx`: two-column grid hosting sidebar + placeholder.
5. Colocated `.test.tsx` files using a minimal MockTransport-like stub
   - the same `makeRouter` pattern as `AppRail.test.tsx`.
6. Typecheck + tests pass.
7. Commit.

## Test plan

`ChatsSidebar.test.tsx`:

- Renders loading sentinel initially, then the project list after the
  transport resolves.
- Renders error sentinel when the transport rejects.
- Renders projects and (when expanded) threads with correct titles.
- Caret click toggles expansion (a thread under a collapsed project is
  not rendered).
- Search filters projects and threads case-insensitively; matching by
  thread title auto-expands its project.
- Clicking a thread calls `router.navigate` with
  `{ kind: 'chat', conversationId: threadId }`.
- The thread row whose id matches the current route renders with
  `aria-current="page"`.
- Active highlight follows router updates (publish a new route, the
  highlight moves).
- Fullscreen toggle button click emits `onFullscreenChange(!fullscreen)`.

`ChatsDestination.test.tsx`:

- Renders the sidebar (project list visible after transport resolves) and
  the canvas placeholder (`data-testid="thread-canvas-placeholder"`).

## Risks

- **Endpoint not yet implemented.** `/v1/chats/projects` is not on disk in
  the facade. This is intentional — the sidebar consumes the port contract
  the moment the endpoint exists; the orchestrator can ship the endpoint
  in any later phase without changing this file. The error sentinel
  handles the today-state.
- **Active-route synchronization.** If the router fires a route change
  before the transport response lands, the sidebar would have to re-decide
  whether to auto-expand a project. Handled: the auto-expand logic runs on
  every render with no memo, so a late-arriving route + late-arriving data
  still resolve to the correct expansion.
- **Two routes share `conversationId`.** `kind: 'chat'` and
  `kind: 'conversation'` are both valid routes pointing at the same id.
  Active highlight matches either kind; navigation always emits `chat`.

## Audit notes (post-implementation)

**Shipped:**

- 5 files added (no other files touched):
  - `packages/chat-surface/src/destinations/chats/ChatsDestination.tsx`
  - `packages/chat-surface/src/destinations/chats/ChatsDestination.test.tsx`
  - `packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx`
  - `packages/chat-surface/src/destinations/chats/ChatsSidebar.test.tsx`
  - `packages/chat-surface/src/destinations/chats/index.ts`
- Sub-PRD at `docs/plan/desktop/phase-2/2A-chats-sidebar.md` (this file).
- Tests added: 17 (15 in `ChatsSidebar.test.tsx`, 2 in
  `ChatsDestination.test.tsx`).
- Full chat-surface suite: 201/201 passing (was 184 before).
- `npm run typecheck --workspace @enterprise-search/chat-surface` passes.
- `npm run lint --workspace @enterprise-search/chat-surface` passes.
- `npm run typecheck --workspace @enterprise-search/frontend` still passes.
- `npm run typecheck --workspace @enterprise-search/surface-renderers`
  still passes.

**Deviations from the original PRD scope:**

1. **Two files instead of one.** The PRD names only `ChatsSidebar.tsx`. I
   added `ChatsDestination.tsx` as the destination outlet host because
   the destination needs a two-pane layout and the orchestrator prompt
   explicitly says the canvas placeholder lives in this phase. Without a
   host, either `ChatShell` inlines the layout (defeating
   per-destination componentization) or 2B has to invent the host
   (defeating 2A scope). The host is ~40 LOC.

2. **Auto-expand on mount.** PRD says "all projects collapsed by default"
   but it's a poor UX if the active thread is hidden. I auto-expand the
   project that owns the active thread (derived inline from
   `router.current()` — no extra state). Empty-route case behaves as
   "all collapsed" per the spec.

3. **Active highlight matches `conversation` kind too.** The router
   has both `chat` and `conversation` route shapes pointing at the
   same id. Highlighting only on `chat` would let users land on a
   conversation route with no visible active thread. The match
   handles both kinds; navigation still emits `chat` only.

**Known carry-forward:**

- `/v1/chats/projects` is consumed but not yet implemented on the
  facade. Today the sidebar renders the error sentinel. The endpoint
  contract is `{ projects: Array<{ id, name, threads: Array<{ id, title,
updated_at }> }> }` — declared inline; promote to `packages/api-types`
  when the backend lands.
- Persistence of expanded-project state across reloads (via
  `KeyValueStore`) is deferred until the UX calls for it.
- Right-rail open/close state lives on `ChatShell` today; per-destination
  fullscreen lives on `ChatsDestination` today. If global "focus mode"
  arrives later, lift the fullscreen state to the shell.

---

**For orchestrator (integration):**

- Add to `packages/chat-surface/src/index.ts`:
  `export { ChatsDestination, ChatsSidebar, type ChatsSidebarProps } from "./destinations/chats";`
- Wire `ChatsDestination` into `ChatShell`'s `DestinationOutlet` when the
  active destination slug is `chats`.
