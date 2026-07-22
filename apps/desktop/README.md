# @0x-copilot/desktop

[![ci-desktop](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-desktop.yml/badge.svg)](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-desktop.yml)

The 0xCopilot desktop client. It is an Electron app that runs the whole
0xCopilot stack on your own machine: it starts an embedded PostgreSQL database
and the three backend services locally, then shows the chat workspace in a
native window. Your runtime, data, and activity history stay on the device.

**If you just want to install and use 0xCopilot,** you don't need this
document — install it from your terminal with the
[`@0x-copilot/cli`](../../tools/cli/README.md) package (`npm install -g
@0x-copilot/cli`, then `copilot`). This README is a reference for developers
working on or building the desktop app itself.

## Layout

```
main/         Node — app lifecycle, BrowserWindow, app:// protocol, deep links
preload/      Node + sandboxed DOM — contextBridge.exposeInMainWorld('bridge', ...)
renderer/     Chromium — mounts <ChatShell /> from @0x-copilot/chat-surface
out/          esbuild output (main/, preload/, renderer/)
dist/         electron-builder output
```

## Renderer wiring (the app shell)

`renderer/bootstrap.tsx` composes the six-destination desktop shell. From the
outside in:

```
DeploymentProfileProvider  profile = "single_user_desktop" (team features gated off)
  BootGate                 supervised-boot progress screen (packaged/staged only)
    SignInGate             dev-mint / Google / wallet sign-in; bearer stays in main
      ChatShell            48px icon rail (6 destinations) + 46px topbar + rail-foot Settings/avatar
        DestinationOutlet  active-slug → real surface (Run cockpit or a list binder)
        SettingsMount      full-height Settings surface when settingsActive
      PaletteHost          the global ⌘K command palette + topbar trigger
```

- **`DeploymentProfileProvider`** seeds the static `single_user_desktop`
  profile (the value is not bridged from main; a future `team` desktop build
  can supply it through the same `DeploymentProfile` port). `destinationsForProfile`
  yields the six destinations — **Run · Chats · Projects · Activity ·
  Tools · Skills** — and `defaultDestinationForProfile` lands the app on
  **Run**.
- **`ChatShell`** owns the rail/topbar chrome and reads the slug↔label source
  of truth from `chat-surface`'s `destinations.ts`. The host owns navigation
  state (`activeDestination`) and the `onOpenSettings` rail-foot wiring;
  Settings is not a rail destination — it opens full height and suppresses the
  topbar/context/right-rail while active.
- **`DestinationOutlet`** (`renderer/DestinationOutlet.tsx`) maps the active
  slug to its surface: `run` → the `RunDestination` cockpit; `chats` /
  `projects` / `activity` / `connectors` (Tools) / `tools` (Skills) → the
  list surfaces from `@0x-copilot/chat-surface`, each fed by a desktop
  binder in **`renderer/destinationBinders.tsx`** that fetches over the shell's
  `Transport` port (no `apps/frontend` import — that is a hard boundary). Any
  unexpected slug falls back to the sanctioned `DestinationPlaceholder`, and
  the legacy `agents` / `inbox` slugs fold onto Activity.
- **`PaletteHost`** (`renderer/PaletteHost.tsx`) mounts exactly one
  `CommandPalette` over a **local static registry**
  (`renderer/palette-commands.ts` → `renderer/DesktopPaletteSearchPort.ts`, no
  network call): 6 navigation + 3 settings + 4 action entries. Navigation hits
  dispatch to the shell's `onNavigate(slug)`, settings hits open Settings at
  the target section, action hits launch the matching flow (New chat / Add
  provider key / Download local model / Connect tool). The palette is
  **controlled** by bootstrap (`open`/`onOpenChange`) so `⌘K` is single-sourced.
