# Phase 1.D: routing-palette

## Vision

Routing in the Atlas desktop is hash-based, single-source-of-truth inside
`chat-surface`, and substrate-portable. The `Router<TRoute>` port (frozen on
disk) already has the right shape: `current` / `navigate` / `subscribe`. This
phase ships the web-substrate reference implementation (`HashRouter`) that
maps the browser URL hash to `ArtifactRoute` and back, plus the route table
that names the destinations and components for Phase 1, plus the Cmd+K
command palette that drives `navigate` from a single keystroke.

The substrate touchpoints (`globalThis.window`, `globalThis.document`,
`globalThis.location`) live here for the same reason `LocalStorageKeyValueStore`
and `DocumentPresenceSignal` live in chat-surface: they are the web reference
implementations of a port. The desktop substrate will provide a different
implementation (deep-link-aware, `BrowserWindow`-scoped) that fulfills the
same `Router<ArtifactRoute>` contract — surface code never branches on
substrate.

Staff-engineer take on the URI mapping: the hash format reads
`#/{scheme}/{body}` and the existing `parseArtifactUri` / `buildArtifactUri`
modules already own scheme validation and round-tripping. The HashRouter
slices `#/` off the hash, rebuilds a canonical `scheme://body` string, and
delegates to `parseArtifactUri`. The reverse maps each `ArtifactRoute` kind
to its `ArtifactScheme` and serializes the body. The router does not
re-implement scheme parsing — there is one parser, one source of truth.

The route table is keyed by `ArtifactRoute['kind']`, not by the 11
top-level Atlas destinations (home, chats, agents, library, inbox, tools,
projects, todos, connectors, team, memory). `ArtifactRoute` is the
_artifact_ route union — it covers chat / conversation / run / subagent /
tool-result / mcp / mcp-tool / skill / workspace (9 kinds). The 11
destinations are an AppRail concept; mapping them onto `ArtifactRoute`
is a Phase 1-B / Phase 3 concern. Phase 1-D ships the 9 entries that
`ArtifactRoute` actually covers and flags the gap.

## Status

- Status: done
- Agent slug: `routing-palette`
- Branch: `desktop/phase-1-routing-palette`
- Worktree: `.claude/worktrees/agent-a93c261122dd8ff31`
- Created: 2026-05-17
- Audited: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-1/1D-routing-palette.md` — this file.
- `packages/chat-surface/src/routing/HashRouter.ts` — `Router<ArtifactRoute | null>` impl.
- `packages/chat-surface/src/routing/HashRouter.test.ts` — unit tests.
- `packages/chat-surface/src/routing/route-table.ts` — `ROUTE_TABLE` + `RouteEntry`.
- `packages/chat-surface/src/routing/route-table.test.ts` — coverage tests.
- `packages/chat-surface/src/palette/CommandPalette.tsx` — Cmd+K palette.
- `packages/chat-surface/src/palette/CommandPalette.test.tsx` — interaction tests.
- `packages/chat-surface/src/palette/index.ts` — barrel.
- `packages/chat-surface/src/index.ts` — append-only Phase 1-D block.

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/shell/**` — Agent 1-B's territory.
- `packages/chat-surface/src/providers/RouterProvider.tsx` — left alone; the host instantiates `HashRouter` and passes it to the existing provider.
- `packages/chat-surface/src/routing/router.ts` — frozen port.
- `packages/chat-surface/src/routing/uri/**` — already shipped; this phase imports them, doesn't change them.
- `packages/chat-transport/**` — Agent 1-C's territory.
- `apps/desktop/**` — Agent 1-A's territory.
- ESLint config — substrate touchpoints (`globalThis.window`, `globalThis.document`, `globalThis.location`) use member access and already pass the existing rule.

## Functional requirements

- [x] FR-1 — `HashRouter` implements `Router<ArtifactRoute | null>`.
      `current()` returns the parsed `ArtifactRoute` for the current
      `globalThis.location.hash`, or `null` when the hash is empty or
      doesn't decode to a known scheme/body shape.
- [x] FR-2 — `navigate(route, options?)` writes the canonical hash and
      notifies subscribers — including the subscriber that triggered the
      `navigate` call. `replace: true` uses `globalThis.location.replace`-
      equivalent semantics (history-replace, not push); default is push.
- [x] FR-3 — `subscribe(listener)` registers a listener and returns an
      unsubscribe function. Listeners fire on every `hashchange` event
      AND on every `navigate` call, with the freshly-parsed route.
- [x] FR-4 — Construction: `new HashRouter()` uses the current
      `globalThis.location.hash`. `new HashRouter({ initialRoute })`
      overrides for tests (also writes the canonical hash on construction
      so `current()` and the substrate agree).
