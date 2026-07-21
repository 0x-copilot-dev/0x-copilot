---
id: desktop-app
title: Desktop App (Electron)
kind: cluster
paths: [apps/desktop]
loc: 21600
languages: [typescript, c]
---

# Cluster: Desktop App (Electron)

## Purpose

`apps/desktop` (`@0x-copilot/desktop`) is the Electron client that turns the three-service web product into a fully local, single-user desktop app. Its main process is a **supervisor**: on a packaged (or `COPILOT_RUNTIME_DIR`-staged) launch it boots an embedded PostgreSQL plus the three Python services (`backend`, `ai-backend`, `backend-facade`) from a bundled runtime, generates and keychain-persists all boot secrets, runs migrations, health-gates the children, and only then wires the renderer to the local facade (`apps/desktop/main/index.ts:262-296`, `main/services/desktop-supervisor.ts:52`). In plain `npm run dev` it skips supervision and runs against `MockTransport` or `COPILOT_FACADE_URL` (`main/index.ts:291-296`).

The renderer deliberately owns almost nothing: it mounts the shared `ChatShell` from `@0x-copilot/chat-surface` (the SSOT interaction layer both web and desktop use) and binds each destination surface through desktop-native binders that fetch exclusively over an IPC-proxied `Transport` (`renderer/bootstrap.tsx:115-127`, `renderer/destinationBinders.tsx`). The renderer's CSP is `connect-src 'none'` (`main/app-protocol.ts:15-26`) — every HTTP/SSE byte flows through Electron main's `TransportBridge`, and bearer tokens never cross the IPC boundary (`main/transport-bridge.ts:22-28`, `packages/chat-transport/src/ipc/rpc-protocol.ts:15-18`).

Around that core the app carries four security-sensitive subsystems: (1) a real end-user **auth stack** (Google OAuth via system browser + loopback, SIWE wallet login via the facade-served wallet page, and a production-safe "use locally" per-install SIWE key), with dev/production **posture** resolution that fixes the earlier "CLI launch mints the Sarah Chen dev persona" bug (`main/posture.ts`, `tools/cli/lib/launch.mjs:69`); (2) the **AC5 capability subsystem** — user folder grants, an authenticated loopback FS broker, and hardened path validation; (3) the **AC8 agentic-browser** subsystem (Playwright in a supervised child behind an egress-policy proxy) — built and tested but currently *not wired into main at all*; and (4) the **Phase-6 tier-2 adapter pipeline** (AST allowlist, vm sandbox, quality gate, install/broken lifecycle) — wired but fed by a stub event source pending Phase 7.

## Public Interface

### Renderer IPC surface (the app's principal internal contract)

All channels are allowlisted in the sandboxed preload (`preload/bridge.ts:16-22`); invoke handlers are registered in `main/ipc/handlers.ts:140` with Zod validation on every inbound payload and strict-parse on sensitive outbound views.

| Channel | Direction | Purpose | Evidence |
|---|---|---|---|
| `transport.request` | R→M invoke | Proxy one typed HTTP request to the facade | `main/ipc/handlers.ts:144` |
| `transport.subscribe` / `transport.unsubscribe` | R→M invoke | Open/close an SSE subscription by id | `main/ipc/handlers.ts:153,179` |
| `transport.session-snapshot` | R→M invoke | Session + transport capabilities snapshot | `main/ipc/handlers.ts:192` |
| `transport.stream-event` | M→R push | SSE open/message/error/closed fan-out | `main/index.ts:330-338` |
| `auth.get-session`, `auth.sign-in`, `auth.sign-in-google`, `auth.sign-in-wallet`, `auth.sign-out`, `auth.refresh` | R→M invoke | Auth lifecycle; only renderer-safe session views cross (no bearer) | `main/ipc/handlers.ts:206-267` |
| `auth.get-posture` | R→M invoke | Production/dev posture flag (currently no renderer consumer — see F8) | `main/ipc/handlers.ts:215` |
| `tier2.install` / `tier2.uninstall` / `tier2.mark-broken` | M→R push | Adapter source install/uninstall/demote into chat-surface registry | `renderer/Tier2Bridge.ts:134-138` |
| `tier2.boundary-error` | R→M invoke | Live render-boundary trip → mark-broken pipeline | `main/ipc/handlers.ts:272`, `main/index.ts:376-388` |
| `boot.status` / `update.status` | M→R push (stateful, replayed) | Supervisor boot phases; electron-updater lifecycle | `preload/bridge.ts:30-45`, `main/index.ts:129-139` |
| `capability.request-folder-grant` / `capability.list-grants` / `capability.revoke-grant` | R→M invoke | Folder-grant UX; only `RendererGrant` (no host path) returns | `main/capabilities/channels.ts:15-22`, `main/ipc/handlers.ts:286-326` |
| `connector.list-catalog` / `connector.connect` | R→M invoke | Reconciled connector catalog + system-browser OAuth connect | `main/connectors/channels.ts:3-8`, `main/ipc/handlers.ts:328-357` |

