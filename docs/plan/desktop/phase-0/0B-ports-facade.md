# Phase 0.B: ports-facade

## Vision

The PRD's §3.2 lists `packages/chat-surface/src/ports/` as the single named home
for the substrate-port interfaces (`Transport`, `Router`, `KeyValueStore`,
`PresenceSignal`, `SurfaceHost`). Today three of those (`Router`,
`KeyValueStore`, `PresenceSignal`) live in their feature directories
(`src/routing/`, `src/storage/`, `src/presence/`); `Transport` lives in a
sibling package (`@0x-copilot/chat-transport`); `SurfaceHost` is not yet
defined anywhere.

Staff-engineer take: do not relocate the producers. Re-export from a thin
`ports/` facade. The producer modules already encode load-bearing comments
about substrate intent and have existing consumers (web providers,
chat-transport `WebTransport`, tests). Moving them would churn imports across
the package and the apps for no behaviour change, and would re-open boundary
questions that the on-disk modules have already answered. A re-export facade
gives the rest of Phase 0+ a single canonical import path
(`@0x-copilot/chat-surface/ports`) without disturbing the producers.

`SurfaceHost` is the one new interface introduced in this phase. It is defined
once, here, and is intentionally not consumed in MVP — it exists so Phase 4-a
(when `TcSurfaceMount` is built) and Phase 6 (tier-2 dynamic adapter loading)
have a stable shape to target. The interface is deliberately minimal: pure
surface lifecycle (mount/unmount/pause/snapshot/events). No I/O — surfaces
talk to MCP via the host's `TcSurfaceMount`, not via this port (D28).

## Status

- Status: in-progress
- Agent slug: `ports-facade`
- Branch: `desktop/phase-0-ports`
- Worktree: `.claude/worktrees/agent-a62aa034d4dd4e66e`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-0/0B-ports-facade.md` — this file.
- `packages/chat-surface/src/ports/Transport.ts` — re-export from `@0x-copilot/chat-transport`.
- `packages/chat-surface/src/ports/Router.ts` — re-export from `../routing/router`.
- `packages/chat-surface/src/ports/KeyValueStore.ts` — re-export from `../storage/key-value-store`.
- `packages/chat-surface/src/ports/PresenceSignal.ts` — re-export from `../presence/presence-signal`.
- `packages/chat-surface/src/ports/SurfaceHost.ts` — NEW interface, original definition (not a re-export).
- `packages/chat-surface/src/ports/index.ts` — barrel.
- `packages/chat-surface/src/ports/index.test.ts` — round-trip import check.
- `packages/chat-surface/src/index.ts` — append a clearly-delimited Phase 0-B block; no other edits.

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/surfaces/**` — Agent 0-A's territory.
- `packages/chat-surface/src/{routing,storage,presence}/**` — producers are left as-is.
- `packages/chat-transport/**` — unchanged.
- `packages/surface-renderers/**` — Phase 4-a's territory.
- `apps/desktop/**` — Agent 0-C's territory.
- `eslint.config.js` and any ESLint settings — Agent 0-D's territory.
- Anything in `services/*`.

## Functional requirements

- [ ] FR-1 — `import { ... } from '@0x-copilot/chat-surface/ports'`
      resolves all of: `Transport`, `Session`, `SseSubscribeOptions`,
      `SseSubscription`, `TransportCapabilities`, `TypedRequest`, `HttpMethod`,
      `QueryParamValue`, `Router`, `ArtifactRoute`, `NavigateOptions`,
      `KeyValueStore`, `PresenceSignal`, `PresenceState`, `SurfaceHost`,
      `SurfaceHandle`, `SurfaceEvent`, `UnauthorizedError`. Type-only members
      flow as `export type`; the one value (`UnauthorizedError`) flows as a
      value export.
- [ ] FR-2 — The re-exported `Transport`, `Router`, `KeyValueStore`,
      `PresenceSignal` types are referentially the same types as the
      producers'. A consumer that imports `Router` from the producer path and
      another that imports `Router` from the ports facade hold an
      assignment-compatible reference (no structural drift).
- [ ] FR-3 — The `SurfaceHost` interface is defined exactly per PRD §3.3:
      `mountSurface` / `unmountSurface` / `pauseSurface` / `resumeSurface` /
      `snapshotSurface` / `onSurfaceEvent`. `SurfaceHandle` exposes one
      field (`readonly id: string`); `SurfaceEvent` carries
      `readonly surfaceId: string`, `readonly type: string`, and an
      optional `readonly payload?: unknown`. Both are minimal placeholders.
- [ ] FR-4 — `packages/chat-surface/src/index.ts` re-exports the entire
      `ports/` barrel via a delimited `// === Phase 0-B ports facade ===` block,
      so existing import paths (`@0x-copilot/chat-surface`) keep working
      and any new code can prefer either the package root or the
      `…/ports` subpath.
- [ ] FR-5 — Round-trip import test exists at
      `packages/chat-surface/src/ports/index.test.ts`. It asserts that the
      runtime value(s) re-exported from the barrel (`UnauthorizedError`) match
      the producer, and uses `expectTypeOf`-style or assignment-only checks
      to prove the type re-exports are referentially the same as the producer
      types.

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on interface fields by default.
- Type-only imports use `import type`.
- No new comments anywhere except the one header on `SurfaceHost.ts` that
  explains the deferred-consumption design (per PRD §6.1 + the orchestrator's
  explicit carve-out).
- No new third-party dependency.
- Test coverage: one test file (`ports/index.test.ts`). These are re-exports,
  not new logic — there is nothing else to assert.

## Interfaces consumed

