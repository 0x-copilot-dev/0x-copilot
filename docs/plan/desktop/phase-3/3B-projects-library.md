# Phase 3.B: dest-projects-library

## Vision

Two leaf destinations in the Atlas shell — Projects (workspace projects grid)
and Library (saved agent artifacts: tier-2 adapters, agent-saved tool results,
knowledge cards). Both are read-mostly views that fetch through `Transport`
and route into the existing shell. They are deliberately substrate-agnostic:
no `window`, `document`, `fetch`, `localStorage`, or `EventSource` — only the
`Transport` port handed in by the shell.

DRY principles applied:

- **Single transport.** Both destinations call `transport.request(...)` via
  the existing `useTransport()` hook. No ad-hoc fetch wrappers, no per-page
  client. The shell injects the Transport via `TransportProvider`.
- **Single router.** "Open this project" / "open this library item" both go
  through `useRouter<ArtifactRoute>().navigate(...)`. Projects route to
  `{ kind: 'workspace', workspaceId }` (no project-specific scheme exists
  yet — flagged in §Open questions).
- **One state machine per destination.** A small `'loading' | 'error' |
'ready'` view-state, computed inline from the fetch result. No
  `useEffect`-derived state, no redux.
- **Inline styles only.** Consistent with `AppRail`, `ContextPanel`,
  `CommandPalette` — design-system tokens for fonts/colours are not yet
  wired into chat-surface inline-style consumers.
- **No comments by default; functional components only; no `any`.**

## Status