### Loopback HTTP servers (main-owned, localhost-only)

- **Capability broker** `/v1/{handshake,grants/list,grants/snapshot,runs/begin,runs/end,fs/stat,fs/list,fs/read,fs/glob,fs/grep,fs/write,fs/edit,fs/mkdir,fs/delete,fs/move}` — per-boot 256-bit bearer, constant-time compare, protocol header, POST+JSON only; intended consumer is the ai-backend worker's `broker_client.py` (`main/capabilities/broker.ts:75-90`; `services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py:1-40`). Gated behind `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` (`main/capabilities/feature-gate.ts:14`).
- **Browser broker** (AC8, unwired) — same hardening plus aud/nonce/expiry replay protection (`main/browser/browser-broker.ts:1-12`).
- **Ephemeral auth loopbacks** — per-sign-in `127.0.0.1` servers receiving the OAuth code (Google) or the session handoff (wallet) (`main/auth/loopback-server.ts:118-227`).

### OS-level surfaces

- Custom app scheme `app://app` serving the built renderer with CSP headers (`main/app-protocol.ts:52-119`).
- Deep-link scheme `enterprise://oauth/callback`, demuxed by 256-bit state between connector flows and (unwired) app-login (`main/deep-links.ts:85-137`).
- electron-updater against the GitHub Releases feed, install-on-quit only (`main/updater.ts`, `electron-builder.yml:81-86`).

### Env-var contract (read at boot)

`COPILOT_RUNTIME_DIR`, `COPILOT_FACADE_URL`, `COPILOT_PRODUCTION`, `COPILOT_DEV`, `COPILOT_AUTH_MODE`, `COPILOT_DEV_PERSONA`, `COPILOT_OIDC_*`, `COPILOT_WORKSPACE_ID` (`main/index.ts:494-529,590`, `main/posture.ts:24-28`, `main/services/boot-mode.ts`); `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` (`main/capabilities/feature-gate.ts:14`); `RUNTIME_ENABLE_DESKTOP_BROWSER` (`main/browser/feature-gate.ts:10`, unwired); `COPILOT_DESKTOP_FILE_STORE_V1` (`main/services/service-env.ts:87`). Child services receive a **curated env** built from scratch — passthrough allowlist is only PATH/HOME-class vars + `GOOGLE_OAUTH_CLIENT_ID/SECRET` + provider API keys (`main/services/service-env.ts:11-36`); everything else (DB URLs, `ENTERPRISE_*` secrets, `SIWE_ORIGIN`, `FACADE_WEB_DIST_DIR`, store backend selection) is set explicitly per service (`main/services/service-env.ts:143-248`).

## Internal Structure