- `Transport`, `Session`, `SseSubscribeOptions`, `SseSubscription`,
  `TransportCapabilities`, `TypedRequest`, `HttpMethod`, `QueryParamValue`,
  `UnauthorizedError` — from `@0x-copilot/chat-transport`
  (`packages/chat-transport/src/{transport.ts,types.ts}`).
- `Router`, `ArtifactRoute`, `NavigateOptions` — from
  `packages/chat-surface/src/routing/router.ts`.
- `KeyValueStore` — from `packages/chat-surface/src/storage/key-value-store.ts`.
- `PresenceSignal`, `PresenceState` — from
  `packages/chat-surface/src/presence/presence-signal.ts`.

## Interfaces produced

```ts
// packages/chat-surface/src/ports/index.ts (and equivalent per-port files)

export type {
  Transport,
  Session,
  SseSubscribeOptions,
  SseSubscription,
  TransportCapabilities,
  TypedRequest,
  HttpMethod,
  QueryParamValue,
} from "./Transport";
export { UnauthorizedError } from "./Transport";

export type { Router, ArtifactRoute, NavigateOptions } from "./Router";
export type { KeyValueStore } from "./KeyValueStore";
export type { PresenceSignal, PresenceState } from "./PresenceSignal";
export type { SurfaceHost, SurfaceHandle, SurfaceEvent } from "./SurfaceHost";
```

```ts
// packages/chat-surface/src/ports/SurfaceHost.ts (NEW — original definition)

export interface SurfaceHost {
  mountSurface(args: {
    readonly id: string;
    readonly uri: string;
    readonly rect: DOMRect;
  }): Promise<SurfaceHandle>;
  unmountSurface(id: string): Promise<void>;
  pauseSurface(id: string): Promise<void>;
  resumeSurface(id: string): Promise<void>;
  snapshotSurface(id: string, t: number): Promise<Blob>;
  onSurfaceEvent(handler: (event: SurfaceEvent) => void): () => void;
}

export interface SurfaceHandle {
  readonly id: string;
}

export interface SurfaceEvent {
  readonly surfaceId: string;
  readonly type: string;
  readonly payload?: unknown;
}
```

## Open questions

1. **`Router` is generic on disk (`Router<TRoute>`), not the
   parameter-less shape sketched in PRD §3.3.** The orchestrator's prompt
   says "use the actual names on disk, don't invent." So the facade
   re-exports `Router<TRoute>` verbatim. PRD §3.3 also describes a
   different `Route` union (`{ destination, view?, id? }`) where the on-disk
   producer defines `ArtifactRoute` as a discriminated union of artifact
   kinds. These are not equivalent — they model two different routing
   responsibilities. Flagging for orchestrator review: the PRD §3.3 sketch
   appears to be aspirational; the on-disk `ArtifactRoute` is what's used
   today and what spike-prep took a dependency on. **Proceeding with the
   on-disk shape.** If the PRD wins, the producer module needs to change,
   not the facade.

2. **`Transport` on disk uses `subscribeServerSentEvents` and `getSession()`
   (sync, returns `Session`), not `subscribeRunStream` /
   `getSession(): Promise<Session | null>` per PRD §3.3.** The orchestrator
   prompt is explicit: use the on-disk shape. Doing so. Same flag: the PRD
   §3.3 sketch is aspirational; producer is the source of truth. No
   `reauthenticate` either — re-exporting only the symbols the producer
   actually exports.

3. **`SurfaceHandle` / `SurfaceEvent` are minimal placeholders.** They
   carry only the fields needed to satisfy the `SurfaceHost` signature.
   Phase 4 (`TcSurfaceMount`) and Phase 6 (tier-2 sandbox lifecycle events)
   will almost certainly need to refine these — likely adding bounding-rect
   updates, focus events, and a richer event-type discriminated union.
   Flagging so the orchestrator can sequence the refinement before
   tier-2 work locks in a shape.

4. **Name normalization opportunity (no action this phase).** PRD §3.3 uses
   the verbs `current/navigate/back/subscribe` on `Router`; the on-disk
   `Router<TRoute>` has `current/navigate/subscribe` and no `back`. If
   Phase 1-D (`routing-palette`) needs `back`, the producer should grow it
   — not the facade. Flagging.

## Done criteria

- [ ] All FRs met
- [ ] `npm run typecheck --workspace @0x-copilot/chat-surface` passes
- [ ] `npm test --workspace @0x-copilot/chat-surface` passes
- [ ] `npm run lint --workspace @0x-copilot/chat-surface` passes
- [ ] No imports outside scope
- [ ] No bare browser primitives in `chat-surface/src/ports/**`
- [ ] No new third-party dependency
- [ ] `packages/chat-surface/src/index.ts` only gains the delimited Phase 0-B
      block; all pre-existing exports are untouched

## Notes for orchestrator review

- The facade is **re-export only** — producers stay in their feature
  directories. The PRD §3.2 layout sketch shows `ports/Transport.ts` as
  "re-export from chat-transport"; the same convention is applied to
  `Router` / `KeyValueStore` / `PresenceSignal`. Anyone updating a port
  interface still edits the producer, not the facade.
- `index.ts` extension is **append-only inside the marked block**. Agent
  0-A is appending in parallel; the orchestrator merges by stacking blocks
  end-to-end. No edits to any pre-existing export line.
- The `SurfaceHost.ts` header comment is the only narrative comment in this
  scope. It explains the non-obvious "defined here but unused in MVP"
  intent — without it, a future maintainer would reasonably delete the
  interface as dead code.
- Three open questions above (1, 2, 4) flag shape mismatches between PRD
  §3.3 and the on-disk producers. Per the orchestrator prompt, I used the
  on-disk shape. The reconciliation belongs in the orchestrator's merge
  review, not in this agent's scope.
