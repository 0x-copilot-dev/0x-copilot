# @0x-copilot/desktop

0xCopilot Electron desktop client. See
[docs/plan/desktop/PRD.md](../../docs/plan/desktop/PRD.md) for the master
plan and
[docs/plan/desktop/phase-1/1A-electron-shell.md](../../docs/plan/desktop/phase-1/1A-electron-shell.md)
for the Phase 1-A shell scope.

## Layout

```
main/         Node — app lifecycle, BrowserWindow, app:// protocol, deep links
preload/      Node + sandboxed DOM — contextBridge.exposeInMainWorld('bridge', ...)
renderer/     Chromium — mounts <ChatShell /> from @0x-copilot/chat-surface
out/          esbuild output (main/, preload/, renderer/)
dist/         electron-builder output (Phase 8 expands)
```

## Module system (read before adding source)

`package.json` deliberately **omits** `"type": "module"`. The build picks per-process:

- **main** and **preload** compile to **CommonJS** — Node-shaped, `__dirname` available, Electron's main-process loader resolves `@0x-copilot/*` workspace deps without an ESM/CJS interop dance.
- **renderer** compiles to **ESM** via an esbuild bundle — browser-shaped, React 19 + chat-surface + chat-transport + surface-renderers bundled into one `out/renderer/bootstrap.js`.

Two tsconfigs split the targets (`tsconfig.main.json` and `tsconfig.renderer.json`); `tsconfig.json` is the typecheck-only umbrella. This was validated in the Phase S spike — see [docs/plan/desktop/phase-0.5/S2-decision.md](../../docs/plan/desktop/phase-0.5/S2-decision.md).

## Scripts

```bash
npm run typecheck --workspace @0x-copilot/desktop
npm run lint --workspace @0x-copilot/desktop
npm run test --workspace @0x-copilot/desktop
npm run build --workspace @0x-copilot/desktop
npm run dev --workspace @0x-copilot/desktop     # launches the GUI
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
  depending on `COPILOT_AUTH_MODE` (see `main/index.ts#buildAuthService`).
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

The facade base URL comes from `COPILOT_FACADE_URL` (default
`http://127.0.0.1:8200`), the same source the transport bridge uses.

## Service supervisor (packaged / staged-runtime boots)

`main/services/` owns the packaged-app boot: it starts an embedded
postgres plus the three python services and only hands the renderer a
transport once the facade is healthy.

**When it runs.** Supervision engages iff `app.isPackaged` OR
`COPILOT_RUNTIME_DIR` is set (`main/services/boot-mode.ts#shouldSupervise`).
Plain `npm run dev` is unchanged: no supervisor, `COPILOT_FACADE_URL`
selects WebTransport (MockTransport otherwise).

**Runtime layout the supervisor expects** — this is EXACTLY what
`tools/desktop-runtime/stage.mjs` produces and what the proven
`tools/desktop-runtime/run-local.mjs` boots. `resolveRuntimePaths()` roots
the tree at `<base>/runtime/<platform>-<arch>`, where `<base>` is
`process.resourcesPath` (packaged) or `COPILOT_RUNTIME_DIR` (dev, point it at
`apps/desktop/resources`). electron-builder `extraResources` maps
`apps/desktop/resources/runtime` → `<resourcesPath>/runtime`:

```
runtime/<platform>-<arch>/          # e.g. darwin-arm64, win32-x64
  python/bin/python3                # unix; symlink -> python3.13
  python/python.exe                 # windows; at the python/ root, not bin/
  postgres/bin/{initdb,pg_ctl}      # zonky bundle ships ONLY these two + `postgres`;
                                    # NO psql / pg_isready / createdb
  services/{backend,ai-backend,backend-facade}/
    src/  site-packages/  scripts/ (migrate.py for backend + ai-backend)  migrations/
  staging-manifest.json
```

**Boot order** (`main/services/supervisor.ts`): secrets → free-port
allocation (4 ports, OS-assigned) → postgres (initdb once with
`--encoding=UTF8 --locale=C -U atlas --pwfile --auth=scram-sha-256`, stale
`postmaster.pid` cleanup, `pg_ctl -w -t 60 start` on 127.0.0.1 — `-w`
blocks until the server accepts connections, so no separate `pg_isready`
gate is needed or available; databases `atlas_backend` + `atlas_ai` are
created with the staged python + psycopg because the bundle has no
psql/createdb) → `scripts/migrate.py apply` per stateful service (yoyo runs
against the `postgresql+psycopg://` migrate URL — the bare scheme resolves
to the absent psycopg2 driver) → all three uvicorn children in parallel →
health gate (`/v1/health`) backend + ai-backend first, then facade → ready.
Progress streams to the renderer on the allowlisted `boot.status` channel
(`BootStatusPayloadSchema`); the renderer's `BootGate` shows a progress
screen until `phase: "ready"` and a terminal error screen on
`fatal: true`. `before-quit` awaits `supervisor.stop()` (facade →
ai-backend → backend → `pg_ctl stop -m fast`).