| module/group | files | ~LOC | responsibility |
|---|---|---|---|
| main boot & composition | `main/index.ts`, `main/window.ts`, `main/app-protocol.ts`, `main/branding.ts`, `main/posture.ts`, `main/deep-links.ts`, `main/updater.ts`, `main/crash-reporter.ts` | ~1750 | App lifecycle, single-instance lock, `app://` protocol + CSP, brand identity, dev/prod posture, deep links, auto-update, composition of every subsystem |
| service supervision | `main/services/{supervisor,desktop-supervisor,postgres,python-service,service-env,boot-secrets,migrations,health,ports,exec,rotating-log,runtime-paths,boot-mode,google-oauth-default}.ts` | ~1950 | Pure boot state machine (`supervisor.ts`) + OS-facing composition (`desktop-supervisor.ts`); embedded Postgres lifecycle; uvicorn child babysitting w/ crash-loop detection; per-service curated env; keychain boot secrets; migration gate; health polling; bundled Google OAuth client seeding |
| auth stack | `main/auth/{index,oidc-client,google-login,wallet-login,local-login,loopback-server,secret-storage,audit-log,profile-claims}.ts` | ~1990 | `AuthService` facade: dev-mint/OIDC client, Google system-browser+loopback flow, SIWE wallet-page flow, production-safe local-key SIWE flow, keychain session storage, JSONL auth audit trail, fail-closed session probe |
| IPC & transport | `main/ipc/{handlers,schemas}.ts`, `main/transport-bridge.ts`, `preload/{bridge,window-bridge-types}.ts` | ~650 | Channel registration + Zod validation both directions; IPC↔Transport bridge with subscription lifecycle; sandboxed preload allowlist with stateful-channel replay |
| capabilities (AC5) | `main/capabilities/{index,service,broker,host-fs,grant-store,path-validation,run-context,folder-picker,types,schemas,channels,feature-gate}.ts` | ~3560 | Folder grants: native picker (main owns paths), safeStorage-encrypted grant store, authenticated loopback FS broker, two-gate path validation (pure syntax + symlink/TOCTOU), per-run grant snapshots |
| agentic browser (AC8) | `main/browser/*` (16 src files incl. `worker/index.ts`) | ~3450 | Playwright-in-child browser capability: supervised worker, hardened broker, deny-by-default egress policy + anti-rebinding CONNECT proxy, profile isolation, staging, MCP tool schemas. **Not imported by `main/index.ts`; no build task emits the worker bundle** (see F1) |
| tier-2 adapters (Phase 6) | `main/adapters/{lifecycle,registry-host,integrate,sandbox,ast-allowlist,smoke-render-executor,tier2-installer,lifecycle-events,types}.ts`, `main/adapters/quality-gate/*` | ~1900 | Install pipeline: schema validation → AST allowlist scan → vm-sandbox compile → smoke render → renderer register; append-only lifecycle audit log; 3-attempt retry budget; boundary-error → mark-broken |
| tier-2 dormant modules | `main/adapters/{harvest,download,loader,opt-out}.ts` | ~660 | Community harvest/promoted-download/reload-from-disk/opt-out — **no non-test consumers** (F5) |
| connectors (AC9) | `main/connectors/{connector-service,oauth-coordinator,schemas,channels}.ts` | ~430 | Reconciled catalog via facade; system-browser OAuth connect with loopback+deep-link race, state-keyed demux |
| renderer shell | `renderer/{bootstrap.tsx,SignInGate.tsx,BootProgress.tsx,DestinationOutlet.tsx,SettingsMount.tsx,PaletteHost.tsx,palette-commands.ts,DesktopPaletteSearchPort.ts,Tier2Bridge.ts}` + css | ~2600 | Mounts shared `ChatShell`; boot gate; sign-in gate (wallet/Google/local); Settings surface w/ every section bound; ⌘K palette (static command registry + fuzzy port); tier-2 renderer bridge |
| renderer binders | `renderer/destinationBinders.tsx` | ~690 | Desktop-native destination binders (Chats/Activity/Connectors/Skills/Projects/Run) — fetch via IPC transport, re-implement web binder projections (F3) |
| renderer composer | `renderer/composer/*` (7 files) | ~890 | Shared `AssistantComposer` bound to desktop ports: file picker, attachment adapter, `/`-menu, hardcoded model catalog (F4), run dispatch |
| native addon | `native/workspace-fs/{workspace_fs.c,index.cjs,index.d.ts,binding.gyp}` | ~510 | Optional C addon (openat/O_NOFOLLOW-style race-free FS ops) loaded best-effort by `host-fs.ts`; JS fallback when absent |
| build & config | `esbuild.config.mjs`, `electron-builder.yml`, `eslint.config.mjs`, `package.json`, tsconfigs, `vitest.*`, `build/*` | ~600 | Three esbuild bundles (main CJS / preload CJS / renderer ESM); packaging + extraResources runtime mapping; eslint bans `apps/*` sibling imports and renderer `fetch`/`electron` |

