# Phase 5.A: auth-integration

## Vision

Phase 1 wired a stub bearer through the IPC bridge so the chat surface could
render against `MockTransport`. The renderer constructs an `IpcTransport`
with `{ bearer: null }` and the main-side `TransportBridge` makes outbound
requests with no auth. That was deliberate: the bearer/secret-storage seam
was always meant to be exactly one place — main — and Phase 1's stub kept
the seam in place without taking on OIDC complexity.

Phase 5 fills that seam in. The principles, applied to the actual primitives
(not by analogy to other Electron apps):

1. **Single source of truth for "who is signed in".** The token never lives
   in renderer state. The renderer asks main "is there a session for this
   workspace?" and renders a sign-in CTA when the answer is no. Once main
   has a session, every outbound HTTP request the renderer triggers through
   the IpcTransport gets the bearer attached **inside main** before the
   network call. The Transport contract's `getSession(): Session` accessor
   stays substrate-honest: web returns the bearer string it holds in memory
   (because that's where it lives in a browser); IpcTransport returns a
   snapshot of the bearer fetched from main's secret-storage at sign-in
   time, never refreshed in the renderer.

2. **D24 strictly.** The secret-storage decision was reasoned from first
   principles in PRD §6.7. Per-`(workspace_id, server)` ciphertext files
   gated by an active-workspace check: smallest unit of retrievability,
   smallest blast radius on a decrypt-then-leak, simplest revocation
   (delete a directory).

3. **DRY between dev and prod.** The PRD §9 R3 risk note says the OIDC
   provider identity is unresolved. We defer that choice but split the
   client into two modes selected by env: `ATLAS_AUTH_MODE=oidc` runs the
   real authorization-code-with-PKCE flow; `ATLAS_AUTH_MODE=dev-mint` POSTs
   to `/v1/dev/identity/mint` against the facade and reuses the bearer
   verification path. Both modes return the same `Session` envelope to the
   renderer — the renderer cannot tell the difference. This keeps the
   downstream code path single-source-of-truth.

4. **Stubs disappear at the producer boundary, not the consumer boundary.**
   Phase 1-A's `StubRouter` / `MemoryKeyValueStore` / `StubPresenceSignal`
   were placeholders for the real chat-surface exports
   `HashRouter`, `LocalStorageKeyValueStore`, `DocumentPresenceSignal`.
   The chat-surface package was always going to be the substrate-honest
   home for the web-flavored implementations (Chromium renderer = web for
   storage and visibility purposes). Phase 5 swaps the three stubs for
   imports from chat-surface. Three lines of bootstrap, three deletions.

## Status

- Status: in-progress
- Agent slug: `auth-integration`
- Branch: `desktop/phase-5-auth-integration`
- Worktree: `.claude/worktrees/agent-a8eea8c8ac8c8f708`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-5/5A-auth-integration.md` — this file.
- `apps/desktop/main/auth/oidc-client.ts` — NEW. OIDC authorization-code +
  PKCE flow with a dev-mint fallback. Initiates auth, drives the loopback
  server, exchanges the code, refreshes when the bearer is near expiry.
- `apps/desktop/main/auth/loopback-server.ts` — NEW. Ephemeral
  `http.createServer` listener on `127.0.0.1:RANDOM_PORT` that catches the
  redirect, returns a single static "you can close this window" page, and
  resolves a Promise with the `(code, state)` pair.
- `apps/desktop/main/auth/secret-storage.ts` — NEW. Per-`(workspace_id,
server)` ciphertext files under `{userData}/secrets/{workspace_id}/
{server_kind}/{server_id}.bin`. Backed by Electron `safeStorage` with a
  plaintext-fallback **only** when `BACKEND_ENVIRONMENT=development` and
  `safeStorage.isEncryptionAvailable()` is false (loud warning logged once
  at app start). Active-workspace gate enforced on every read.
- `apps/desktop/main/auth/index.ts` — NEW. Composes the OIDC client +
  secret storage + IPC channel handlers; exposes a typed `AuthService`
  consumed by `main/index.ts` and `TransportBridge`.
- `apps/desktop/main/auth/*.test.ts` — NEW. Unit tests covering: session
  round-trip, active-workspace gate (mismatched workspace rejected with no
  decrypt), on-disk ciphertext does not contain the plaintext token,
  loopback server returns the code, PKCE verifier is generated with crypto
  random + S256-hashed correctly.
- `apps/desktop/main/transport-bridge.ts` — MODIFIED. Accept an optional
  `bearerProvider` so the bridge attaches `Authorization: Bearer …` from
  the auth service on every outbound request. Default keeps the
  MockTransport for tests with no provider, so existing tests are
  unaffected.
- `apps/desktop/main/ipc/handlers.ts` — MODIFIED. Register four new IPC
  channels: `auth.signIn`, `auth.signOut`, `auth.refresh`,
  `auth.getSession`.
- `packages/chat-transport/src/ipc/rpc-protocol.ts` — MODIFIED. Add the
  four `auth.*` channel names + Zod payload schemas to the allowlist.
  Renderer + main read from the same constants — there is no other source.
- `apps/desktop/main/index.ts` — MODIFIED. Construct the auth service at
  app-ready and pass its `bearerProvider` into the TransportBridge.
- `apps/desktop/renderer/bootstrap.tsx` — MODIFIED. Replace three stubs
  with the chat-surface exports. Re-enable `<StrictMode>`. Mount the new
  `<SignInGate>` around `<ChatShell>`.
- `apps/desktop/renderer/SignInGate.tsx` — NEW. Reads the current session
  for the active workspace; renders a sign-in CTA when null, the chat
  shell once signed in.
- `apps/desktop/renderer/StubRouter.ts` — DELETED.
- `apps/desktop/renderer/MemoryKeyValueStore.ts` — DELETED.
- `apps/desktop/renderer/StubPresenceSignal.ts` — DELETED.
- `apps/desktop/SMOKE.md` — NEW. Manual end-to-end smoke recipe.

**Out of scope** (Phase 6+):

- Real OIDC provider selection (PRD R3). The OIDC mode is wired to a
  configurable issuer URL but no provider is hardcoded; production deploy
  picks one before this code ships.
- Tier-2 sandbox + codegen pipeline.
- Crash-reporter wiring to a real endpoint.
- Automated e2e harness (Phase 8 owns it).

## Required artifacts

1. `oidc-client.ts` exports `OidcClient` with:
   - `signIn(workspaceId): Promise<Session>` — drives the full PKCE flow
     in `oidc` mode, or POSTs `/v1/dev/identity/mint` in `dev-mint` mode.
   - `refresh(workspaceId, session): Promise<Session>` — exchanges the
     refresh token, or re-mints in `dev-mint` mode.
   - Pure transport semantics: takes a `fetch` and a `shellOpenExternal`
     callback at construction so it's testable without Electron.

2. `loopback-server.ts` exports `awaitLoopbackCode({ state }): Promise<{ port, codePromise, close }>`:
   - Resolves to the actual bound port (so the OIDC client can construct
     the redirect URI before the auth URL is built).
   - `codePromise` resolves to `{ code }` when the redirect lands, rejects
     on state-mismatch or timeout.
   - `close()` is idempotent.

3. `secret-storage.ts` exports `SecretStorage` with the PRD §6.7 API
   shape:
   - `get(workspace_id, server_kind, server_id): Promise<unknown | null>`
   - `set(workspace_id, server_kind, server_id, payload): Promise<void>`
   - `delete(workspace_id, server_kind, server_id): Promise<void>`
   - `deleteWorkspaceSecrets(workspace_id): Promise<void>`
   - `setActiveWorkspace(workspaceId): void`
   - `getActiveWorkspace(): WorkspaceId | null`
   - Active-workspace gate: reads against a non-active workspace return
     null **without attempting decryption**, and an audit-log entry is
     written. The gate is documented in code with a comment that cites
     PRD §6.7 — the only place comments are mandatory in this codebase.

4. `<SignInGate>` (renderer):
   - On mount, IPC-invokes `auth.getSession` for the default workspace
     (read from the chat-surface `LocalStorageKeyValueStore`; defaults to
     `wsp_acme` so first-launch users land in the persona store).
   - If null, renders a sign-in CTA. Clicking it IPC-invokes
     `auth.signIn` and stores the returned session in component state.
   - Once a session is present, renders `<ChatShell>` parametrised with
     the new session-aware IpcTransport.

## Open questions

- The OIDC provider is unresolved (PRD R3). The `oidc` code path here is
  structurally complete (PKCE, state, code exchange, refresh) but doesn't
  hardcode the issuer — it reads `ATLAS_OIDC_ISSUER`,
  `ATLAS_OIDC_CLIENT_ID`, `ATLAS_OIDC_SCOPES`. Production deploy supplies
  these. The dev-mint mode covers all dev flows.

- The `workspace_id` claim isn't yet on dev-mint bearers (the W0.1 dev IdP
  payload has `org_id` and `user_id` only). The active-workspace gate
  reads the claim's `org_id` and treats it as the workspace identifier
  for D24 purposes during dev. Production OIDC ID-tokens will carry a
  real `workspace_id`; the gate code reads whichever name is present.

- Phase 5 leaves `crash-reporter` un-pointed (Phase 8 owns wiring to a
  real endpoint). The stub remains.

## Smoke test (verbatim PRD §5 Phase 5)

> launch → log in via system browser → see chats → open thread → send →
> stream response → approve a diff in a tier-1 renderer → see swimlane
> bead update → open a thread with an unknown SaaS scheme → see tier-3
> render the same payload

Recipe lives in `apps/desktop/SMOKE.md`. Phase 8 will automate it.