- Status: in-progress
- Agent slug: `dest-projects-library`
- Branch: `desktop/phase-3-projects-library`
- Worktree: `.claude/worktrees/agent-ae2aae6359ce2e978`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-3/3B-projects-library.md` — this file.
- `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx`
- `packages/chat-surface/src/destinations/projects/ProjectsDestination.test.tsx`
- `packages/chat-surface/src/destinations/projects/index.ts`
- `packages/chat-surface/src/destinations/library/LibraryDestination.tsx`
- `packages/chat-surface/src/destinations/library/LibraryDestination.test.tsx`
- `packages/chat-surface/src/destinations/library/index.ts`

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — listed in the PRD as orchestrator
  territory; the integrating commit at the end of Phase 3 wires the barrel.
- `packages/chat-surface/src/shell/**` — Agent 1-B / 2-A territory.
- Any other destination (`home`, `chats`, `inbox`, `todos`, `agents`,
  `tools`, `connectors`, `team`, `memory`).
- `Transport` / `Router` / `KeyValueStore` ports — frozen.

## Functional requirements

### Projects destination

- [x] FR-P1 — `ProjectsDestination` is a functional React component, no
      props. On mount it calls `useTransport().request<{ projects: Project[] }>(...)` with `method: 'GET'` and `path: '/v1/projects'`.
- [x] FR-P2 — Renders one of four states:
      `loading` (skeleton grid of 6 placeholder cards),
      `error` (centered error panel with a Retry button that re-issues the
      request),
      `empty` (centered "No projects yet" + the +New project action),
      `ready` (responsive grid of project cards + the +New project action).
- [x] FR-P3 — Each project card shows: name, last-activity timestamp,
      chat count, owner avatar (image when `ownerAvatarUrl` is present,
      otherwise a circle with the owner's initials).
- [x] FR-P4 — Clicking a project card calls
      `router.navigate({ kind: 'workspace', workspaceId: project.id })`.
- [x] FR-P5 — "+ New project" action calls
      `transport.request({ method: 'POST', path: '/v1/projects', body: { name } })`,
      prepends the returned project to the list on success, and surfaces
      a tiny inline-error toast on failure. The action is a controlled
      inline form that auto-focuses an input; Enter submits, Esc cancels.

### Library destination

- [x] FR-L1 — `LibraryDestination` is a functional React component, no
      props. Maintains a single `activeTab` state.
- [x] FR-L2 — Three tabs: `Adapters` (`kind: 'adapter'`),
      `Results` (`kind: 'result'`), `Knowledge` (`kind: 'knowledge'`).
      Each tab fetches via `transport.request<{ items: LibraryItem[] }>(...)` with `method: 'GET'`, `path: '/v1/library'`, and `query: { kind: tabSlug }`.
- [x] FR-L3 — Renders one of four states per tab: `loading` (4 skeleton
      rows), `error` (panel + Retry), `empty` (per-tab copy), `ready`
      (vertical list of item cards).
- [x] FR-L4 — Each item card shows: kind icon (one of three SVG glyphs),
      title, modified-date label, "Open" affordance (button, not a link —
      routing happens through Router).
- [x] FR-L5 — Clicking "Open" on an item routes via Router. With no
      library-specific ArtifactRoute yet, items currently route to
      `{ kind: 'workspace', workspaceId: item.id }` (placeholder — same
      gap that projects faces). Tracked under §Open questions.

### Both

- [x] FR-X1 — Render inside the destination body. Width ~600–1000 px wide,
      full height. Both fill `100%` of their containing column and use
      `box-sizing: border-box`.
- [x] FR-X2 — Dark palette consistent with the shell:
      backgrounds `#0F1218` / `#131722`, borders `#22252E`, primary text
      `#E4E5E9`, secondary text `#7E8492`, accent `#7B9BFF`.
- [x] FR-X3 — Substrate ports only. No `window`, `document`, `fetch`,
      `localStorage`, `EventSource`, `XMLHttpRequest`, `WebSocket`.

## Non-functional requirements

- **Accessibility.** All interactive controls are `<button>` elements
  with explicit `aria-label`s; tabs use `role="tab"` + `role="tablist"`
  with `aria-selected`; tab panels use `role="tabpanel"`; project cards
  expose `aria-label` matching their name; the empty state announces
  itself via `role="status"`.
- **Performance.** Both destinations issue exactly one network request per
  mount + one per Retry / tab change. No retry loops. Skeletons render
  synchronously to avoid layout shift.
- **Test coverage.** Each destination has four interaction-level tests
  (skeleton → populated → empty → error). Transport is mocked with a
  controllable `Promise` so loading state is observable.

## Interfaces consumed

- `Transport` via `useTransport()` from
  `packages/chat-surface/src/providers/TransportProvider.tsx`.
- `Router<ArtifactRoute>` via `useRouter<ArtifactRoute>()` from
  `packages/chat-surface/src/providers/RouterProvider.tsx`.
- `ArtifactRoute` from `packages/chat-surface/src/routing/router.ts`.

## Interfaces produced

```ts
export function ProjectsDestination(): React.ReactElement;
export function LibraryDestination(): React.ReactElement;
```

Both are exported via their package-local `index.ts` barrels.
`packages/chat-surface/src/index.ts` is intentionally not edited from
this branch — that wiring is the orchestrator's job (per the PRD §5 row
3B coordination note).

## Open questions

1. **Project-specific ArtifactRoute kind.** The current `ArtifactRoute`
   union has no `{ kind: 'project'; projectId }` variant. Per the prompt,
   projects currently navigate via `{ kind: 'workspace', workspaceId }`.
   When a `project` kind is added (likely Phase 4 or 5), update the
   navigation handlers in both destinations.
2. **Library `Adapter` open path.** Today there is no
   `{ kind: 'adapter'; scheme; version }` route either. Items also fall
   back to `workspace`. Same note as #1.
3. **Backend endpoints.** `/v1/projects` and `/v1/library` are assumed
   to exist on `backend-facade` per the PRD; no backend-side work in
   this branch.

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

- The two `index.ts` barrels exist only to keep each destination
  package-internal-importable; the top-level
  `packages/chat-surface/src/index.ts` wiring is deferred per the PRD
  coordination note.
- Both destinations resolve the same gap (no project / no adapter
  ArtifactRoute kind) the same way (workspace fallback) — kept identical
  so the future migration is mechanical.
- Tests use a `makeDeferredTransport` helper that returns a mock
  Transport whose `request()` resolves on demand; this is the standard
  pattern for asserting that a `loading` state is visible before the
  promise settles. Helper is colocated in each test file (no shared
  test-utils file in chat-surface yet).