Architecture is consistently **ports-and-fakes**: every OS touchpoint (fs, spawn, net, dialogs, safeStorage, clocks) is an injected dependency, with exactly two composition roots that touch the real OS (`main/index.ts`, `main/services/desktop-supervisor.ts:48-52`). Security invariants are structural, not aspirational: host paths never cross renderer IPC (strict outbound Zod parse, `main/ipc/handlers.ts:123-125`), bearers never cross IPC (attached in main per outbound request), the renderer cannot fetch (CSP + eslint ban), children get a from-scratch env.

## Dependencies

### Outbound

| target | kind | what | evidence |
|---|---|---|---|
| backend-facade | http | All `/v1/*` product calls via `WebTransport` (auth attach + 401 refresh-retry in main) | `main/index.ts:573-622` |
| backend-facade | sse | Run event streams proxied to renderer subscriptions | `main/transport-bridge.ts:55-86` |
| backend-facade | http | Auth flows: `/v1/auth/oidc/google/{start,callback}`, `/v1/auth/siwe/{nonce,verify}`, `/wallet.html`, `/v1/me/profile` probe | `main/auth/google-login.ts:85,123`, `main/auth/local-login.ts:119,150`, `main/auth/wallet-login.ts:21`, `main/auth/index.ts:367` |
| backend-facade / backend-product / ai-runtime-api | spawn+env | Supervisor spawns the three uvicorn children with curated env (`backend_app.desktop_app`, `runtime_api.app`, `backend_facade.app`) and polls `/v1/health` | `main/services/desktop-supervisor.ts:133-160`, `main/services/service-env.ts:38-42`, `main/services/health.ts` |
| external:postgres | spawn | Embedded postgres: initdb, `pg_ctl -w start`, createdb via staged python+psycopg | `main/services/postgres.ts` |
| shared-packages | import/contract | `@0x-copilot/chat-transport` (CHANNELS, Zod schemas, `IpcTransport`, `WebTransport`, `MockTransport`), `@0x-copilot/api-types` (wire types, adapter allowlist JSON), `@0x-copilot/surface-renderers`, `@0x-copilot/design-system` | `main/ipc/schemas.ts:4-37`, `renderer/bootstrap.tsx:1-33`, `main/adapters/ast-allowlist.ts:1-8` |
| chat-surface-core | import | `ChatShell`, `useTransport`, ports (`HashRouter`, KV store), `Tier2Loader`, registry | `renderer/bootstrap.tsx:15-31`, `renderer/Tier2Bridge.ts:3-11` |
| chat-surface-destinations | import | `ChatsArchive`, `ActivityDestination`, `ConnectorsDestination`, `SkillsDestination`, `ProjectsDestination`, `RunDestination`, `SettingsSurface` | `renderer/destinationBinders.tsx:22-34`, `renderer/SettingsMount.tsx:41` |
| external:github | http | electron-updater release feed (packaged+signed only) | `main/updater.ts`, `electron-builder.yml:83` |
| external:google-oauth | http (via system browser) | Google authorization endpoint reached by `shell.openExternal(auth_url)` | `main/auth/google-login.ts:9-16` |
| external:ethereum | import | `viem/accounts.generatePrivateKey` for the local-identity key; SIWE signing in `local-login.ts` | `main/auth/index.ts:1`, `main/auth/local-login.ts` |
| desktop-distribution | env/build | `tools/cli/lib/launch.mjs` sets `COPILOT_PRODUCTION=1` consumed by posture; `tools/desktop-runtime/stage.mjs` stages the runtime tree `runtime-paths.ts` resolves; icns consumed by CLI mac shell | `main/posture.ts:10-12`, `main/services/runtime-paths.ts:1-7`, `esbuild.config.mjs:61-68` |
| build-deploy | build | electron-builder packaging, GH-release publish config | `electron-builder.yml`, `package.json` scripts |