- **Keyboard shortcuts** come from `chat-surface`'s `useShellShortcuts`, driven
  by the `shell/shortcuts.ts` chord source of truth. Bootstrap wires the five
  **global** chords — `⌘N` new run, `⌘K` palette, `⌘,` Settings, `⌘⇧M`
  local-model picker, `⌘⇧F` search Activity — with the input guard that keeps
  single-letter chords from firing inside a composer (`⌘K` / `⌘,` stay exempt).
  The **run-scoped** chords (`⌘M`/`⌘←`/`⌘→`/`⌘L`/`⌘.`/`⌘↵`/`⌘⌫`) are owned by
  the Run cockpit's own listeners, live only while Run is active, and are
  deliberately left undefined at the shell level to avoid double-wiring.

## Running the renderer

```bash
# MockTransport (no backend): plain dev
npm run dev --workspace @0x-copilot/desktop

# WebTransport against a running facade (e.g. `make dev` from the repo root)
COPILOT_FACADE_URL=http://127.0.0.1:8200 npm run dev --workspace @0x-copilot/desktop
```

`COPILOT_FACADE_URL` selects `IpcTransport → WebTransport`; unset falls back
to `MockTransport`. This plain path does **not** engage the service supervisor
(no embedded Postgres) — for the REAL supervised app (embedded Postgres + all
three services + Electron shell) from source in one command, use
`make desktop-supervised` (see [Service supervisor](#service-supervisor-packaged--staged-runtime-boots) below).
The live end-to-end walkthrough (boot → run → palette → shortcuts → settings) is
[`SMOKE.md`](./SMOKE.md).

## Run cockpit states

`renderer/DestinationOutlet.tsx` mounts `RunDestination`
(`@0x-copilot/chat-surface`) for the `run` slug. The cockpit binds to a
conversation and resolves its runs over the Transport port, so it has two
states beyond the live run layout:

- **Empty / idle** — no active run for the conversation. Instead of a blank
  canvas, it shows an honest goal composer (`RunEmptyState`, "Give it a
  goal…"). Submitting a goal starts a run and binds it in place, so the live
  layout appears without remounting the shell.
- **Multi-run** — more than one run in the conversation. A run selector
  (`RunMultiSelect`, goal · status · time) lets you switch which run the
  cockpit shows; picking one rebinds the projection/timeline/surface. A
  conversation with zero or one run shows no selector chrome.

The desktop mounts the cockpit against a default conversation id
(`DESKTOP_DEFAULT_CONVERSATION_ID`); threading the _real_ active conversation
(Chats → reopen-into-Run) is still deferred, so reopen / open-run / run-skill
all land on the cockpit front door as an honest interim.

## Module system (read before adding source)

`package.json` deliberately **omits** `"type": "module"`. The build picks per-process:

- **main** and **preload** compile to **CommonJS** — Node-shaped, `__dirname` available, Electron's main-process loader resolves `@0x-copilot/*` workspace deps without an ESM/CJS interop dance.
- **renderer** compiles to **ESM** via an esbuild bundle — browser-shaped, React 19 + chat-surface + chat-transport + surface-renderers bundled into one `out/renderer/bootstrap.js`.

Two tsconfigs split the targets (`tsconfig.main.json` and `tsconfig.renderer.json`); `tsconfig.json` is the typecheck-only umbrella.

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
and `require('electron')` returns a path string instead of the API.

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

**Production posture is derived from the same signal.** `shouldSupervise` is the
authoritative production-posture input: a supervised local stack is always
production-configured (`main/services/service-env.ts` pins every child to
`*_ENVIRONMENT=production`, so the dev IdP `/v1/dev/identity/mint` route is never
registered there). `main/posture.ts#isProductionPosture` therefore returns true
whenever the app supervises (`app.isPackaged` **or** a staged
`COPILOT_RUNTIME_DIR`) OR `COPILOT_PRODUCTION=1` is set — unless an explicit dev
override (`COPILOT_DEV=1` / `COPILOT_AUTH_MODE=dev-mint`) forces dev-mint. This
keeps supervision and auth from diverging: the staged
`COPILOT_RUNTIME_DIR=… npm run dev` recipe below runs real sign-in
(`signInLocal` SIWE), not dev-mint, which would 404 against its own production
stack. `COPILOT_PRODUCTION=1` alone (no `COPILOT_RUNTIME_DIR`) means production
auth against an external facade with no local supervisor.

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
generated once (`main/services/boot-secrets.ts`) and persisted at
`<userData>/secrets/boot-env.bin`. By DEFAULT the blob is a chmod-600 JSON
file and the OS keychain is never touched, so a fresh install shows no macOS
keychain prompt. Settings → Key storage & app lock → "Protect secrets with
macOS Keychain" opts into safeStorage encryption
(`main/services/secure-storage-policy.ts`): the keychain prompt then fires at
toggle time, and again only after an upgrade re-signs the ad-hoc binaries.
The same policy gates the auth session store and the capability grant store
(their chmod-600 plaintext paths are sanctioned in file mode). Existing blobs
always load by their own marker — flipping the toggle migrates in place, and
legacy cipher blobs stay readable in file mode. An unreadable blob is a fatal
boot error — never silently regenerated, because the postgres password and
`ENTERPRISE_AUTH_SECRET` live there.

**On-disk locations** (all under `app.getPath("userData")`):
`secrets/boot-env.bin`, `pgdata/` (postgres cluster),
`logs/{backend,ai-backend,backend-facade}.log` (10 MB × 3 rotation),
`logs/postgres.log`.

**File-native AI store (default)** — the **ai-backend** runtime store is the
file-native JSONL store under `<userData>/agent-data/v1`
(`RUNTIME_STORE_BACKEND=file`; adapter provisions the tree `0o700`), and its
Postgres migration gate is skipped. `backend`'s own Postgres
(identity/OAuth/vault) is untouched. `COPILOT_DESKTOP_FILE_STORE_V1` is an
**override**, not an opt-in: a falsey value (`0`/`false`/`no`/`off`/`disabled`)
pins the legacy Postgres `atlas_ai` store (the rollback / escape hatch), a
truthy value forces file, and unset resolves to file. **Data continuity**: a
first file boot starts a **fresh** store — conversations already written to the
`atlas_ai` Postgres DB are preserved on disk but not shown until carried over
with `python -m runtime_adapters.migrate` (see
`docs/operations/desktop-file-store-migration.md`), or pin Postgres with
`COPILOT_DESKTOP_FILE_STORE_V1=0`. The file backend rides the in-process worker;
the `single_user_desktop` profile is what starts it.

**Crash policy**: children restart with 1s→2s→4s→…→30s backoff;
≥ 5 crashes in 5 minutes is a `FatalCrashLoop` surfaced on the boot
screen. The app holds a single-instance lock (two postmasters on one
`pgdata/` would corrupt it); second launches re-focus the first window.

**One command (build-from-source + run supervised).**
`make desktop-supervised` (or `node tools/desktop-runtime/run-supervised.mjs`) is
the single command that codifies the whole hand-assembly: it stages the host
runtime (idempotent — a warm re-run stamp-skips the pip installs and only
refreshes the cheap source copy; `--adhoc-sign` on macOS so unsigned arm64
mach-o binaries still execute), then builds and launches the Electron shell
against it with `COPILOT_RUNTIME_DIR` set, so the supervisor boots embedded
postgres + all three services in production posture. It is the from-source
dev-loop counterpart to the published `copilot` CLI, and the GUI counterpart to
`run-local.mjs` (which boots the same backend topology headlessly, no window).

```bash
make desktop-supervised
# equivalently:
node tools/desktop-runtime/run-supervised.mjs
# fast path once staged (only main/renderer changed — skips staging):
node tools/desktop-runtime/run-supervised.mjs --skip-stage
# or via make:  make desktop-supervised ARGS="--skip-stage"
```

**Which command when:**

| command                                          | postgres + 3 services                     | Electron GUI        | posture    |
| ------------------------------------------------ | ----------------------------------------- | ------------------- | ---------- |
| `make desktop-supervised` / `run-supervised.mjs` | yes (supervised)                          | yes                 | production |
| `npm run dev --workspace @0x-copilot/desktop`    | no (MockTransport / `COPILOT_FACADE_URL`) | yes                 | dev-mint   |
| `node tools/desktop-runtime/run-local.mjs`       | yes (supervised)                          | no (headless smoke) | production |

Manual equivalent of what `run-supervised.mjs` automates (stage once, then run):

```bash
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64 --adhoc-sign
COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" \
  npm run dev --workspace @0x-copilot/desktop
```

## Terminal distribution (the `copilot` CLI)

[`tools/cli`](../../tools/cli) publishes `@0x-copilot/cli`, which installs and
launches this app from the terminal:

```bash
npm install -g @0x-copilot/cli   # or: bun add -g @0x-copilot/cli
copilot                          # stages the runtime, then launches this app
```

It is a thin wrapper over the exact dev-run recipe above: it stages the runtime
with `tools/desktop-runtime/stage.mjs --adhoc-sign` into `~/.0xcopilot`, then
spawns `electron <appDir>` with `COPILOT_RUNTIME_DIR` pointed there — which flips
`shouldSupervise()` on, so the same supervisor path boots the runtime.
`main/updater.ts` auto-no-ops (unpackaged → `app.isPackaged` is false), so the
CLI channel simply updates via `npm i -g …@latest`. See
[tools/cli/README.md](../../tools/cli/README.md).

## Brand identity (name + icons)

macOS takes the Dock icon and hover name from the **.app bundle hosting the
process**, never from the JS runtime — so a launch that spawns the stock
Electron binary would present as "Electron" with the atom icon. Branding is
therefore applied per launch mode:

- **Assets** — `build/icon-source.svg` is the vector source (same mark as the
  website favicon); `node build/generate-icons.mjs` (macOS host) regenerates
  the committed `build/icon.png` / `icon.icns` / `icon.ico`. The compile step
  stages the png + icns into `out/main/` so they ship in the asar and the CLI
  payload.
- **Packaged installs** — `electron-builder.yml` (`productName`, `mac.icon`,
  `win.icon`) bakes the identity into the bundle; nothing to do at runtime.
- **`copilot` CLI launches** — `tools/cli/lib/mac-shell.mjs` clones
  Electron.app into `~/.0xcopilot/shell/0xCopilot.app` (APFS copy-on-write, so
  ~no extra disk), rewrites `CFBundleName`/`CFBundleDisplayName`/
  `CFBundleIdentifier`, swaps in `icon.icns`, ad-hoc re-signs the outer
  bundle, and launches that — the Dock shows the real name + icon. This is
  the rebrand procedure from Electron's own Application Distribution docs
  (what electron-packager automates), minus the helper-app renames: helpers
  never appear in the Dock, and leaving them untouched keeps their nested
  signatures valid without a deep re-sign — so Activity Monitor still lists
  "Electron Helper" child processes. Cosmetic only.
- **Plain `npm run dev`** — `main/branding.ts` sets the app name and the
  runtime **dock icon**; the Dock _tooltip_ still reads "Electron" here (it
  comes from node_modules' Electron.app Info.plist, which we don't mutate).
  Launch through `copilot` from the repo checkout if that matters.

`main/branding.ts#APP_ID`, `electron-builder.yml#appId`, and the shell's
`CFBundleIdentifier` must stay identical (`com.0x-copilot.app`) so
notifications and Windows taskbar grouping attribute to one app.

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

## Summary of what's implemented

The renderer ships the full six-destination shell (see **Renderer wiring**
above): the profile-gated rail, the Run cockpit mounted through the real
`DestinationOutlet`, the list surfaces, the Settings surface (BYOK + local
models + approval policy, team sections gated off), the `⌘K` command palette,
and the keyboard shortcuts. Sign-in (dev-mint / Google / wallet), secret
storage, the service supervisor, packaging, signing, and auto-update are all
wired (see the sections above). The live end-to-end walkthrough is
[`SMOKE.md`](./SMOKE.md). The bootstrap renders under `<StrictMode>`
(`renderer/bootstrap.tsx`).