**Secrets** — `ENTERPRISE_AUTH_SECRET`, `ENTERPRISE_SERVICE_TOKEN`,
`MCP_TOKEN_VAULT_SECRET`, the postgres password, and `AUDIT_HMAC_KEY` — are
generated once (`main/services/boot-secrets.ts`) and persisted encrypted via
safeStorage at `<userData>/secrets/boot-env.bin` (chmod-600 plaintext JSON
fallback when safeStorage is unavailable). An unreadable blob is a fatal boot
error — never silently regenerated, because the postgres password and
`ENTERPRISE_AUTH_SECRET` live there.

**On-disk locations** (all under `app.getPath("userData")`):
`secrets/boot-env.bin`, `pgdata/` (postgres cluster),
`logs/{backend,ai-backend,backend-facade}.log` (10 MB × 3 rotation),
`logs/postgres.log`.

**File-native AI store (opt-in)** — setting `COPILOT_DESKTOP_FILE_STORE_V1`
truthy (`1`/`true`/`yes`/`on`/`enabled`) switches only the **ai-backend**
runtime store from the Postgres `atlas_ai` DB to the file-native JSONL store
under `<userData>/agent-data/v1` (`RUNTIME_STORE_BACKEND=file`; adapter
provisions the tree `0o700`), and its Postgres migration gate is skipped.
`backend`'s own Postgres (identity/OAuth/vault) is untouched. **Opt-in pending a
Postgres→file migration**: it does not exist yet, so enabling the flag starts a
**fresh** store — conversations already in Postgres are not visible under the
file store until a migration is built. Default (flag unset/false) is
byte-identical to the Postgres store.

**Crash policy**: children restart with 1s→2s→4s→…→30s backoff;
≥ 5 crashes in 5 minutes is a `FatalCrashLoop` surfaced on the boot
screen. The app holds a single-instance lock (two postmasters on one
`pgdata/` would corrupt it); second launches re-focus the first window.

Dev-run recipe against a staged runtime:

```bash
COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" \
  npm run dev --workspace @0x-copilot/desktop
```

## Terminal distribution (the `copilot` CLI)

[`tools/cli`](../../tools/cli) publishes `@0x-copilot/cli`, which installs and
launches this app from the terminal with **no DMG/installer and no signing
credentials**:

```bash
npm install -g @0x-copilot/cli   # or: bun add -g @0x-copilot/cli
copilot                          # stages the runtime, then launches this app
```

It is a thin wrapper over the exact dev-run recipe above: it stages the runtime
with `tools/desktop-runtime/stage.mjs --adhoc-sign` (credential-free ad-hoc
signing so unsigned binaries run on Apple Silicon) into `~/.0xcopilot`, then
spawns `electron <appDir>` with `COPILOT_RUNTIME_DIR` pointed there — which flips
`shouldSupervise()` on, so the same supervisor path boots the runtime. Because
the app runs as a spawned process (not a distributed `.app`/`.exe`) and
npm/curl-staged files never carry the quarantine / Mark-of-the-Web marker,
Gatekeeper/SmartScreen never gate it. `main/updater.ts` auto-no-ops (unpackaged
→ `app.isPackaged` is false), so the CLI channel simply updates via
`npm i -g …@latest`. See [tools/cli/README.md](../../tools/cli/README.md).

## Packaging, signing & auto-update

Installers are built by `electron-builder` (`electron-builder.yml`). The
`dist:*` npm scripts run the full handshake — **stage the runtime → compile →
package** — for one platform/arch and never publish:

```bash
# from apps/desktop/ (run each on its native host — the staged python
# site-packages are host-specific; a win build must run on Windows):
npm run dist:mac:arm64     # -> dist/0xCopilot-<v>-arm64.dmg + -arm64-mac.zip
npm run dist:mac:x64       # -> dist/0xCopilot-<v>-x64.dmg  + -x64-mac.zip
npm run dist:win           # -> dist/0xCopilot Setup <v>.exe (nsis, per-user)
```

`stage:runtime*` runs `tools/desktop-runtime/stage.mjs` into
`apps/desktop/resources/runtime/<platform>-<arch>/`; `extraResources` then maps
`resources/runtime` → `<resourcesPath>/runtime`. Both `resources/` (staged
binaries) and `dist/` are gitignored.

**Signing (all via env; unset ⇒ unsigned local build that still runs):**

- macOS code signing: `CSC_LINK` (base64 .p12 or path) + `CSC_KEY_PASSWORD`.
  Set `CSC_IDENTITY_AUTO_DISCOVERY=false` to force an unsigned build.
  `build/sign-nested.js` (afterPack) pre-signs the bundled python/postgres
  Mach-O binaries with the hardened runtime BEFORE electron-builder signs the
  `.app`; it no-ops cleanly when no identity is configured.
- macOS notarization is auto-gated: it runs only when signing succeeds AND
  `APPLE_API_KEY` + `APPLE_API_KEY_ID` + `APPLE_API_ISSUER` are in the env.
- Windows signing: `CSC_LINK` + `CSC_KEY_PASSWORD` (same vars).

**Auto-update** (`main/updater.ts`, electron-updater against the
`0x-copilot-dev/0x-copilot` GitHub Releases feed): checks on ready + every 4h,
downloads in the background, and installs **only on quit** so migrations never
run under the old version. It is a hard no-op unless the build is packaged AND
carries `app-update.yml` (i.e. signed release builds); lifecycle is surfaced to
the renderer on the allowlisted `update.status` channel. CI publishes with
`electron-builder --publish always`; the `dist:*` scripts pass
`--publish never`.

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