- [x] FR-5 — Hash format: `#/{scheme}/{body}` where `{scheme}` and
      `{body}` are the same values `buildArtifactUri` produces
      (`scheme://body`). Unknown / malformed hashes resolve to `null`,
      not to a synthesized default — silent defaulting masks routing bugs.
- [x] FR-6 — `ROUTE_TABLE` is a frozen record keyed by
      `ArtifactRoute['kind']`. Each `RouteEntry` carries: `kind`,
      `scheme` (matches `ARTIFACT_SCHEMES`), `label` (human string),
      `iconHint` (string keyword the AppRail can map), and `Component`
      (a React component rendered with `route` prop). Phase 1 components
      are stubs (`<div>{label}</div>`); Phase 3 replaces them.
- [x] FR-7 — `CommandPalette` opens on Cmd+K (Mac) or Ctrl+K (other
      platforms) via a `keydown` listener on `globalThis.document`;
      closes on Esc. Cmd+K toggles when already open. Search input
      auto-focuses on open.
- [x] FR-8 — Result list is built from two sources: (a) every
      `ROUTE_TABLE` entry rendered as `{label} · destination`, and
      (b) mock chat/project/library entries (placeholder data, fine for
      Phase 1). Substring-match against `label`, case-insensitive.
- [x] FR-9 — Arrow Up / Down moves selection (with wrap-around); Enter
      calls `router.navigate(...)` with the selected entry's route and
      closes the palette; Esc closes without navigating.
- [x] FR-10 — Mounts as a `position: fixed` scrim + centered card. No
      portals (substrate-port discipline — `createPortal` requires
      `document`).

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on interface fields by default.
- No bare browser globals — substrate touchpoints prefixed `globalThis.`
  (window / document / location), matching `LocalStorageKeyValueStore`
  and `DocumentPresenceSignal`.
- No new third-party dependency.
- CommandPalette ≈ 150 LOC excluding the in-memory index.
- Tests cover: initial parse, navigate, subscribe + unsubscribe,
  unknown-hash → null, notification-on-own-navigate, route-table
  kind coverage, label stability, palette open/close keystrokes,
  filtering, arrow-key navigation, Enter, Esc.

## Interfaces consumed

- `Router<TRoute>`, `ArtifactRoute`, `NavigateOptions` from `../routing/router`
- `ARTIFACT_SCHEMES`, `ArtifactScheme` from `../routing/uri/schemes`
- `parseArtifactUri`, `buildArtifactUri` from `../routing/uri/parser`
- `useRouter` from `../providers/RouterProvider`

## Interfaces produced

```ts
// packages/chat-surface/src/routing/HashRouter.ts
export interface HashRouterConfig {
  readonly initialRoute?: ArtifactRoute | null;
}
export class HashRouter implements Router<ArtifactRoute | null> {
  constructor(config?: HashRouterConfig);
  current(): ArtifactRoute | null;
  navigate(route: ArtifactRoute | null, opts?: NavigateOptions): void;
  subscribe(handler: (route: ArtifactRoute | null) => void): () => void;
}

// packages/chat-surface/src/routing/route-table.ts
export interface RouteEntry {
  readonly kind: ArtifactRoute["kind"];
  readonly scheme: ArtifactScheme;
  readonly label: string;
  readonly iconHint: string;
  readonly Component: React.ComponentType<{ readonly route: ArtifactRoute }>;
}
export const ROUTE_TABLE: Readonly<Record<ArtifactRoute["kind"], RouteEntry>>;

// packages/chat-surface/src/palette/CommandPalette.tsx
export interface CommandPaletteProps {
  readonly extraEntries?: ReadonlyArray<CommandPaletteEntry>;
}
export interface CommandPaletteEntry {
  readonly id: string;
  readonly label: string;
  readonly hint?: string;
  readonly route: ArtifactRoute;
}
export function CommandPalette(props: CommandPaletteProps): React.ReactNode;
```

## Open questions

