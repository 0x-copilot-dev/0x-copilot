# Phase 1.B: chat-shell-layout

## Vision

The Atlas desktop is **not a chat app and not an IDE** — it is "git for an
agent's work across real SaaS surfaces" (`project_atlas_product_model`).
The shell is the load-bearing chrome that makes that mental model legible:
a left **AppRail** for destinations (11 flat top-level destinations), a
**ContextPanel** that hangs per-destination filter rows, a **Topbar** with
breadcrumb + mode toggle + window chrome, and a toggleable **right rail**
where chat sits (chat is the right rail, not the center).

Staff-engineer take, applied to this phase's primitives:

- **DRY.** One grid host (`ChatShell`) composes the four shell regions. The
  destinations are a hard-coded list shared by `AppRail` and (in later
  phases) the routing table; for now, the list lives in one module and is
  imported.
- **Substitution.** The shell consumes `useRouter()` from the existing
  `RouterProvider`. It does not import `HashRouter`, does not import
  `Router` impls, does not know whether the substrate is web or desktop.
  `ChatShell` keeps its existing four-port provider wrap; the new shell
  regions render inside it.
- **Simple & elegant.** No state management library. Right-rail open/close
  is a single `useState`. Active-destination state derives from
  `router.current()` + a `subscribe` effect — no extra layer. The grid is
  inline CSS (`display: grid`) so the shell is one component, not three.
- **Single source of truth.** The destination list (`SHELL_DESTINATIONS`)
  is defined exactly once and re-used by `AppRail`. The `DestinationOutlet`
  stub switch lives inside `ChatShell` — a real route table arrives in
  1D, at which point `DestinationOutlet` will route through it; for this
  phase, the switch is intentionally minimal so the shell renders before
  the route table is ready.

The PRD lists `Topbar.tsx` under the shell subtree (§3.2). This phase
implements it as a chrome placeholder; mode toggle + breadcrumb + more
menu + fullscreen toggle UX-detail are Phase 3 (per
`project_atlas_product_model`'s mode posture description).

## Status

- Status: in-progress
- Agent slug: `chat-shell-layout`
- Branch: `desktop/phase-1-chat-shell-layout`
- Worktree: `.claude/worktrees/agent-a688f15171739eac0`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-1/1B-chat-shell-layout.md` — this file.
- `packages/chat-surface/src/shell/AppRail.tsx` — NEW. 52 px-wide icon rail
  with 11 hard-coded destinations.
- `packages/chat-surface/src/shell/AppRail.test.tsx` — NEW.
- `packages/chat-surface/src/shell/ContextPanel.tsx` — NEW. 224 px-wide
  per-destination panel; placeholder filter rows.
- `packages/chat-surface/src/shell/ContextPanel.test.tsx` — NEW.
- `packages/chat-surface/src/shell/Topbar.tsx` — NEW. 44 px-high top bar
  with breadcrumb + placeholder mode toggle.
- `packages/chat-surface/src/shell/Topbar.test.tsx` — NEW.
- `packages/chat-surface/src/shell/RightRail.tsx` — NEW. 380 px right rail
  with toggle.
- `packages/chat-surface/src/shell/RightRail.test.tsx` — NEW.
- `packages/chat-surface/src/shell/ChatShell.tsx` — REWRITE. Adds CSS grid
  hosting AppRail / ContextPanel / DestinationOutlet / RightRail; keeps
  the four-port provider wrap; preserves the existing `children` slot.
- `packages/chat-surface/src/shell/ChatShell.test.tsx` — NEW.
- `packages/chat-surface/src/shell/destinations.ts` — NEW. The
  hard-coded `SHELL_DESTINATIONS` list (single source of truth used by
  `AppRail`, `ContextPanel`, the `DestinationOutlet`).
- `packages/chat-surface/src/shell/index.ts` — NEW. Barrel for the four
  new components.
- `packages/chat-surface/src/index.ts` — APPEND ONLY. Add a delimited
  Phase 1-B block re-exporting the four new shell components. Existing
  `ChatShell` export stays.

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/routing/**` — Agent 1-D's territory
  (`HashRouter`, route-table). I rely on the existing on-disk
  `ArtifactRoute` discriminated union but do not extend it.
- `packages/chat-surface/src/palette/**` — Agent 1-D's territory.
- `packages/chat-transport/**` — Agent 1-C's territory.
- `apps/desktop/**` — Agent 1-A's territory.
- `packages/chat-surface/src/{ports,surfaces,thread-canvas,messages,citations,presence,providers,storage,routing,icons,test}/**` —
  not this agent's concern.
- Per-destination content beyond "render the destination name" —
  Phase 3.

## Functional requirements

- [ ] FR-1: `AppRail` renders 11 destination buttons (`home`, `chats`,
      `agents`, `library`, `inbox`, `tools`, `projects`, `todos`,
      `connectors`, `team`, `memory`). Each button is
      `<button type="button">` (keyboard reachable), has an `aria-label`
      of the destination's human-readable name, has an inline SVG glyph,
      exposes `data-destination={slug}` for tests, and exposes
      `aria-current="page"` when active.
- [ ] FR-2: Clicking an `AppRail` button calls `router.navigate(...)` for
      the destinations whose `ArtifactRoute` shape is already on disk
      (`chats` → `{ kind: 'chat', conversationId: '' }`); for the other
      ten destinations, the click is a navigate-noop in this phase (a
      route table mapping `Destination -> ArtifactRoute` arrives in 1D).
      The behavior is uniform — all 11 buttons are interactive; ten
      simply navigate to nothing today, by design and per orchestrator
      direction.
- [ ] FR-3: `AppRail` derives its `active` highlight from
      `router.current()` and the same router's `subscribe(...)` (cleanup
      on unmount). The active highlight is data-state and a visual
      treatment, not just a className; tests assert via
      `aria-current="page"`.
