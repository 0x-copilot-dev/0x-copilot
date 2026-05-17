# Phase 3.C: dest-agents-tools-connectors

## Vision

Three leaf destination pages that surface the "git for an agent's work
across real SaaS surfaces" model: **Agents** (run history), **Tools**
(user skill catalog), **Connectors** (MCP servers). Each is a flat
top-level destination — clicked from the AppRail, listed in the
`SHELL_DESTINATIONS` constant from Phase 1-B — and renders inside the
shell's main body slot.

Staff-engineer take on the primitives:

- **DRY.** Loading skeleton / error / empty / populated is the same
  state-machine for all three destinations. Each destination is a
  thin shell over a typed `transport.request<...>` call; differences
  live in card shape and filter list, not in fetch / status plumbing.
- **Substitution.** Destinations consume `useTransport()` and
  `useRouter()` from the existing providers. They never touch
  `window` / `document` / `fetch` / `localStorage` / `EventSource`
  directly — the chat-surface ESLint rule enforces this. The
  components render identically in the desktop substrate (where
  Transport is an IPC bridge) as in the web (where Transport is a
  fetch wrapper).
- **Simple & elegant.** Single `useEffect` per destination drives the
  fetch lifecycle (load → resolve → render). Filter state is local
  `useState`. Sortable table is a `useMemo` over the loaded rows
  with the active sort key — no external state library, no virtual
  list machinery in Phase 3 (the lists are short by design).
- **Single source of truth.** Row sort + filter happens in one place
  per destination (a top-of-file `applyFilters` / `applySort` pair).
  The destination's view-model is a single derived list passed to
  the renderer.

The PRD scope for this phase is leaf pages only; clicking a row
calls `router.navigate(...)` with the `ArtifactRoute` shape that
already exists for that artifact kind (`{kind:'run',runId}`,
`{kind:'skill',skillId}`, `{kind:'mcp',serverId}`). What renders on
that artifact route is a later phase's concern — these destinations
do not own the artifact view itself.

## Status