1. **`Router<TRoute>['current']` is non-nullable on disk; this impl
   widens to `Router<ArtifactRoute | null>`.** The port comment says
   "Always defined — the implementation picks a default when the
   substrate is in an indeterminate state." The orchestrator prompt
   instead says `current()` returns `ArtifactRoute | null`. Per the
   orchestrator prompt, returning `null` is preferred over synthesizing
   a default route (silent defaulting masks routing bugs and the
   "default destination" concept doesn't have a Phase 1 owner). The
   `TRoute` parameter is `ArtifactRoute | null`, which is a valid
   instantiation of `Router<TRoute>` — type-safe, but a deliberate
   read of the comment. Flagging so the orchestrator can decide
   whether to:
   - Accept the wider instantiation (current choice), or
   - Replace the `null` with a synthesized "default route" once
     someone owns the default-destination decision, or
   - Tighten the port to `Router<TRoute | null>` if multiple
     implementations want this shape.

2. **ArtifactRoute coverage of Atlas destinations is incomplete.** The
   Atlas product model lists 11 top-level destinations: home, chats,
   agents, library, inbox, tools, projects, todos, connectors, team,
   memory. The on-disk `ArtifactRoute` covers 9 kinds: chat,
   conversation, run, subagent, tool-result, mcp, mcp-tool, skill,
   workspace. Direct matches: chats↔chat/conversation, agents↔(no
   direct), connectors↔mcp, tools↔mcp-tool, memory↔(no direct, possibly
   skill). Unmatched destinations: home, library, inbox, projects,
   todos, team, plus indirect ones above. The route-table ships entries
   for the 9 on-disk `ArtifactRoute` kinds; the AppRail (Phase 1-B / 3)
   will need either a wider top-level route union (`AppRoute = AtlasDestination
| ArtifactRoute`) or new `ArtifactRoute` kinds for the missing
   destinations. **Not blocking Phase 1-D**, but the orchestrator
   should adjudicate before Phase 3.

3. **Hash format ambiguity for kinds whose body contains `/`.** Schemes
   like `subagent` (`run-1/sub-1`), `tool-result` (`run-1/step-1`), and
   `mcp-tool` (`server-1/tool`) carry a slash in the body. Building the
   hash as `#/{scheme}/{body}` produces `#/subagent/run-1/sub-1`. The
   parser slices on the _first_ `/` after the scheme; bodies keep
   internal slashes intact. This matches `parseArtifactUri`'s contract
   (body is everything after `scheme://`). Tests cover this path.

4. **Mock data in the palette index.** Phase 1 needs real chat /
   project / library entries to come from `Transport.request`, but the
   transport is Phase 1-C's territory and the surface is substrate-
   agnostic. The palette ships a static placeholder array and exposes
   `extraEntries` so Phase 2+ can wire real data without forking the
   component. Flagging so the orchestrator knows to schedule the wire-up.

## Done criteria

- [x] All FRs met
- [x] `npm run typecheck --workspace @enterprise-search/chat-surface` passes
- [x] `npm test --workspace @enterprise-search/chat-surface` passes
      (28 HashRouter + 9 route-table + 12 CommandPalette tests)
- [x] `npm run lint --workspace @enterprise-search/chat-surface` passes
- [x] No imports outside scope
- [x] No bare browser globals (only `globalThis.` member access)
- [x] No new third-party dependency
- [x] `packages/chat-surface/src/index.ts` only gains the delimited
      Phase 1-D block; all pre-existing exports untouched

### Pending integration (not 1D's responsibility)

- Apps/desktop renderer still mounts `StubRouter` from 1A; the
  `HashRouter` displacement (one-line swap in
  `apps/desktop/renderer/bootstrap.tsx`) belongs to the Phase 2
  integration window.
- Open Q2 (ArtifactRoute coverage of 11 Atlas destinations) remains
  open — Phase 3 (or a route-union widening sub-PRD) owns the
  resolution. The shell currently no-ops the 8 unmatched tiles.
- Open Q4 (real palette entries via `Transport.request`) — wired
  when Phase 2+ ships transport-backed search.

## Notes for orchestrator review

- The HashRouter substrate touchpoint pattern matches
  `LocalStorageKeyValueStore` and `DocumentPresenceSignal`: deferred
  lookups via `globalThis.X` so test-time `vi.stubGlobal(...)` lands
  and SSR/pre-DOM substrates degrade gracefully (here, `current()`
  returns `null`).
- `HashRouter.navigate` calls subscribers synchronously after writing
  the hash, then the `hashchange` event would fire a second time. To
  prevent duplicate notifications, the impl tracks the last-emitted
  serialized hash and skips a duplicate emission. This is the
  load-bearing "notify on own navigate" invariant from the port
  comment — without the dedup, listeners would fire twice on every
  navigate.
- `CommandPalette` deliberately renders inline (no portal). For Phase 1
  the palette is mounted next to `<ChatShell />` inside the host bootstrap;
  Phase 1-B's `ChatShell` can later own the mount point. The fixed-position
  scrim covers the viewport regardless of mount depth.
- Mock palette entries are a minimal static array. They go away
  in Phase 2+ when Transport-backed search lands.