- [ ] FR-4: `ContextPanel` renders a flat `<ul>` of three placeholder
      filter rows for the active destination. Header is the
      destination's human-readable name. Phase 3 replaces the body with
      per-destination filter lists.
- [ ] FR-5: `Topbar` renders a breadcrumb derived from the active route
      (e.g. `chats / —` when the chat route is empty,
      `chats / {conversationId}` when set; for other destinations the
      breadcrumb shows just the destination name). A placeholder
      mode-toggle `<button>` renders next to the breadcrumb (no behavior
      yet); the "more menu" and "fullscreen toggle" are Phase 3.
- [ ] FR-6: `RightRail` renders a header "Atlas conversation" and a
      placeholder list. It exposes a single toggle button on its left
      edge; its open/close state is local to `ChatShell` (a single
      `useState`). When closed, the grid collapses the right column to
      `0` and the toggle button reappears as a chevron-style "expand"
      affordance.
- [ ] FR-7: `ChatShell` renders the grid `52px 224px 1fr 380px` (or
      `52px 224px 1fr 0` when the right rail is collapsed). Existing
      `children` prop is preserved — host apps still pass children and
      they render in the center column (above the `DestinationOutlet`
      stub, which only shows when `children` is absent). This keeps the
      existing `apps/frontend` integration green and gives 1D's route
      table a place to plug in later.
- [ ] FR-8: `DestinationOutlet` (internal to `ChatShell`) renders the
      destination name as a stub, e.g.
      `<div>chats: {conversationId ?? '—'}</div>` for `chats`,
      `<div>home</div>` for `home`, etc.
- [ ] FR-9: Public exports: append a delimited Phase 1-B block in
      `packages/chat-surface/src/index.ts` re-exporting `AppRail`,
      `ContextPanel`, `Topbar`, `RightRail` from `./shell`. Existing
      `ChatShell` export stays unchanged.

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on interface fields by
  default.