### Inbound

- **ai-runtime-capabilities / ai-runtime-worker** → capability broker over HTTP: `broker_client.py` implements the exact `/v1/fs/*` + `/v1/runs/*` wire contract and expects `DESKTOP_BROKER_URL`/`DESKTOP_BROKER_TOKEN` (`services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_backend.py:123-124`) — **the supervisor never sets these** (F2). Similarly `DesktopBrowserMcpProvider` expects `DESKTOP_BROWSER_BROKER_URL/TOKEN` (`services/ai-backend/src/runtime_worker/dependencies.py:334-335`) — never set, subsystem unwired (F1).
- **desktop-distribution (tools/cli)** spawns Electron against this app dir and reuses `out/main/icon.icns` (`tools/cli/lib/launch.mjs`, `tools/cli/lib/mac-shell.mjs:20`).
- **Renderer** consumes main exclusively over the IPC surface above; no other component imports this app (verified: no `apps/desktop` imports elsewhere).

## Data Owned

All under `app.getPath("userData")` unless noted:

- `secrets/boot-env.bin` — safeStorage-encrypted (or plaintext-fallback, F7) boot secrets: `ENTERPRISE_AUTH_SECRET`, `ENTERPRISE_SERVICE_TOKEN`, `MCP_TOKEN_VAULT_SECRET`, pg password, `AUDIT_HMAC_KEY` (`main/services/boot-secrets.ts:44-46,66`).
- `pgdata/` — embedded Postgres cluster (`atlas_backend`, `atlas_ai` databases) (`main/services/desktop-supervisor.ts:98`, `main/services/service-env.ts:44-45`).
- `agent-data/v1/` — opt-in file-native AI store root (JSONL folders), provisioned by the ai-backend adapter (`main/services/service-env.ts:92-117`).
- Auth session + local-identity key blobs via `SecretStorage` (`ATLASv1:` markers) (`main/auth/secret-storage.ts:29-30`); active-workspace pointer file.
- `capabilities/grants.bin` — encrypted grant collection (`ATLASCAPv1:` markers) (`main/capabilities/grant-store.ts:13-15`).
- `audit/auth.log`, `audit/adapter-lifecycle.log` — append-only JSONL audit trails (`main/index.ts:309-311,343-350`).
- `logs/{postgres,backend,ai-backend,backend-facade}.log` — rotating 10MB×3 (`main/services/rotating-log.ts`).
- `adapters/` — installed tier-2 adapter sources (`main/index.ts:342`).
- Renderer `localStorage` (via `LocalStorageKeyValueStore`) — shell UI prefs only.

## Key Flows

1. **Supervised boot** — `app.whenReady` → brand/dock/protocol/deep-links → create window (BootProgress renders immediately; latest status replayed on `did-finish-load`) → `shouldSupervise` → seed bundled Google OAuth client → `ServiceSupervisor.start()` phases: secrets → ports (4 ephemeral) → postgres (initdb/start/ensureDatabase) → migrations (backend + ai-backend via `scripts/migrate.py`, skipped for ai when file-store on) → spawn 3 uvicorn children (parallel) → health gate (backend+ai first, then facade) → `ready` → `wireTransportAndIpc(facadeUrl)` (`main/index.ts:209-304`, `main/services/supervisor.ts:115-194`). Shutdown reverses: facade→ai→backend→postgres with `before-quit` preventDefault (`main/index.ts:418-441`).
2. **Renderer request/stream path** — renderer `IpcTransport` → `transport.request` invoke → Zod parse → `TransportBridge.request` → `withBearerRefresh(WebTransport)` (sync cached bearer; on 401 refresh once + audit) → facade. SSE: `transport.subscribe` registers synchronously; events fan out on `transport.stream-event` keyed by subscriptionId (`main/ipc/handlers.ts:144-202`, `main/index.ts:573-622`).
3. **Sign-in (3 modes)** — SignInGate invokes `auth.sign-in-wallet|google|sign-in`. Google: loopback bind → facade `/start` → system browser → loopback code → facade `/callback` JSON handoff. Wallet: loopback bind → browser at facade `wallet.html?handoff=127.0.0.1:<port>` → page does SIWE + posts nonce/verify itself → redirects session handoff to loopback. Local (production posture): keychain per-install key → in-process SIWE nonce/sign/verify. Newest click cancels the pending browser flow; success persists via SecretStorage + audit row; `getSession` on boot probes `/v1/me/profile` and evicts a 401/403 session (fail closed) (`main/auth/index.ts:134-377`).
4. **Tier-2 adapter lifecycle (partially live)** — `startTier2Lifecycle` with `StubLifecycleEventSource` (install path dormant until Phase 7 wires run-stream `adapter_generated` events, `main/index.ts:105-119,340`). Live path: renderer `Tier2Loader` failure → `tier2.boundary-error` invoke → `markBrokenFromBoundary` → registry demote + `tier2.mark-broken` push + append-only audit (`main/index.ts:376-388`, `renderer/Tier2Bridge.ts:50-63`).
5. **Connector connect (AC9)** — renderer `connector.connect{slug}` → `ConnectorService` → `ConnectorOAuthCoordinator`: loopback + `enterprise://` deep-link race keyed by 256-bit state → system browser → callback → backend token exchange via facade → safe metadata back to renderer → refetch catalog (`main/connectors/oauth-coordinator.ts`, `main/index.ts:317-328`, `renderer/destinationBinders.tsx:431-451`).

