# Phase 0.5: spike-prep (S0)

## Status

- Status: in-progress
- Agent slug: `spike-prep`
- Branch: `spike/0.5-prep`
- Worktree: `.claude/worktrees/agent-a146d69cb6b7b4668`
- Created: 2026-05-17

## Scope

The substrate-independent foundation for the S1 spike. Two variant agents
(S1-A VS Code extension, S1-B Electron) will each be a thin substrate shell
that mounts `<EmailRenderer transport={mockTransport} ... />` and nothing
else inside the renderer. If those two shells require any per-substrate
changes inside the renderer or transport, this sub-PRD is wrong and the
work is rejected at orchestrator review.

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-0.5/S0-spike-prep.md` — this file.
- `packages/surface-renderers/package.json`
- `packages/surface-renderers/tsconfig.json`
- `packages/surface-renderers/eslint.config.js`
- `packages/surface-renderers/vitest.config.ts`
- `packages/surface-renderers/src/index.ts`
- `packages/surface-renderers/src/test/setup.ts`
- `packages/surface-renderers/src/email/EmailRenderer.tsx`
- `packages/surface-renderers/src/email/EmailDiffOverlay.tsx`
- `packages/surface-renderers/src/email/EmailRenderer.test.tsx`
- `packages/surface-renderers/src/email/EmailDiffOverlay.test.tsx`
- `packages/surface-renderers/src/email/index.ts`
- `packages/chat-surface/src/surfaces/SurfaceRegistry.ts`
- `packages/chat-surface/src/surfaces/types.ts`
- `packages/chat-surface/src/surfaces/SurfaceRegistry.test.ts`
- `packages/chat-surface/src/surfaces/index.ts`
- `packages/chat-surface/src/routing/uri/schemes.ts`
- `packages/chat-surface/src/routing/uri/parser.ts`
- `packages/chat-surface/src/routing/uri/parser.test.ts`
- `packages/chat-surface/src/routing/uri/index.ts`
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx`
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.test.tsx`
- `packages/chat-surface/src/thread-canvas/index.ts`
- `packages/chat-surface/src/index.ts` — extend exports only
- `packages/chat-transport/src/mock/MockTransport.ts`
- `packages/chat-transport/src/mock/email-fixture.ts`
- `packages/chat-transport/src/mock/MockTransport.test.ts`
- `packages/chat-transport/src/mock/index.ts`
- `packages/chat-transport/src/index.ts` — extend exports only
- `packages/chat-transport/vitest.config.ts` — new (transport package has no test infra yet)
- `packages/chat-transport/package.json` — add vitest dev deps + script
- `packages/chat-surface/vitest.config.ts` — new (same reason)
- `packages/chat-surface/package.json` — add vitest dev deps + script

**Out of scope** (do NOT touch):

- `apps/frontend/*` — the existing web app does not consume these new pieces
  in S0. Phase 0 wires them up in `apps/frontend` if Electron loses; Phase 2
  wires them up in `apps/desktop` if Electron wins.
- `packages/chat-transport/src/web/*` and `packages/chat-transport/src/transport.ts` —
  the on-disk Transport interface is canonical and frozen for the spike.
- Anything in `services/*`.
- `apps/desktop/*` and `apps/vscode-spike/*` — built by the S1 variant
  agents.
- Existing `packages/chat-surface/src/{shell,routing,messages,citations,presence,providers,storage}`
  except for the surgical additions to `src/index.ts`.

## Functional requirements

- [ ] FR-1 — `parseArtifactUri(raw)` returns `{ scheme, body }` for any
      scheme listed in `ARTIFACT_SCHEMES`, returns `null` for malformed input,
      unknown schemes, missing colon, empty body, or empty scheme.
      `buildArtifactUri({ scheme, body })` is the round-trip inverse. Round-trip
      is exact for every registered scheme.
- [ ] FR-2 — `SurfaceRegistry.registerSurface(scheme, component)` is
      idempotent for the same `(scheme, component)` and throws if a different
      component is registered against an already-claimed scheme. `resolveSurface(uri)`
      returns the registered component, or `null` when the URI is malformed or
      unmatched. `clearRegistry()` empties the table and is intended for tests.
- [ ] FR-3 — `MockTransport` implements the on-disk `Transport` interface
      exactly. `request({ path: '/drafts/draft-1', ... })` resolves to the
      draft fixture (To/Cc/Subject/initial body). `subscribeServerSentEvents({
path: '/drafts/draft-1/events', ... })` delivers — in order over
      ~3 seconds wall clock — `tool_call_start` (t=0), `tool_call_chunk` × 5
      (t=400, 800, 1200, 1600, 2000), `tool_call_end` (t=2400),
      `pending_diff_appeared` (t=2700). Calling `subscription.close()` cancels
      every still-pending timer and prevents any further `onMessage` calls.
- [ ] FR-4 — `TcInlineDiff` renders one card per state in
      `'idle' | 'streaming' | 'pending' | 'accepted' | 'rejected'`. Approve and
      Reject buttons render only in `'pending'`; Approve/Reject fire the
      corresponding callbacks when clicked or activated with Enter/Space.
      Streaming state shows the `progressPercent` value and the provenance pill.
- [ ] FR-5 — `EmailDiffOverlay` wraps `TcInlineDiff`, anchors itself with
      `position: absolute` to its parent, and forwards `onApprove`/`onReject`
      to the props passed in. Visual states match `TcInlineDiff` 1:1.
- [ ] FR-6 — `EmailRenderer`, given a `transport`, calls
      `transport.request({ method: 'GET', path: '/drafts/draft-1' })` once on
      mount, opens a `subscribeServerSentEvents` to
      `/drafts/draft-1/events` once on mount, accumulates `tool_call_chunk`
      payloads into the PENDING block, and mounts `EmailDiffOverlay` when
      `pending_diff_appeared` arrives. When `activeDiff` is passed in by the
      parent (the variant agent's shell), the overlay uses that payload
      directly and the parent's `onApproveDiff` / `onRejectDiff` fire on click.
- [ ] FR-7 — `EmailRenderer` renders the visual layout from the design
      bundle screenshot: "New message" label + Drafting pill + Save draft
      button header row; labeled To/Cc/Subject rows; body with greeting,
      paragraph, PENDING anchor block, closing; Send/Schedule buttons + auto-
      saved label footer. All interactive elements are real `<button>`s with
      visible focus.
- [ ] FR-8 — `EmailRenderer` registers itself for the `email://` scheme
      via `registerSurface('email', EmailRenderer)` inside
      `packages/surface-renderers/src/email/index.ts`. `registerAll()` in
      `packages/surface-renderers/src/index.ts` is the public entry the
      variant shells call at bootstrap.

## Non-functional requirements

- **Performance** — Renderer's initial mount work is one `request` and one
  `subscribeServerSentEvents` call; both return synchronously (request is a
  promise the renderer awaits; subscription returns its handle). No
  intermediate buffering, no render-loop work outside the body-fragment
  accumulator state. Body fragment accumulation is a single `setState`
  with the concatenated string — re-render cost is the cost of re-rendering
  one `<div>` of body text per chunk (5 chunks total, so 5 re-renders for
  the body region).
- **Accessibility** — Semantic HTML: `<form>` wrapper for the composer (no
  submit-on-Enter side effect — `<form onSubmit={(e) => e.preventDefault()}>`),
  `<label>` linked to each labeled row, `<button>` for every interactive
  control. Approve/Reject buttons are keyboard-reachable (Tab) and
  activatable with Enter and Space (native `<button>` behavior). Focus
  visible via `outline: 2px solid` in the `:focus-visible` selector. The
  PENDING anchor block is `<section aria-label="Pending edit">` so screen
  readers announce it as a region.
- **Test coverage** — Vitest with `jsdom` + `@testing-library/react` for
  React components, vitest with default Node env for the URI parser and
  MockTransport. Tests cover every FR-1 / FR-2 branch (round-trip, malformed,
  unknown), MockTransport's event sequence + cancellation, TcInlineDiff's
  five states + button visibility + callback wiring, EmailDiffOverlay's
  state passthrough, and EmailRenderer's mount-time request + subscription
  - Approve callback wiring. No snapshot tests for components (per PRD §6.8).
- **Substrate portability** — The new surface-renderers package extends
  chat-surface's existing substrate-port ESLint rule: no bare `window`,
  `document`, `fetch`, `localStorage`, `EventSource`; no import from
  `apps/*` or `chat-surface/src/shell`. The ESLint rule is verified by
  running `npm run lint --workspace @enterprise-search/surface-renderers`.

## Interfaces consumed

- `Transport` from `@enterprise-search/chat-transport` —
  `packages/chat-transport/src/transport.ts`. The on-disk shape:
  `request<TRes>(req): Promise<TRes>`, `subscribeServerSentEvents(opts): SseSubscription`,
  `getSession(): Session`, `capabilities(): TransportCapabilities`.
- `SseSubscribeOptions`, `SseSubscription`, `Session`, `TypedRequest`,
  `TransportCapabilities` from `@enterprise-search/chat-transport` —
  `packages/chat-transport/src/types.ts`.
- `React`, `react/jsx-runtime` — peer.

The PRD §3.3 lists an aspirational `Transport.subscribeRunStream(runId, ...)`
shape with `Promise<Session>` from `getSession()` and `reauthenticate()`.
**The on-disk shape is authoritative**: `subscribeServerSentEvents` (not
`subscribeRunStream`), `getSession()` returns `Session` synchronously,
no `reauthenticate()` method. MockTransport implements the on-disk shape;
the PRD §3.3 contract is treated as future-state and is out of scope for
S0.

## Interfaces produced

```ts
// packages/chat-surface/src/routing/uri/schemes.ts
export const ARTIFACT_SCHEMES = {
  chat: "chat",
  conversation: "convo",
  run: "run",
  subagent: "subagent",
  toolResult: "tool-result",
  email: "email",
  sheetRow: "sheet-row",
  sfOpportunity: "sf-opp",
  slide: "slide",
  mcp: "mcp",
  mcpTool: "mcp-tool",
  skill: "skill",
  workspace: "workspace",
  timeMachine: "time-machine",
} as const;
export type ArtifactScheme =
  (typeof ARTIFACT_SCHEMES)[keyof typeof ARTIFACT_SCHEMES];

// packages/chat-surface/src/routing/uri/parser.ts
export interface ParsedArtifactUri {
  readonly scheme: ArtifactScheme;
  readonly body: string;
}
export function parseArtifactUri(raw: string): ParsedArtifactUri | null;
export function buildArtifactUri(parts: ParsedArtifactUri): string;
export function isArtifactScheme(value: string): value is ArtifactScheme;

// packages/chat-surface/src/surfaces/types.ts
export interface PendingDiff {
  readonly diffId: string;
  readonly provenance: string;
  readonly title: string;
  readonly description?: string;
  readonly regionAnchorId: string;
}
export interface SurfaceRendererProps {
  readonly uri: string;
  readonly transport: Transport;
  readonly activeDiff?: PendingDiff | null;
  readonly onApproveDiff?: (diffId: string) => void;
  readonly onRejectDiff?: (diffId: string) => void;
}

// packages/chat-surface/src/surfaces/SurfaceRegistry.ts
export function registerSurface(
  scheme: string,
  component: React.ComponentType<SurfaceRendererProps>,
): void;
export function resolveSurface(
  uri: string,
): React.ComponentType<SurfaceRendererProps> | null;
export function clearRegistry(): void;

// packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx
export type InlineDiffState =
  | "idle"
  | "streaming"
  | "pending"
  | "accepted"
  | "rejected";
export interface TcInlineDiffProps {
  readonly state: InlineDiffState;
  readonly progressPercent?: number;
  readonly provenance?: string;
  readonly title: string;
  readonly description?: string;
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
  readonly approveLabel?: string;
  readonly rejectLabel?: string;
}
export function TcInlineDiff(props: TcInlineDiffProps): React.ReactNode;

// packages/surface-renderers/src/email/EmailRenderer.tsx
export function EmailRenderer(props: SurfaceRendererProps): React.ReactNode;

// packages/surface-renderers/src/email/EmailDiffOverlay.tsx
export interface EmailDiffOverlayProps {
  readonly diff: PendingDiff;
  readonly state: InlineDiffState;
  readonly progressPercent?: number;
  readonly onApprove: () => void;
  readonly onReject: () => void;
}
export function EmailDiffOverlay(props: EmailDiffOverlayProps): React.ReactNode;

// packages/surface-renderers/src/index.ts
export function registerAll(): void;

// packages/chat-transport/src/mock/MockTransport.ts
export interface MockTransportConfig {
  readonly setTimeoutImpl?: (handler: () => void, ms: number) => unknown;
  readonly clearTimeoutImpl?: (handle: unknown) => void;
  readonly capabilities?: Partial<TransportCapabilities>;
  readonly session?: Session;
}
export class MockTransport implements Transport {
  constructor(config?: MockTransportConfig);
  request<TRes>(req: TypedRequest): Promise<TRes>;
  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription;
  getSession(): Session;
  capabilities(): TransportCapabilities;
}

// packages/chat-transport/src/mock/email-fixture.ts
export interface EmailFixtureDraft {
  readonly draftId: string;
  readonly to: string;
  readonly cc: string;
  readonly subject: string;
  readonly bodyPrefix: string;
  readonly bodySuffix: string;
}
export interface EmailFixturePendingDiff {
  readonly diffId: string;
  readonly provenance: string;
  readonly title: string;
  readonly description: string;
  readonly regionAnchorId: string;
}
export const EMAIL_FIXTURE: {
  readonly draft: EmailFixtureDraft;
  readonly streamingBodyChunks: readonly string[];
  readonly pendingDiff: EmailFixturePendingDiff;
};
```

## Open questions

1. **Mock event payload schema.** The on-disk Transport defines SSE as raw
   message strings (no envelope). The MockTransport emits JSON-encoded
   payloads with a top-level `type` field (`"tool_call_start"`,
   `"tool_call_chunk"`, `"tool_call_end"`, `"pending_diff_appeared"`) plus
   payload fields. EmailRenderer parses with `JSON.parse(raw)` and narrows
   by `type`. This shape is **spike-local** — the production event schema
   from `ai-backend` is `RuntimeEventEnvelope`, defined in services/ai-backend
   and not yet exported via `packages/api-types`. Flag for orchestrator:
   if Phase 0 wants this fixture realigned to the real envelope, that's a
   2-file diff (`MockTransport.ts` + `EmailRenderer.tsx` parsing branch).
2. **Deep import vs main export for MockTransport.** Both `MockTransport`
   and `EMAIL_FIXTURE` are exported from `packages/chat-transport/src/index.ts`
   (the main barrel) so variant shells can `import { MockTransport, EMAIL_FIXTURE }
from '@enterprise-search/chat-transport'`. A `package.json#exports`
   subpath was considered but adds Node 18+ exports-conditions machinery
   that none of our other packages use; main-barrel is consistent.
3. **Vitest in chat-surface / chat-transport.** Neither package currently
   has test infrastructure (only `apps/frontend` does). S0 adds a minimal
   `vitest.config.ts` + dev-deps to both. This is a deliberate deviation
   from "use the existing setup" — the alternative would be to put the
   shared tests in `apps/frontend/src/test/` which violates the package
   boundary (chat-surface shouldn't have tests living inside an app). Flag
   for orchestrator: confirm vitest-per-package is the desired norm going
   forward. Phase 0's "ESLint rule blocks a deliberately-bad sample" test
   will need somewhere to live, and this scaffolding gives it a home.

## Done criteria

- [ ] All FRs met.
- [ ] `npm run typecheck --workspace @enterprise-search/chat-transport
--workspace @enterprise-search/chat-surface
--workspace @enterprise-search/surface-renderers` passes.
- [ ] `npm test --workspace @enterprise-search/chat-transport
--workspace @enterprise-search/chat-surface
--workspace @enterprise-search/surface-renderers` passes.
- [ ] `npm run lint --workspace @enterprise-search/chat-surface
--workspace @enterprise-search/surface-renderers` passes.
- [ ] No imports outside the in-scope list above.
- [ ] No bare browser primitives (chat-surface + surface-renderers).
- [ ] No new third-party dependency without justification — added vitest +
      jsdom + @testing-library/react + @testing-library/jest-dom +
      @testing-library/user-event + @vitejs/plugin-react to chat-surface and
      vitest only to chat-transport. Same versions apps/frontend already
      pins, no new versions introduced. Justification: tests cannot run
      otherwise.

## Notes for orchestrator review

- The aspirational `Transport` shape in PRD §3.3 (with `subscribeRunStream`,
  `Promise<Session>`, `reauthenticate()`) does not match the on-disk shape.
  S0 follows the on-disk shape verbatim. If the team wants the on-disk
  shape evolved to match §3.3, do it in a dedicated PR before Phase 2 (it
  will break `apps/frontend`'s WebTransport consumers).
- The `EmailRenderer` accepts an optional `activeDiff` prop for the spike,
  which is **not** in the §3.3 production `SurfaceRendererProps`. Reason:
  the variant agents need a simple way to drive the renderer from outside
  during their substrate-portability demo — production renderers will
  subscribe to the transport's run stream themselves and derive the diff
  state internally, but that machinery is Phase 2/4 work.
- The PRD lists `Promise<void>` return types for the production
  `onApproveDiff` / `onRejectDiff`. S0 uses synchronous `(id) => void` —
  the spike doesn't need to await a backend round-trip. Production will
  reintroduce `Promise<void>` when the real approve/reject endpoints are
  wired.
- `registerSurface` throws on a conflicting re-registration. This is a
  spike decision — production may prefer last-write-wins or a registration
  ordering. Flag for Phase 0 ports work.