- React functional components + hooks only.
- Substrate-port discipline: no `window`/`document`/`fetch`/
  `localStorage`/`history`/`location` references. Confirmed by the
  existing ESLint rule (eslint.config.js bans these).
- No `useEffect` for derived state — `router.current()` is read inline.
  `subscribe` is the only `useEffect` needed (cleanup on unmount).
- Inline styles only — `style={{}}` with the dark palette. No new CSS
  files; matches the spike-shape acceptance in the orchestrator prompt.
- Test coverage: one `.test.tsx` per component. Tests assert by role
  and accessible text; no class-based queries; no snapshot tests.

## Interfaces consumed

- `Router<ArtifactRoute>` from `../routing/router` (re-exported via
  `../ports`). The shell components use `useRouter<ArtifactRoute>()`
  from `../providers/RouterProvider`. They do not import `HashRouter`
  or any other concrete impl.
- `ArtifactRoute` from `../routing/router`. Used to type the navigation
  payload and the breadcrumb derivation.
- Existing `ChatShell` provider wrap (`TransportProvider`,
  `RouterProvider`, `KeyValueStoreProvider`, `PresenceSignalProvider`)
  — unchanged in this phase; the new grid renders inside the existing
  provider tree.

## Interfaces produced

```ts
// packages/chat-surface/src/shell/destinations.ts (NEW)

export type ShellDestinationSlug =
  | "home"
  | "chats"
  | "agents"
  | "library"
  | "inbox"
  | "tools"
  | "projects"
  | "todos"
  | "connectors"
  | "team"
  | "memory";

export interface ShellDestination {
  readonly slug: ShellDestinationSlug;
  readonly label: string;
}

export const SHELL_DESTINATIONS: readonly ShellDestination[] = [
  /* 11 entries — full order per project_atlas_product_model */
];
```

```ts
// packages/chat-surface/src/shell/AppRail.tsx (NEW)
export function AppRail(): ReactElement;

// packages/chat-surface/src/shell/ContextPanel.tsx (NEW)
export function ContextPanel(): ReactElement;

// packages/chat-surface/src/shell/Topbar.tsx (NEW)
export function Topbar(): ReactElement;

// packages/chat-surface/src/shell/RightRail.tsx (NEW)
export function RightRail(props: {
  readonly open: boolean;
  readonly onToggle: () => void;
}): ReactElement;

// packages/chat-surface/src/shell/ChatShell.tsx (REWRITE)
//   signature preserved: <ChatShell transport={...} router={...} keyValueStore={...}
//                                    presenceSignal={...}>{children?}</ChatShell>
```

## Open questions

1. **`ArtifactRoute` coverage gap.** The on-disk `ArtifactRoute`
   discriminated union covers nine route kinds (`chat`/`conversation`/
   `run`/`subagent`/`tool-result`/`mcp`/`mcp-tool`/`skill`/`workspace`),
   none of which corresponds 1:1 to the 11 top-level destinations
   listed in `project_atlas_product_model` (`home`, `chats`, `agents`,
   `library`, `inbox`, `tools`, `projects`, `todos`, `connectors`,
   `team`, `memory`).

   Of the 11 destinations, only `chats` has a direct `ArtifactRoute`
   shape today (`kind: 'chat'`). The others either don't have one
   (`home`, `inbox`, `todos`, `projects`, `library`, `agents`,
   `connectors`, `team`, `memory`) or have a related-but-not-equivalent
   shape (`tools` → no shape; `mcp` and `skill` exist but are artifact
   shapes, not destination shapes).

   Per the orchestrator prompt: "for the spike-shipped destinations
   (chat / run / mcp / skill / workspace), wire navigation directly;
   for the rest (home / inbox / todos / projects / library / agents /
   tools / connectors / team / memory), use a stub navigate-noop until
   1D extends the route table."

   I follow that direction. **The cleanest fix is a destination-route
   shape (e.g. `{ kind: 'destination', slug: ShellDestinationSlug }`)
   added to the `ArtifactRoute` union in 1D — flagging for orchestrator
   review.** Without it, the shell cannot represent "I'm on `home` /
   `inbox` / `todos` / …" in the router at all; today it derives
   "active destination" from a default-to-`home` fallback when the
   current route is none of the artifact kinds.

