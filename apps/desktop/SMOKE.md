# Atlas Desktop — Phase 5 smoke test

Manual end-to-end recipe. Phase 8 will automate this through a Spectron /
Playwright harness; for Phase 5 the contract is "a human can walk this
without surprises". If a step doesn't behave as written, file a bug
referencing the step number — the renderer / main split is intentional and
breakage usually points at a single seam.

## Prerequisites

- Backend stack running locally via `make dev` from the repo root.
  - `backend:8100`, `ai-backend:8000`, `backend-facade:8200`, `frontend:5173`.
- `BACKEND_ENVIRONMENT=development` set on the backend so the dev IdP at
  `POST /v1/dev/identity/mint` is registered.
- `ENTERPRISE_AUTH_SECRET` set (same secret the facade verifies with).

## Launch

```bash
cd apps/desktop
npm run build
ATLAS_AUTH_MODE=dev-mint \
  ATLAS_FACADE_URL=http://127.0.0.1:8200 \
  ATLAS_DEV_PERSONA=sarah_acme \
  npm run dev
```

`ATLAS_AUTH_MODE=oidc` would route through the real authorization-code
flow instead — Phase 5 leaves the production OIDC provider unresolved
(PRD R3), so `dev-mint` is the local mode.

## Steps

1. **Launch**
   - Electron window opens with the Atlas chrome.
   - First-launch state: the `<SignInGate>` renders, showing "Sign in to
     your workspace to use Atlas." with a single CTA button.
   - On macOS/Windows the first launch creates `{userData}/secrets/` —
     no `.bin` files yet because no session is stored.

2. **Sign in via system browser** (dev-mint route)
   - Click `Sign in`.
   - The CTA UI changes to "Opening browser…" while the IPC `auth.sign-in`
     is in flight.
   - In `dev-mint` mode the main process POSTs to
     `http://127.0.0.1:8200/v1/dev/identity/mint`. No browser opens —
     dev-mint is a header-less HMAC mint. The full OIDC flow (system
     browser + loopback redirect) only runs in `ATLAS_AUTH_MODE=oidc`.
   - Once the response lands, `<SignInGate>` swaps to render `<ChatShell>`.

3. **See chats**
   - The chat-surface ChatsDestination renders the persona's conversation
     list. The `LocalStorageKeyValueStore` (production renderer
     persistence) caches the pinned list across reloads.

4. **Open a thread**
   - Click a conversation in the sidebar.
   - The hash router updates the URL to `#/conversation/<id>` and the
     thread canvas renders.

5. **Send a message and watch it stream**
   - Type a prompt and submit.
   - The renderer IPC-invokes `transport.request` for the POST and
     subscribes to the run's SSE stream via `transport.subscribe`. Stream
     events flow back through `transport.stream-event` and into the
     thread canvas as tokens.

6. **Approve a diff in a tier-1 renderer**
   - In a thread that surfaces a tier-1 (EmailRenderer / SheetRenderer)
     pending diff, the inline-diff state machine renders the action chip.
   - Click `Approve`. The renderer transitions to `approved`; the
     swimlane bead updates colour (`pending → approved`).

7. **Open a thread with an unknown SaaS scheme**
   - Navigate to a hash like `#/sf-opp/some-opp-id` (any scheme not in the
     tier-1 set). The Phase 4 fallback (`registerAdapter` registry resolves
     to `null`) takes over and the tier-3 `TcSurfaceMount` renders the raw
     JSON payload safely — confirming the system degrades gracefully on
     never-before-seen surfaces.

8. **Sign out (housekeeping)**
   - Open the renderer console and call:
     ```js
     window.bridge.ipc.invoke("auth.sign-out", { workspaceId: "org_acme" });
     ```
   - The on-disk `{userData}/secrets/org_acme/backend/<server-hash>.bin`
     is removed. Subsequent reload returns the user to the sign-in gate.

## What to verify

- **Bearer never reaches renderer state.** Open DevTools and inspect
  `window.bridge` and React component state. The `RendererSession` view
  has `workspaceId`, `expiresAt`, `displayName`, `email` — no bearer.

- **D24 / PRD §6.7 on-disk shape.** The secrets directory layout is
  `{userData}/secrets/{workspace_id}/{server_kind}/{server_id_hash}.bin`.
  Cat one of the `.bin` files: the first bytes are `ATLASv1:cipher:` (or
  `ATLASv1:plaintext:` only in the dev fallback path when
  `safeStorage.isEncryptionAvailable()` is false). The plaintext bearer
  must not appear anywhere in the file under `cipher:`.

- **Active-workspace gate (PRD §6.7).** Programmatically attempting to
  read another workspace's secret while the session is bound to the first
  workspace returns null and emits a `[secret-storage] active-workspace
gate rejected read` warning (see `audit-service.test.ts`).

- **Loopback OIDC** (only when `ATLAS_AUTH_MODE=oidc`): the auth URL
  carries `code_challenge_method=S256`, `state`, and a redirect_uri
  pointing at `http://127.0.0.1:<random>/cb`. After the IdP redirects,
  the loopback server returns a "Signed in." page and the main process
  exchanges the code for tokens; the loopback server then closes itself.

## Platform notes

- **macOS**: `safeStorage.encryptString` is backed by a Keychain item
  named after `app.getName()` (= `Atlas`). The keychain shows a single
  entry for the app — not one per workspace/server (per D24, the
  per-tuple compartmentalization is at the file layer, not the keychain
  layer). The first `safeStorage` call prompts the user if the keychain
  is locked.
- **Windows**: `safeStorage` uses DPAPI scoped to the current user
  profile. No prompt; the ciphertext is bound to the Windows logon
  session.
- **Linux**: `safeStorage.isEncryptionAvailable()` returns false unless a
  Secret Service provider (gnome-keyring / kwallet) is running. In dev
  the app falls back to a plaintext-prefixed `.bin` and logs a loud
  one-shot warning. Production refuses to start (PRD §6.7).

## Out of scope for this smoke

- Real OIDC provider integration (PRD R3 — choose before prod).
- Tier-2 sandbox + codegen pipeline (Phase 6).
- Crash-reporter endpoint wiring (Phase 8).
- Code-signing, auto-update, telemetry first-launch consent (Phase 8).
- Full e2e automation harness (Phase 8 owns the rewrite of this doc into
  a Playwright spec).