- Status: in-progress
- Agent slug: `dest-agents-tools-connectors`
- Branch: `desktop/phase-3-agents-tools-connectors`
- Worktree: `.claude/worktrees/agent-ad89ef19021a84197`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-3/3C-agents-tools-connectors.md` — this
  file.
- `packages/chat-surface/src/destinations/agents/AgentsDestination.tsx`
  — NEW. Sortable run table + status / agent-name filters.
- `packages/chat-surface/src/destinations/agents/AgentsDestination.test.tsx`
  — NEW.
- `packages/chat-surface/src/destinations/agents/index.ts` — NEW.
- `packages/chat-surface/src/destinations/tools/ToolsDestination.tsx`
  — NEW. Skill catalog grid + Install / Manage affordances.
- `packages/chat-surface/src/destinations/tools/ToolsDestination.test.tsx`
  — NEW.
- `packages/chat-surface/src/destinations/tools/index.ts` — NEW.
- `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.tsx`
  — NEW. MCP server list + OAuth-status badges + Connect /
  Reauthorize / Disconnect affordances.
- `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.test.tsx`
  — NEW.
- `packages/chat-surface/src/destinations/connectors/index.ts` — NEW.

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — exports for these three
  destinations are listed in the "Interfaces produced" block below;
  the orchestrator wires them in when this phase merges.
- `packages/chat-surface/src/shell/**` — Phase 1-B's territory; the
  shell renders this destination's component in its main body slot
  via a host-supplied `children` prop today, and via the route table
  in a later phase.
- `packages/chat-surface/src/destinations/{home,inbox,todos,projects,library,team,memory,chats}/**`
  — other Phase 3 agents.
- Per-destination filter rows in `ContextPanel` — the panel renders
  generic placeholder filter rows in Phase 1-B; refining them per
  destination is Phase 3 ContextPanel work (not in this agent's
  scope).
- Real `agent_name` / `model` / `tokens` / `latency` columns from the
  backend — the on-disk `RunStatus` shape from `api-types` does not
  carry these; this destination's view-model defines a local `Run`
  shape that mirrors what the desktop will eventually receive on the
  `/v1/agent/runs` list endpoint. When the server-side payload grows
  these fields, the local shape is replaced with the canonical one
  from `api-types` (see Open question 2).

## Functional requirements

- FR-1: `AgentsDestination` renders a sortable table of agent runs
  (columns: timestamp, agent name, status, model, tokens, latency)
  with filters for status (`running` / `completed` / `failed`) and
  agent name (free-text substring match). The fetch is
  `transport.request<{runs: Run[]}>({method:'GET',path:'/v1/agent/runs',
query:{status?, agent_name?}})`. Initial sort is timestamp
  descending; clicking a column header toggles its sort direction
  and makes it the active sort key.
- FR-2: Clicking an `AgentsDestination` row calls
  `router.navigate({kind:'run', runId})`.
- FR-3: `ToolsDestination` renders a responsive grid of skill cards
  (name / description / last-used relative timestamp / Install /
  Manage affordances). The fetch is
  `transport.request<{skills: Skill[]}>({method:'GET',path:'/v1/skills'})`.
  Card affordance is `Install` when the skill is not yet enabled and
  `Manage` when it is — derived from the `Skill.enabled` field in
  `api-types`.
- FR-4: Clicking a `ToolsDestination` skill card calls
  `router.navigate({kind:'skill', skillId})`. Install / Manage
  buttons stop propagation so they do not also navigate (they are
  no-ops in this phase; wiring is a later phase's concern).
- FR-5: `ConnectorsDestination` renders an MCP server list as cards
  (server name / OAuth status badge / tool count / last-used
  timestamp / Connect / Reauthorize / Disconnect affordances). The
  fetch is `transport.request<{servers: MCPServer[]}>({method:'GET',
path:'/v1/mcp/servers'})`. Affordance choice is derived from
  `auth_state` (`unauthenticated`/`auth_failed` → Connect; `auth_pending`
  → "Authorizing…"; `authenticated` → Reauthorize + Disconnect;
  `auth_skipped`/`auth_unsupported` → Disconnect only).
- FR-6: Clicking a `ConnectorsDestination` card calls
  `router.navigate({kind:'mcp', serverId})`. Card affordance
  buttons stop propagation; they are no-ops in this phase.
- FR-7: Each destination has four UI states, all covered by tests:
  - skeleton (render placeholder rows / cards while the initial
    request is in flight),
  - error (one-line message + Retry button that re-triggers the
    fetch),
  - empty (one-line guidance when the response is empty),
  - populated (renders the rows / cards).
- FR-8: All destination bodies are full-height (consume the parent
  grid cell) and overflow scroll within their own container — the
  shell's main-body slot is already overflow-auto, but each
  destination scrolls its own table / grid so the destination header
  - filters remain sticky.
- FR-9: All interactive elements are keyboard-reachable
  (`<button type="button">`) and announce their role via accessible
  names — column-header sort buttons announce the active direction
  with `aria-sort`.

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on interface fields by
  default; no non-null assertions.
- Functional components + hooks only.
- Substrate-port discipline: no `window` / `document` / `fetch` /
  `localStorage` / `EventSource` / `XMLHttpRequest` references.
  Enforced by `packages/chat-surface/eslint.config.js`.
- One `useEffect` per destination for the fetch lifecycle. Sort and
  filter state derives inline (or via `useMemo`); no `useEffect` for
  derived state.
- No new third-party dependency.
- Test coverage: one `.test.tsx` per destination, covering all five
  scenarios from the prompt (skeleton → populated → empty → error →
  click-navigates correctly).
- Comments: default to none; one short line allowed when the _why_
  is non-obvious. No multi-paragraph docstrings, no narrating
  comments.

## Interfaces consumed

- `useTransport()` from `../../providers/TransportProvider`. Each
  destination issues a single `transport.request<TRes>(...)` call
  with the typed response shape.
- `useRouter<ArtifactRoute>()` from `../../providers/RouterProvider`.
  Used for row / card click navigation only — destinations never
  call `router.current()` or `router.subscribe(...)` (their content
  is route-agnostic).
- `Skill`, `McpServer`, `McpAuthState` from
  `@enterprise-search/api-types`. The `Run` view shape is destination-
  local (see Open question 2).
- Design-system primitives from `@enterprise-search/design-system`:
  `Button`, `Badge`, `TextInput`, `Select`, `StatusPill`,
  `AppIcon`. CSS is loaded by the host app at root; destinations
  inherit it via the `ui-*` class names baked into the primitives.

## Interfaces produced

```ts
// packages/chat-surface/src/destinations/agents/index.ts (NEW)
export { AgentsDestination } from "./AgentsDestination";

// packages/chat-surface/src/destinations/tools/index.ts (NEW)
export { ToolsDestination } from "./ToolsDestination";

// packages/chat-surface/src/destinations/connectors/index.ts (NEW)
export { ConnectorsDestination } from "./ConnectorsDestination";
```

Component signatures:

```ts
export function AgentsDestination(): ReactElement;
export function ToolsDestination(): ReactElement;
export function ConnectorsDestination(): ReactElement;
```

When this phase merges, the orchestrator appends a Phase 3-C block
to `packages/chat-surface/src/index.ts` re-exporting the three
components. The orchestrator owns the edit (do not modify
`index.ts` from this agent's scope).

## Open questions

1. **Empty-state copy and Install / Manage / Connect button labels.**
   The PRD specifies the affordances but not their exact strings.
   I follow Atlas product copy norms (sentence-case, no trailing
   punctuation, verbs preferred): "Install", "Manage", "Connect",
   "Reauthorize", "Disconnect". The empty-state strings are
   destination-specific ("No agent runs yet.", "No skills
   installed.", "No connectors yet."). Flagging in case a copy pass
   wants to retune these.

2. **`Run` view shape vs `RunStatus`.** The on-disk `RunStatus`
   shape in `@enterprise-search/api-types` carries `run_id`,
   `conversation_id`, `status`, `started_at`, `completed_at` and a
   `latest_sequence_no` — it does not carry `agent_name`, `model`,
   `tokens`, or `latency`, which are the columns the PRD lists for
   the Agents destination. The cleanest fix is a backend-side list
   endpoint that returns an aggregated row shape (the current
   `/v1/agent/runs` returns runs with the limited columns from
   `RunStatus`). For this phase the destination defines a local
   `Run` shape that mirrors what the desktop will eventually
   receive; tests fixture this shape directly. When the server
   payload catches up, the local shape is replaced by the canonical
   one from `api-types` in a single PR.

3. **OAuth-status mapping.** The `McpAuthState` union covers
   `unauthenticated` / `auth_skipped` / `auth_pending` /
   `authenticated` / `auth_failed` / `auth_unsupported`. I map
   these onto three UI states: `Connect` (when unauthenticated /
   failed), `Reauthorize` (when authenticated — re-doing the flow),
   `Disconnect` (when authenticated / skipped / unsupported and the
   user wants to remove the server). The PRD names these
   "connected" / "expired" / "needs-auth"; my mapping is the
   minimal sufficient set for the on-disk enum. Flagging for
   orchestrator review — the right long-term move is probably to
   add a derived `oauth_status: "connected"|"expired"|"needs-auth"`
   field to `McpServer` so the UI does not derive it from the raw
   enum.

4. **`onClick` propagation on cards with sub-buttons.** Both the
   Tools and Connectors destinations render cards that are
   themselves clickable (navigate to artifact) _and_ contain
   sub-buttons (Install / Manage / Connect / etc.). I use
   `event.stopPropagation()` on the sub-button handlers so they do
   not also navigate. This is the smallest pattern that works; an
   alternative (split the navigate target into its own element so
   the card is not clickable as a whole) reads worse — the card
   _is_ the click target for the user.

5. **Sticky filters / table header.** I use `position: sticky` on
   the filter row and table header inside each destination so the
   destination's chrome stays visible while the body scrolls.
   `position: sticky` is a standard CSS feature; it does not
   reference `window` / `document` / etc. and is fully substrate-
   portable.

## Done criteria

- All FRs met.
- `npm run typecheck --workspace @enterprise-search/chat-surface`
  passes.
- `npm test --workspace @enterprise-search/chat-surface` passes.
- `npm run lint --workspace @enterprise-search/chat-surface` passes.
- No imports outside scope.
- No bare browser primitives (`window` / `document` / `fetch` /
  `localStorage` / `EventSource`) anywhere in this scope — enforced
  by the existing chat-surface ESLint rule.
- No new third-party dependency.
- `packages/chat-surface/src/index.ts` is NOT modified by this
  agent — re-exports are listed in the Interfaces produced section
  and the orchestrator wires them in.

### Deferred / carried forward

- Open Q1 (copy review), Q2 (canonical `Run` shape in `api-types`),
  and Q3 (`oauth_status` derived field on `McpServer`) remain open
  and are flagged for orchestrator follow-up.
- Install / Manage / Connect / Reauthorize / Disconnect buttons are
  no-ops in this phase — they render and are click-isolated from
  the card, but they do not call backend endpoints. Wiring the
  real flows is a later phase's concern (skills install / MCP
  OAuth flow already live in `backend` + `apps/frontend`; the
  desktop wiring will reuse them via Transport).
- ContextPanel per-destination filter rows (today: generic
  placeholders) are not refined in this scope.

## Notes for orchestrator review

- The three destinations share their loading / error / empty /
  populated state-machine shape, but I deliberately kept them as
  three independent components rather than extracting a shared
  `useFetched` hook. The shape is ~15 LOC per destination; the
  earliest moment a shared hook is worth introducing is when
  multiple destinations also share a _retry policy_ / _abort
  policy_ — and that decision belongs to Phase 5 (auth) when 401
  retries enter the picture. Pre-emptively extracting now would
  freeze a contract that is likely to grow.
- Filters render as inline `<Select>` and `<TextInput>` from the
  design system. Filter state is local to each destination; the
  ContextPanel is a separate slot in the shell and renders the
  generic filter rows from Phase 1-B in this phase. A later
  phase (likely 3-followup) replaces the placeholder rows with
  the destination's filter list and removes the in-body filter
  bar in favor of the panel's.
- All three destinations consume `useRouter<ArtifactRoute>()` —
  they navigate via the existing `ArtifactRoute` kinds and never
  introduce a new variant. The "Open Q1" thread from Phase 1-B
  (ArtifactRoute coverage gap for non-artifact destinations like
  home / inbox) does not apply here — `run`, `skill`, and `mcp`
  are all already covered.
