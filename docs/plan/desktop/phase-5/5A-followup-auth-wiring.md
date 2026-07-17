# Phase 5A follow-up: auth wiring gaps

## Status

- Status: in-progress
- Agent slug: `phase-5-followup-auth-wiring`
- Branch: `desktop/phase-5-followup-auth-wiring`
- Worktree: `.claude/worktrees/agent-a1f3752e6e9b11071`

## Vision

Phase 5A (auth-integration) hasn't merged yet — `apps/desktop/main/auth/*`, `loopback-server`, and `oidc-client` don't exist on disk. This follow-up lands the **substrate-shaped contracts** the four audit findings call for so Phase 5A's eventual sub-agent has a sealed seam to plug into:

- a `Transport` decorator for token-refresh-on-401 that lives next to the other Transport adapters,
- a deep-link dispatcher that delivers `code`/`state` to an injected callback rather than logging,
- a `TransportBridge` that requires a `Transport` (no implicit `MockTransport` default),
- an append-only JSON-Lines auth audit sink with a typed event union.

Each contract is exercised by tests that don't assume `auth/*` exists; Phase 5A wires its real OIDC flow onto these contracts without further refactoring.

## Scope

In scope:

- `packages/chat-transport/src/auth/withBearerRefresh.ts` + colocated test
- `packages/chat-transport/src/auth/index.ts`
- `packages/chat-transport/src/index.ts` — add re-export
- `apps/desktop/main/deep-links.ts` — add OAuth callback dispatch
- `apps/desktop/main/deep-links.test.ts` — new
- `apps/desktop/main/transport-bridge.ts` — require injected transport
- `apps/desktop/main/index.ts` — pick transport at the seam
- `apps/desktop/main/ipc/handlers.test.ts` — drop the old "default MockTransport" assertion
- `apps/desktop/main/auth/audit-log.ts` + test — new (created here so the audit sink contract is testable today; Phase 5A's real OIDC/loopback/secret-storage code lands beside it and shares the sink)
- `apps/desktop/main/auth/index.ts` — barrel
- `docs/plan/desktop/phase-5/5A-followup-auth-wiring.md` — this document

Out of scope:

- `oidc-client.ts`, `loopback-server.ts`, `secret-storage.ts` (Phase 5A proper)
- Renderer SignInGate (Phase 5A proper)
- SSE re-auth on 401 (deferred — see Gap 3 notes)

## Gap 1 — deep-link → OAuth-callback dispatch (HIGH)

**Symptom**: `deep-links.ts` only logs deep links. With `ATLAS_AUTH_MODE=oidc`, the OIDC flow's `codePromise` never resolves because nobody hands `code`/`state` back to it.

**Fix**: `registerDeepLinks(options)` accepts an optional `onAuthCallback(code, state) => void`. When a deep link matches `enterprise://oauth/callback?code=...&state=...` the handler invokes the callback synchronously before logging. Both transport paths (macOS `open-url`, Windows `second-instance` argv tail) feed the same dispatcher.

**Primary vs fallback**: the primary path is deep-link → callback → resolve `codePromise`. The loopback HTTP server stays as a fallback for Windows variants where custom URL schemes aren't reliably registered. Phase 5A's `oidc-client.ts` registers both at the start of a flow and accepts whichever fires first; the loser is torn down by the same Promise.race.

**Test**: `deep-links.test.ts` builds a fake `app` (just `on`/`off`/`setAsDefaultProtocolClient`) and confirms:

- `enterprise://oauth/callback?code=C&state=S` invokes the callback with `("C", "S")` via the `open-url` path.
- The same URL passed as `second-instance` argv tail does the same.
- A deep link missing `code` does **not** call the callback (auth state machine never receives partial inputs).
- Non-auth deep links don't call the callback.
- A deep link that fails `parseDeepLink` is ignored.

## Gap 2 — `TransportBridge` requires an injected `Transport` (HIGH)

**Symptom**: `transport-bridge.ts` defaults to `new MockTransport()` if no transport is supplied. Dev → prod switching requires editing production code. Worse, a wiring bug that silently injects a fixture in production is invisible.

**Fix**:

- `TransportBridgeOptions` becomes `{ transport: Transport }` (required, non-optional).
- The constructor stores the supplied transport — no fallback.
- `apps/desktop/main/index.ts` picks the implementation at the seam:
  - When `process.env.ATLAS_FACADE_URL` is set, instantiate `WebTransport` (HTTP + SSE pump already exists in `packages/chat-transport/src/web/WebTransport.ts` — that **is** `FetchTransport`; no duplicate written) and wrap with `withBearerRefresh`.
  - Otherwise, instantiate `MockTransport`. Dev-only and explicit.
- The bridge no longer imports `MockTransport`.

**Test**: handlers' existing test rig already injects a `FakeTransport` via `{ transport }`. Drop the one test that asserted the implicit MockTransport default (it now asserts a contract we're removing). TypeScript enforces the required field — no runtime guard needed, no separate type-level test.

## Gap 3 — `withBearerRefresh` decorator (MEDIUM)

**Symptom**: bridge attaches a bearer but has no 401-retry path. Token expiry mid-request silently fails.

**Fix**: a pure decorator in `packages/chat-transport/src/auth/withBearerRefresh.ts`:

