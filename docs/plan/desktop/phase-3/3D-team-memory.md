# Phase 3.D: dest-team-memory

## Vision

Two of Atlas's eleven top-level destinations are administrative / introspective
leaves whose visual shape is "scrollable list inside the destination body":

- **Team** — the workspace's directory of humans. Avatars + names + roles + an
  Invite action. This is the right home for the existing `Workspace` artifact
  route — clicking a row navigates there.
- **Memory** — the per-tenant agent memory inspector. Three buckets (User /
  Project / Reference) accessed via tabs, each populated by a
  `GET /v1/memory?type=...` call. Pin/unpin and delete affordances.
  Search across all memories.

Staff-engineer take, applied to this phase's primitives:

- **DRY.** Both destinations boil down to the same three primitives —
  `<DestinationHeader/>`, `<DestinationToolbar/>`, `<DestinationBody/>` —
  but those primitives are not justified yet (they would be promoted from
  duplicated code across the four 3-x sub-PRDs, not invented here). Until
  the 3-x branches all merge and we can see the duplication, both
  destinations open-code their own header / toolbar inline. Memory's tab
  bar is the same idea repeated three times — `<button data-active=…>` —
  no abstraction.
- **Substitution.** Every side effect flows through ports.
  `useTransport().request(...)` for the member list and the memory query,
  `useRouter()` for the click-through navigation on Team. No `fetch`, no
  `localStorage`, no `window`. The destination boots inside the existing
  `ChatShell` provider tree.
- **Simple & elegant.** State for each destination fits in a small set of
  hooks: a loading / error / data discriminated union, a tab-active piece of
  state on Memory, a search-query string. No memoization beyond the trivial
  `useMemo` for the filtered list, because the lists are short and the
  cost-of-being-clever exceeds the cost of recomputing.
- **Single source of truth.** Both destinations are read-only with respect
  to the in-memory data they fetch — the action buttons (Invite, Pin,
  Delete) are presentational stubs in this PRD that emit a callback. Wiring
  the actions to mutating transport calls is a follow-up; doing it here
  forces decisions on the writes' contracts before the read side has
  landed.

## Status

