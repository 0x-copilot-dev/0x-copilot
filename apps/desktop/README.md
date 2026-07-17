# @enterprise-search/desktop

Atlas Electron desktop client. See
[docs/plan/desktop/PRD.md](../../docs/plan/desktop/PRD.md) for the master
plan and
[docs/plan/desktop/phase-1/1A-electron-shell.md](../../docs/plan/desktop/phase-1/1A-electron-shell.md)
for the Phase 1-A shell scope.

## Layout

```
main/         Node — app lifecycle, BrowserWindow, app:// protocol, deep links
preload/      Node + sandboxed DOM — contextBridge.exposeInMainWorld('bridge', ...)
renderer/     Chromium — mounts <ChatShell /> from @enterprise-search/chat-surface
out/          esbuild output (main/, preload/, renderer/)
dist/         electron-builder output (Phase 8 expands)
```

## Module system (read before adding source)

`package.json` deliberately **omits** `"type": "module"`. The build picks per-process:

- **main** and **preload** compile to **CommonJS** — Node-shaped, `__dirname` available, Electron's main-process loader resolves `@enterprise-search/*` workspace deps without an ESM/CJS interop dance.
- **renderer** compiles to **ESM** via an esbuild bundle — browser-shaped, React 19 + chat-surface + chat-transport + surface-renderers bundled into one `out/renderer/bootstrap.js`.

Two tsconfigs split the targets (`tsconfig.main.json` and `tsconfig.renderer.json`); `tsconfig.json` is the typecheck-only umbrella. This was validated in the Phase S spike — see [docs/plan/desktop/phase-0.5/S2-decision.md](../../docs/plan/desktop/phase-0.5/S2-decision.md).

## Scripts

```bash
npm run typecheck --workspace @enterprise-search/desktop
npm run lint --workspace @enterprise-search/desktop
npm run test --workspace @enterprise-search/desktop
npm run build --workspace @enterprise-search/desktop
npm run dev --workspace @enterprise-search/desktop     # launches the GUI
```

`dev` prefixes the child command with `ELECTRON_RUN_AS_NODE=` (empty,
which unsets it) because CI / agent harnesses sometimes set
`ELECTRON_RUN_AS_NODE=1`, in which case Electron behaves as plain Node
and `require('electron')` returns a path string instead of the API (S2
decision report friction note 1).

## Sign-in

The renderer's `SignInGate` offers three paths, all IPC → main (bearer
tokens never cross the IPC boundary):

- **Sign in** — `auth.sign-in`: dev-mint (default) or direct-IdP OIDC
  depending on `ATLAS_AUTH_MODE` (see `main/index.ts#buildAuthService`).
- **Continue with Google** — `auth.sign-in-google`: the facade-brokered
  flow from `main/auth/google-login.ts`. Main binds an ephemeral loopback
  server (random port, EADDRINUSE retry), calls
  `GET {facade}/v1/auth/oidc/google/start?redirect_uri=<loopback>&format=json`,
  opens the returned `auth_url` in the system browser
  (`shell.openExternal`), receives Google's redirect on the loopback, and
  exchanges `state`+`code` at `GET {facade}/v1/auth/oidc/callback` for the
  JSON bearer handoff. PKCE verifier + nonce are held server-side, bound
  to the single-use 10-minute `state`. Requires `GOOGLE_OAUTH_CLIENT_ID`
  on the backend process (and the loopback URI authorized on the Google
  OAuth client).
- **Connect wallet** — `auth.sign-in-wallet`: Sign-In-With-Ethereum via
  the facade-served standalone wallet page (`main/auth/wallet-login.ts`).
  Main mints a random `state`, binds an ephemeral loopback server armed
  with it, and opens
  `{facade}/wallet.html?handoff=http://127.0.0.1:<port>/wallet/cb?state=<state>`
  in the system browser. The page drives the wallet (EIP-6963 pick →
  `personal_sign` → `POST /v1/auth/siwe/nonce` / `/verify`) and redirects
  back to the loopback with the bearer handoff in the query (same field
  names as the OIDC callback); the loopback rejects any redirect whose
  `state` does not round-trip. No desktop-side facade hop besides the
  best-effort `GET /v1/me/profile` claims enrichment.

Shared hardening for both system-browser flows: sessions persist via
`SecretStorage` (safeStorage); sign-in success/failure lands in the auth
audit log (`<userData>/audit/auth.log`, modes `google` / `wallet`); a
second sign-in click — either button — cancels any pending flow and
replaces the stored session; loopback redirect waits time out (5 min
default); sessions that come back `requires_mfa: true` are refused — the
desktop has no MFA challenge surface yet.

The facade base URL comes from `ATLAS_FACADE_URL` (default
`http://127.0.0.1:8200`), the same source the transport bridge uses.

## Manual sanity check after launching

In DevTools console:

```js
fetch("https://example.com");
```

This must fail. The renderer's CSP is delivered per response by the
`app://` privileged protocol handler (`apps/desktop/main/app-protocol.ts`)
and includes `connect-src 'none'`. If a network request succeeds, the
CSP is not being applied — investigate the protocol handler first.

## Phase 1-A status

Phase 1-A ships the substrate only: one `BrowserWindow`, hardened
WebPreferences, the `app://` protocol with strict per-response CSP, a
preload bridge whose `ipc.invoke` / `ipc.on` throw "not yet wired" (Phase
1-C populates the channel allowlist), and a renderer that mounts
`<ChatShell />` against `MockTransport` and local stubs for `Router` /
`KeyValueStore` / `PresenceSignal`. OIDC, secret storage, auto-update,
signing, and crash-report uploading are Phase 5 / Phase 8.

The bootstrap deliberately does NOT use `<StrictMode>` — see PRD
§S2-decision friction note 5 and the sub-PRD Open question 4. Re-enable
once the EmailRenderer's `hasMounted` guard is fixed in Phase 4-a.