2. **`Router<TRoute>` is generic, no `back()`.** The on-disk router
   has `current()` / `navigate()` / `subscribe()` only. `Topbar` only
   needs `current()` + `subscribe()` for the breadcrumb; no back
   button in this phase, so no exposure. If 1D's palette needs `back`,
   the producer should grow it (flagged in 0B's open question #4
   already).

3. **`SHELL_DESTINATIONS` list — why hard-coded here, not in
   `packages/api-types`?** Because this is product chrome, not a wire
   contract. The list is consumed only by chat-surface today (and 1D's
   route table, which lives in chat-surface too). Moving it to
   `api-types` would imply backend awareness; backend does not own the
   shell layout. If the list ever becomes server-driven (e.g. tenant
   admin re-orders destinations), the right move is to keep the
   constant as a default and have a hook surface a possible override —
   not to relocate the constant.

4. **`DestinationOutlet` stub inside `ChatShell`.** I keep it inside
   `ChatShell` rather than splitting into a separate file because 1D
   replaces this exact switch with the real route table. Splitting
   into a separate file now would create a churned file for 1D to
   delete. The stub is ~30 LOC and only used in one place.

5. **Right rail header copy.** "Atlas conversation" per
   `project_atlas_product_model` ("right is 'Atlas conversation'"). In
   Focus mode this becomes "Activity / Approvals" tabs — Phase 2-D
   builds that.

## Done criteria

- [ ] All FRs met
- [ ] `npm run typecheck --workspace @enterprise-search/chat-surface`
      passes
- [ ] `npm test --workspace @enterprise-search/chat-surface` passes
- [ ] `npm run lint --workspace @enterprise-search/chat-surface` passes
- [ ] No imports outside scope
- [ ] No bare browser primitives (`window` / `document` / `fetch` /
      `localStorage` / `history` / `location`) anywhere in this scope —
      enforced by the existing ESLint rule
- [ ] No new third-party dependency
- [ ] `packages/chat-surface/src/index.ts` only gains the delimited
      Phase 1-B block; all pre-existing exports are untouched
- [ ] `npm run typecheck --workspace @enterprise-search/surface-renderers`
      still passes (deprecated `SurfaceRendererProps` continues to live)

## Notes for orchestrator review

- The `ArtifactRoute` coverage gap (Open Q1) is the most important
  flag from this phase. The shell can render and navigate today, but
  cannot route eight of the eleven destinations until 1D extends the
  union. The right shape is a new variant; I have not added it in
  this phase because the orchestrator scoped routing to 1D.
- The breadcrumb implementation in `Topbar` is intentionally
  minimal — a real breadcrumb derives from a route → label map that
  is part of the route table 1D builds. For this phase, breadcrumb
  derives from `ArtifactRoute.kind` directly.
- The dark palette is rendered inline (`backgroundColor: '#0E1015'`
  for the app background, `#16181F` for elevated surfaces, `#22252E`
  for borders, `#E4E5E9` for text, `#3D4250` for inactive icons,
  `#7B9BFF` for active highlight). These match the design's near-black
  / soft-elevated treatment from the screenshots; they are not tokens
  in the design system yet. If `packages/design-system` grows shell
  tokens later, the constants in this scope migrate to consume them
  — single-source discipline. Flagging as a follow-up.
- `ChatShell`'s `children` prop is preserved so the existing
  `apps/frontend` integration (which mounts its own React tree inside
  `ChatShell`) keeps working. When `children` is non-empty, the
  internal `DestinationOutlet` is suppressed. This is the smallest
  bridge between "this phase ships shell chrome" and "1D wires up
  the destination route table"; it dies the moment 1D's route table
  lands and the host stops passing `children`.