## Test Posture

Exemplary breadth for an Electron app: **71 colocated vitest files, ~16.3k test LOC vs ~21.6k src LOC**, all driven through injected fakes (no live Electron, no live network). Security-critical modules have the deepest suites: `host-fs.test.ts` (1000), `broker.test.ts` (893), `handlers.test.ts` (782), `auth-service.test.ts` (647), `path-validation.test.ts`, `browser-session.test.ts`, plus posture, boot-secrets, service-env, supervisor state machine, postgres, python-service crash loops, SignInGate/bootstrap/SettingsMount renderer tests.

Gaps: `renderer/destinationBinders.tsx` (691 LOC, all five destination projections + Run readiness probing) has **no test** while the equivalent web projections do (`apps/frontend/src/features/activity/ActivityRoute.test.tsx`); `renderer/composer/desktopModelCatalog.ts`, `main/transport-bridge.ts` (covered only indirectly via handlers tests), and the two composition roots (`main/index.ts`, `main/services/desktop-supervisor.ts`) are untested — acceptable for roots, but `desktop-supervisor.ts` embeds real decisions (ai-migration skip under file store) that are only covered via `service-env.test.ts` adjacents. The dormant modules (browser subsystem, harvest/download/loader) are test-only reachable — their green suites can mask the fact that nothing ships them.

## Health Assessment

**Strengths.** This is one of the healthiest clusters in the repo. The security architecture is genuinely layered and structural: sandboxed renderer with `connect-src 'none'`, allowlisted preload, Zod on both IPC directions with strict outbound parsing so a leaked host path/token fails closed, bearers confined to main, curated child envs, keychain-backed secrets with refuse-to-regenerate semantics (`BootSecretsUnreadable`), fail-closed feature gates, and a fail-closed boot-session probe that fixed the phantom-persona bug properly (posture derives from `isPackaged || COPILOT_PRODUCTION`, dev-mint is unreachable in production posture, and stale dev sessions are evicted on boot — `main/posture.ts`, `main/auth/index.ts:332-354`). Ports-and-fakes discipline is uniform, and the boot orchestrator is a clean, fully tested state machine.

**Weaknesses.** The cluster carries a large amount of **built-but-unshipped surface**: the entire AC8 browser subsystem (~3.4k src LOC) is not imported by main and its worker has no build target, yet its own docs claim main wires it; the AC5 capability broker starts but its token/URL never reach the intended consumer, so the flagship host-filesystem feature cannot work end-to-end; the tier-2 install pipeline idles on a stub event source; and four adapter modules are consumer-less. Meanwhile pure projection logic and the model catalog are hand-duplicated against the web app, and three near-identical encrypted-blob/env-flag implementations exist. None of this is rot — it is scaffolding running ahead of integration — but the gap between what the code claims (comments, README, AI-backend counterparts) and what actually executes is now the cluster's main risk: a reader (or compliance reviewer) auditing "the desktop has an egress-policied browser and grant-scoped FS access" would be wrong on both counts today.