- Status: in-progress
- Agent slug: `dest-team-memory`
- Branch: `desktop/phase-3-team-memory`
- Worktree: `.claude/worktrees/agent-aabaf060b914f971d`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-3/3D-team-memory.md` — this file.
- `packages/chat-surface/src/destinations/team/TeamDestination.tsx` — NEW.
  Workspace member directory.
- `packages/chat-surface/src/destinations/team/TeamDestination.test.tsx` —
  NEW.
- `packages/chat-surface/src/destinations/team/index.ts` — NEW. Barrel.
- `packages/chat-surface/src/destinations/memory/MemoryDestination.tsx` —
  NEW. Per-tenant agent memory inspector.
- `packages/chat-surface/src/destinations/memory/MemoryDestination.test.tsx`
  — NEW.
- `packages/chat-surface/src/destinations/memory/index.ts` — NEW. Barrel.

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — orchestrator appends the Phase 3-D
  export block at merge time. Exports listed below under
  "For orchestrator (integration)".
- `packages/chat-surface/src/shell/ChatShell.tsx` — orchestrator wires the
  destination dispatcher at merge time.
- Other `packages/chat-surface/src/destinations/*` subtrees — Agents 3-A,
  3-B, 3-C.
- Backend endpoints `/v1/workspace/members` and `/v1/memory` — not in this
  PRD scope; destinations read via `Transport.request`, the contract is
  the response shape.

## Functional requirements — Team

- [x] FR-T1: `TeamDestination` mounts and calls `transport.request` for
      `GET /v1/workspace/members` exactly once on mount. Loading renders
      a skeleton; error renders an inline error row with the message;
      empty renders an empty-state card; success renders a table.
- [x] FR-T2: Each member row shows an avatar (initial-circle via
      design-system `AppIcon`), name, email, role badge (Owner / Admin /
      Member / Guest with tone variants), and a relative last-active label.
- [x] FR-T3: Header has an "Invite" button (design-system primary
      button). Click emits `props.onInvite()` if provided; otherwise
      no-op. The button is always enabled.
- [x] FR-T4: Clicking a member row calls
      `router.navigate({kind: 'workspace', workspaceId})` where
      `workspaceId` is taken from `member.workspaceId` (server-provided).
      Rows are keyboard-focusable (`<button>`-like semantics via
      `role="button"` + `tabIndex={0}` + `onKeyDown` for Enter/Space).
- [x] FR-T5: Public exports: `TeamDestination`, `TeamDestinationProps`,
      `Member`, `MemberRole`.

## Functional requirements — Memory

- [x] FR-M1: `MemoryDestination` renders a tab bar with three tabs: "User
      memories" (type=`user`), "Project memories" (type=`project`),
      "Reference memories" (type=`reference`). Default tab is `user`.
- [x] FR-M2: Switching tabs triggers a new `transport.request` for
      `GET /v1/memory?type=<tab>`. The previous in-flight request is
      aborted via the `signal` on the typed request. Each tab maintains
      an independent loading / error / data state — switching back to a
      previously-loaded tab does not refetch (cache by type).
- [x] FR-M3: Each tab's body renders a search input (filters across the
      tab's loaded memories), and a list of memory cards. Each card shows:
      title, description (truncated to ~2 lines), last-updated relative
      label, a type-tag badge, a pin/unpin icon button, and a delete icon
      button. Pinned memories sort first.
- [x] FR-M4: Search is case-insensitive substring match against
      `title` + `description`. Empty search shows all.
- [x] FR-M5: Pin/unpin and delete buttons emit `props.onTogglePin(memory)`
      and `props.onDelete(memory)` respectively. Local pin state updates
      optimistically for visual feedback; if the parent does not provide
      the callback, the buttons are present but no-op.
- [x] FR-M6: Loading / error / empty / populated states per tab. Empty
      while searching shows a "No memories match …" card.
- [x] FR-M7: Public exports: `MemoryDestination`, `MemoryDestinationProps`,
      `Memory`, `MemoryType`.

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on interface fields by default.
- React functional components + hooks only. No class components.
- **No comments by default** — code reads itself; comments only where the
  why-not-what is non-obvious and adding lower the friction of reading.
  Per the PRD's house style this aligns with how other Phase 1/2 files are
  written.
- Substrate-port discipline: no `window`/`document`/`fetch`/`localStorage`/
  `EventSource` references — ports only.
- Inline styles only (matches Phase 1/2 destinations) — dark palette
  matching `ChatShell` (`#0E1015` panel background, `#22252E` borders,
  `#E4E5E9` primary text, `#7E8492` secondary text, `#7B9BFF` accent).
- Test coverage: one `.test.tsx` per destination, four states each
  (skeleton → populated → empty → error). Queries by role / accessible
  text first; `data-testid` reserved for state sentinels only.

## Interfaces consumed

- `Transport.request` from `useTransport()` — fetches member list / memory
  list.
- `Router<ArtifactRoute>` from `useRouter<ArtifactRoute>()` — Team uses
  navigate; Memory does not (memory cards have no current artifact route).

## Interfaces produced

```ts
// packages/chat-surface/src/destinations/team/TeamDestination.tsx
export type MemberRole = "owner" | "admin" | "member" | "guest";
export interface Member {
  readonly id: string;
  readonly name: string;
  readonly email: string;
  readonly role: MemberRole;
  readonly lastActiveIso: string;
  readonly workspaceId: string;
  readonly avatarColor?: string;
}
export interface TeamDestinationProps {
  readonly onInvite?: () => void;
}
export function TeamDestination(props?: TeamDestinationProps): ReactElement;

// packages/chat-surface/src/destinations/memory/MemoryDestination.tsx
export type MemoryType = "user" | "project" | "reference";
export interface Memory {
  readonly id: string;
  readonly type: MemoryType;
  readonly title: string;
  readonly description: string;
  readonly lastUpdatedIso: string;
  readonly pinned: boolean;
}
export interface MemoryDestinationProps {
  readonly onTogglePin?: (memory: Memory) => void;
  readonly onDelete?: (memory: Memory) => void;
}
export function MemoryDestination(props?: MemoryDestinationProps): ReactElement;
```

## Out of scope

- Mutating transport calls for Invite / Pin / Delete — emitted as
  callbacks; wiring is a follow-up.
- Avatar image URLs — initial-circle only, brand glyph fallback via
  design-system `AppIcon`.
- Pagination — both lists are assumed short enough for one fetch; if /
  when paginated, the response shape extends without breaking the
  destinations.
- Sorting / filtering controls beyond pin-first and search — list order
  is server-provided otherwise.
- Memory create flow / "add memory" — separate destination concern; lives
  outside this PRD.

## Implementation plan

1. Sub-PRD (this file).
2. Create directories + `index.ts` barrels.
3. `TeamDestination.tsx`: typed props, internal data state union,
   mount effect that calls `transport.request`, render header (title +
   Invite), table of members. Row click → `router.navigate`.
4. `TeamDestination.test.tsx`: four state cases against a stub Transport.
5. `MemoryDestination.tsx`: tab state, per-tab data cache, effect that
   fetches when active tab is uncached, search input, sorted/filtered
   list, card grid.
6. `MemoryDestination.test.tsx`: four state cases against a stub
   Transport; tab switching triggers a second fetch.
7. Typecheck + tests pass.
8. Commit on `desktop/phase-3-team-memory`.

## Test plan

`TeamDestination.test.tsx`:

- Renders loading skeleton initially.
- Renders empty-state card when transport returns `{members: []}`.
- Renders the table with avatar / name / email / role / last-active for
  each member when transport resolves.
- Clicking a row calls `router.navigate({kind:'workspace', workspaceId})`.
- Clicking the Invite header button calls `props.onInvite`.
- Renders error sentinel when the transport rejects.

`MemoryDestination.test.tsx`:

- Renders loading skeleton for the default `user` tab initially.
- Renders empty-state card when transport returns `{memories: []}`.
- Renders memory cards with title / description / type tag / relative
  last-updated label when transport resolves.
- Pinned memories sort before unpinned.
- Search input filters by title / description case-insensitively;
  no-match shows the "no memories match" card.
- Switching tabs triggers a second `transport.request` with the new
  `type` query.
- Pin button click calls `props.onTogglePin`; delete button click calls
  `props.onDelete`.
- Renders error sentinel when the transport rejects.

## Risks

- **Endpoints not yet implemented.** `/v1/workspace/members` and
  `/v1/memory` are not on disk in the facade today. Same handling as
  Phase 2-A — the destination consumes the port contract the moment the
  endpoint exists; until then the error sentinel renders the today-state
  and the integration tests cover skeleton / empty / populated using a
  stub Transport.
- **Member `workspaceId` plurality.** A single workspace owns many
  members, so every member row shares the same `workspaceId`. Click-
  through navigates to the same `Workspace` artifact regardless of which
  row is clicked — intentional; the artifact contains the directory and
  the router has nowhere narrower to point. If a future per-member
  artifact appears, both the route union and `Member` extend.
- **Memory cache invalidation.** Per-tab cache means a delete or pin
  emitted upward will not auto-refresh until the parent triggers a
  remount. Acceptable for the PRD's read-side scope; the follow-up
  that wires writes will own cache invalidation.

## Audit notes (post-implementation)

To be filled after implementation.

---

**For orchestrator (integration):**

Append to `packages/chat-surface/src/index.ts`:

```ts
// === Phase 3-D dest-team-memory ===
export {
  TeamDestination,
  type Member,
  type MemberRole,
  type TeamDestinationProps,
} from "./destinations/team";
export {
  MemoryDestination,
  type Memory,
  type MemoryDestinationProps,
  type MemoryType,
} from "./destinations/memory";
// === end Phase 3-D ===
```

Wire `TeamDestination` into `ChatShell`'s `DestinationOutlet` when the
active destination slug is `team`; wire `MemoryDestination` when it is
`memory`.