```ts
export interface BearerRefreshResult {
  readonly ok: boolean;
  readonly reason?: string;
}
export type BearerRefreshFn = (
  workspaceId: string,
) => Promise<BearerRefreshResult>;

export interface WithBearerRefreshOptions {
  readonly workspaceId: string;
  readonly refresh: BearerRefreshFn;
  readonly onRetry?: (req: TypedRequest) => void;
  readonly onRefreshFailure?: (reason: string) => void;
}

export function withBearerRefresh(
  inner: Transport,
  opts: WithBearerRefreshOptions,
): Transport;
```

The decorator wraps `request()`:

- On `UnauthorizedError` it calls `refresh(workspaceId)` **once** per request, then retries the original `TypedRequest` exactly once.
- If the retry also throws `UnauthorizedError`, it propagates — no infinite refresh loop.
- If `refresh()` resolves `{ ok: false, reason }`, the original 401 propagates and `onRefreshFailure` fires (so the renderer's SignInGate can re-prompt).
- `subscribeServerSentEvents`, `getSession`, `capabilities` are pass-through. **SSE re-auth on 401 is deferred to Phase 8** — documented here. Reason: SSE 401 in the middle of a stream is an active-session policy decision the agent runtime hasn't surfaced yet (we don't know whether to silently re-subscribe with bumped bearer or surface a cancel-with-reason). One-shot `request()` retries are unambiguous; SSE re-subscription is not.

**Tests** (`withBearerRefresh.test.ts`):

- 200 passes through unchanged.
- 401 → refresh succeeds → retry returns 200.
- 401 → refresh fails → original `UnauthorizedError` propagates with `onRefreshFailure` called once.
- 401 → refresh ok → retry also 401 → propagates (no second refresh, no infinite loop).
- A fresh request after a 401 starts a new refresh budget (per-request, not per-transport).
- `getSession` and `capabilities` are pass-through.
- `subscribeServerSentEvents` is pass-through to inner.

## Gap 4 — auth-event audit log (MEDIUM)

**Symptom**: only inactive-workspace gate violations are audited. Sign-in success, sign-out, token refresh, and retried-401 leave no trail.

**Fix**: `apps/desktop/main/auth/audit-log.ts` defines a discriminated-union `AuthAuditEvent` and an append-only JSON-Lines writer:

```ts
export type AuthAuditEvent =
  | {
      kind: "sign-in-success";
      workspaceId: string;
      sub: string;
      mode: "dev-mint" | "oidc";
    }
  | {
      kind: "sign-in-failure";
      workspaceId: string;
      mode: "dev-mint" | "oidc";
      reason: string;
    }
  | { kind: "sign-out"; workspaceId: string }
  | { kind: "token-refresh-success"; workspaceId: string }
  | { kind: "token-refresh-failure"; workspaceId: string; reason: string }
  | { kind: "unauthorized-retry"; workspaceId: string; path: string }
  | {
      kind: "secret-storage-gate-violation";
      claimedWorkspaceId: string;
      sessionWorkspaceId: string;
      serverKind: "backend" | "mcp" | "saas";
      serverId: string;
    };

export interface AuthAuditLog {
  append(event: AuthAuditEvent): Promise<void>;
  readAll(): Promise<readonly AuthAuditEntry[]>; // for tests + admin export
}

export function createFileAuthAuditLog(opts: {
  filePath: string;
  now?: () => Date;
}): AuthAuditLog;
```

Each line is `{"ts":"<iso>","event":{...}}` — single record per line, never rewritten in place. The existing `secret-storage-gate-violation` event is included in the union so Phase 5A's `secret-storage.ts` shares one sink (single source of truth, not parallel logs).

`withBearerRefresh` accepts an optional `onUnauthorizedRetry(path)` notify — `apps/desktop/main/index.ts` wires that to an `unauthorized-retry` audit entry. Sign-in/-out/refresh events are written by Phase 5A's `oidc-client.ts` at the moment the token store mutates (and **before** the renderer is notified, per the brief).

**Tests** (`audit-log.test.ts`):

- Every event kind round-trips through `append` → `readAll`.
- File is JSON-Lines: each event = one line + newline, valid `JSON.parse` per line.
- File is append-only: a second `append` does not rewrite earlier lines.
- Each entry carries an ISO timestamp from the injected clock.

## Done criteria

- [x] Sub-PRD committed first
- [ ] `npm test --workspace @0x-copilot/chat-transport` passes (existing 26 + new tests)
- [ ] `npm test --workspace @0x-copilot/desktop` passes (existing 36 + new tests)
- [ ] `npm test --workspace @0x-copilot/chat-surface` still passes (untouched)
- [ ] `npm run typecheck --workspace @0x-copilot/desktop` clean
- [ ] `npm run typecheck --workspace @0x-copilot/chat-transport` clean
- [ ] `npm run lint --workspace @0x-copilot/desktop` clean

## Coordination notes

- Phase 6C is editing `apps/desktop/main/index.ts` and `packages/chat-transport/src/ipc/rpc-protocol.ts` in parallel. Our changes are additive — index.ts gets a new pre-bridge block, and we don't touch rpc-protocol.ts. Where new lines are added in index.ts they're fenced with `// === phase-5-followup ... ===` markers so a 3-way merge resolves mechanically.
- The new `apps/desktop/main/auth/` directory is owned by Phase 5A long-term; this follow-up only seeds the `audit-log.ts` contract because Gap 4 needs it now. Phase 5A's `oidc-client.ts`, `loopback-server.ts`, `secret-storage.ts` slot in beside it without churn.