**Verdict:** structurally excellent, security-serious, over-provisioned. Priority is integration/deletion decisions on the dormant subsystems and de-duplicating the shared projections, not rework.

## Findings

F1. **[dead-code | high | high]** AC8 agentic-browser subsystem (~3.4k src LOC, 16 modules) is built, tested and documented but wired nowhere — `main/index.ts` never imports `main/browser`, no esbuild task emits the claimed `out/browser-worker/index.js` (`esbuild.config.mjs:10-53` has only main/preload/renderer), and the supervisor never sets the `DESKTOP_BROWSER_BROKER_URL/TOKEN` env the ai-backend's `DesktopBrowserMcpProvider` requires (`services/ai-backend/src/runtime_worker/dependencies.py:334-335`). `main/browser/index.ts:3-5` falsely states "main/index.ts builds the subsystem ONLY when isDesktopBrowserEnabled(process.env) is true". Evidence: `apps/desktop/main/browser/index.ts`, `apps/desktop/esbuild.config.mjs`, `apps/desktop/main/index.ts`. Suggestion: either land the wiring slice (build task + main gate + env delivery) or move the subtree to a clearly-marked incubating area and fix the docstring; today it's 3.4k LOC of maintained-but-unreachable security code.

F2. **[risk | high | high]** AC5 capability broker's token/URL are never delivered to the ai-backend child, so host-folder grants cannot function end-to-end in the supervised app: `startCapabilitySubsystem` mints the per-boot token (`main/index.ts:166-207`) but `buildServiceEnv` sets no `DESKTOP_BROKER_URL`/`DESKTOP_BROKER_TOKEN` (`main/services/service-env.ts:143-248`) and the passthrough allowlist strips them (`service-env.ts:11-36`), while `workspace_backend.py:123-124` waits for exactly those vars. The renderer grants UI, picker and broker all run — against no consumer. Evidence: `apps/desktop/main/services/service-env.ts`, `apps/desktop/main/index.ts`, `services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_backend.py`. Suggestion: complete the "slice 2 wiring" (`main/index.ts:167-170` admits it's pending) — supervisor consumes the broker handle and injects url+token into the ai-backend child env; until then gate the renderer grant UI off too, so users can't mint grants nothing honors.

F3. **[duplication | medium | high]** Destination projections are hand-duplicated between web and desktop: `bucketConversations`/`chatStatus`/`toArchiveRow` (`apps/desktop/renderer/destinationBinders.tsx:125-177` vs `apps/frontend/src/features/chats/api/chatsApi.ts:70+`) and `mapRunStatus`/`auditLabel`/`buildMetaIndex`/`projectActivityRows` (`destinationBinders.tsx:215-298` vs `apps/frontend/src/features/activity/api/activityApi.ts:56-147`). The desktop file acknowledges the duplication as intentional (`destinationBinders.tsx:16-18`), but these are pure functions over `@0x-copilot/api-types` shapes with zero substrate dependency — exactly what `chat-surface` (or an api-types-adjacent projections module) exists to single-source; the desktop copies are also untested while the web ones are tested. Evidence: paths above. Suggestion: lift the projections next to their consuming components in `packages/chat-surface` and have both hosts import them.

F4. **[ssot-violation | medium | high]** Curated cloud-model catalog is hardcoded again in the desktop composer (`apps/desktop/renderer/composer/desktopModelCatalog.ts:30-61`) mirroring the web `ChatScreen`'s separate hardcoded list (`apps/frontend/src/features/chat/ChatScreen.tsx:2561-2600`) — one more instance of the known "7+ divergent model lists" problem; the lists already disagree (web has `gpt-5.4-nano`, desktop doesn't). Evidence: paths above. Suggestion: serve one catalog from the facade (`/v1/models`-style, per the file's own OQ4 note) or at minimum a single shared constant in `api-types`.

F5. **[dead-code | medium | high]** Tier-2 dormant modules: `main/adapters/harvest.ts` (296), `download.ts` (236), `loader.ts` (56), `opt-out.ts` (71) have no non-test consumers (only each other + tests); additionally the wired lifecycle runs on `StubLifecycleEventSource` so the install path receives no events until Phase 7 (`main/index.ts:105-119,340`). Evidence: `apps/desktop/main/adapters/harvest.ts`, `download.ts`, `loader.ts`, `opt-out.ts`, `main/index.ts`. Suggestion: delete or park harvest/download/loader/opt-out until the registry backend exists; keep the wired pipeline (boundary-error path is live).

F6. **[inconsistency | medium | high]** Boot-secrets plaintext fallback is unconditional: when safeStorage is unavailable, `persist()` writes `ENTERPRISE_AUTH_SECRET`, pg password, vault secret and audit HMAC key as chmod-600 plaintext with no `allowPlaintextFallback` gate and no warning (`main/services/boot-secrets.ts:103-116`), whereas the sibling stores fail closed without the explicit dev flag (`main/auth/secret-storage.ts:154-169`, `main/capabilities/grant-store.ts:191-217`). Deliberate for headless Linux, but silently divergent from the app's own convention and from what a compliance read of SecretStorage would infer. Evidence: paths above. Suggestion: emit a visible degraded-security signal (log + boot status) and align the three stores on one policy knob.

F7. **[bespoke-replaceable | low-medium | high]** Triplicated hand-rolled primitives: (a) safeStorage-encrypted marker-prefixed blob envelope implemented three times with different markers (`ATLASv1:` `main/auth/secret-storage.ts:29-30`, `ATLASBOOTv1:` `main/services/boot-secrets.ts:44-45`, `ATLASCAPv1:` `main/capabilities/grant-store.ts:13-14`); (b) truthy-env-flag parser three times (`main/capabilities/feature-gate.ts:16`, `main/browser/feature-gate.ts:12`, `main/services/service-env.ts:89`); (c) the AC8 browser broker re-implements the AC5 broker's transport hardening (~300 LOC, acknowledged mirror `main/browser/browser-broker.ts:4-7`). Suggestion: one `encryptedBlobStore` helper + one `isFlagEnabled` helper; extract the shared loopback-broker base if AC8 ships.

F8. **[dead-code | low | high]** `auth.get-posture` IPC channel is registered and handled (`main/ipc/handlers.ts:215-218`, `main/index.ts:372-374`) but no renderer production code invokes it — `SignInGate.tsx` always offers wallet/Google/local; the channel docstring's claim that SignInGate uses it to hide dev-mint (`packages/chat-transport/src/ipc/rpc-protocol.ts:20-22`) is stale (behavior is safe because main routes local sign-in by posture, `main/index.ts:548-551`). Likewise `registerDeepLinks`' `onOAuthCallback` app-login branch is never wired — main passes only `connectorCallbackRouter` (`main/index.ts:219-222`), so a non-connector `enterprise://oauth/callback` logs a warning and drops (`main/deep-links.ts:118-130`); app login is loopback-only in practice, contradicting `deep-links.ts:59-63`. Suggestion: consume posture in SignInGate (label the local option) or drop the channel; update both docstrings.

F9. **[ssot-violation | low | medium]** `main/browser/tool-schemas.ts` hand-authors JSON Schemas that mirror the Zod schemas in `main/browser/protocol.ts` (acknowledged at `tool-schemas.ts:3-6`) — drift is only prevented by discipline; only relevant if AC8 ships. Suggestion: generate at build time (zod-to-json-schema as a devDependency, output checked in).

F10. **[refactor | low | medium]** Minor consolidation targets: `main/index.ts` is a 622-line composition root with the `allowPlaintext` expression computed twice (`main/index.ts:173-175` vs `498-500`); `RunBinder` and `RunComposer` independently probe `/v1/settings/provider-keys` + `/v1/local-models` per mount (`renderer/destinationBinders.tsx:576-616`, `renderer/composer/RunComposer.tsx:150-185`) — one shared readiness hook would halve the probes and keep the two gates consistent.
