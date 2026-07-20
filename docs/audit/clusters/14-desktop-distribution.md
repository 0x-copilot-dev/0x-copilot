---
id: desktop-distribution
title: Desktop Distribution — CLI, staging, smoke harness
kind: cluster
paths: [tools/cli, tools/cli-testing, tools/desktop-runtime]
loc: 3600
languages: [javascript]
---

# Cluster: Desktop Distribution — CLI, staging, smoke harness

## Purpose

This cluster is the entire "no DMG, no installer" distribution pipeline for the 0xCopilot desktop app. `tools/cli` is the published npm package `@0x-copilot/cli` (bin: `copilot` / `0xcopilot`): it stages a self-contained runtime (pinned CPython 3.13 + PostgreSQL 17 + the three Python services) into the user's home directory, ad-hoc code-signs it on macOS so it runs on Apple Silicon without Apple Developer credentials, and launches the Electron app with `COPILOT_RUNTIME_DIR` + `COPILOT_PRODUCTION=1` so the app's supervisor boots the stack in production posture. It also carries the full local lifecycle: `install`, `doctor`, `repair` (non-destructive recovery), `uninstall` (stops leaked processes, then deletes state).

`tools/desktop-runtime` is the staging engine the CLI (and the electron-builder release workflow) both drive: `manifest.json` pins sha256-verified binary inputs (python-build-standalone, zonky embedded-postgres), `stage.mjs` downloads/verifies/extracts them, pip-installs each service's pinned requirements into per-service `site-packages` (`--require-hashes` for backend/facade), stages the built frontend dist as the SIWE wallet page, prunes, byte-compiles, and optionally ad-hoc signs every Mach-O. `run-local.mjs` boots the staged tree end-to-end without Electron and smoke-tests it — it is the executable specification of the boot contract the Electron supervisor implements.

`tools/cli-testing` is the Playwright-Electron live-smoke harness born from the "unit fakes hid a real-run breakage" incident: `driver.mjs` launches the real app exactly the way `copilot start` does, intercepts `shell.openExternal`, and exposes an HTTP `/rpc` control server for step-wise driving (click/screenshot/DOM-dump); `siwe-session.mjs` completes a real SIWE login against the live facade and feeds the app's loopback, producing a signed-in GUI for surface testing. The FIX-PLAN/FIX-VERIFICATION docs record a completed remediation cycle (PRs #87/#88/#90/#91) driven by this harness.

Zero non-builtin Node dependencies in the CLI and stager (electron is the CLI's only runtime dep; playwright + viem live only in the private harness). The cluster's role in the system is build/distribution glue: it owns no product data, but it owns the *contracts* by which the desktop app finds and boots its runtime.

## Public Interface

**CLI commands** (`tools/cli/bin/copilot.mjs:5-11`, dispatch at `:189-215`):

- `copilot` / `copilot start` — stage if needed + launch (`bin/copilot.mjs:83-135`)
- `copilot install` — force re-stage, no launch (`bin/copilot.mjs:137-149`)
- `copilot doctor` — install diagnosis, exit 0/1 (`lib/doctor.mjs:46`)
- `copilot repair [--session]` — reclaim orphaned postgres / clear sessions (`lib/repair.mjs:128`)
- `copilot uninstall [--yes]` — stop our processes, delete state (`lib/uninstall.mjs:122`)
- `copilot help | version`, flags `--force/-f`, `--yes/-y`, `--session`, `--help/-h`, `--version/-v` (`bin/copilot.mjs:159-169`)

**Staging tool CLI** (`tools/desktop-runtime/stage.mjs:92-112`): `--platform darwin|win32 --arch arm64|x64 [--dest DIR] [--adhoc-sign]`. `run-local.mjs:53-68`: `[--dest DIR] [--keep]`, exit 0 = smoke PASS.

**Env-var contracts produced for the desktop app** (consumed in `apps/desktop/main`):

- `COPILOT_RUNTIME_DIR` — supervisor-on switch + runtime root (`lib/launch.mjs:64` → `apps/desktop/main/services/boot-mode.ts:14`)
- `COPILOT_PRODUCTION=1` — production posture despite `app.isPackaged=false` (`lib/launch.mjs:69` → `apps/desktop/main/posture.ts:27`)
- `COPILOT_HOME` — relocates the state dir (`lib/paths.mjs:30-33`)
- Harness-only: `COPILOT_DEV=1` (`tools/cli-testing/harness/driver.mjs:90` → `posture.ts:26`), `COPILOT_DESKTOP_USER_DATA_SUBDIR` (`driver.mjs:93` — **no consumer exists**, see F4), `CTL_PORT`, `POSTURE`, `RUN_DIR`, `APP_DIR` (`driver.mjs:34-40`)

**On-disk contracts others read:**

- `staging-manifest.json` at `<dest>/runtime/<platform>-<arch>/` — "runtime is runnable" signal, `host_exec` flag, versions/sha256s (`stage.mjs:784-812`; read by `lib/stage.mjs:20-33`, `run-local.mjs:231-242`)
- Staged tree layout `python/`, `postgres/`, `services/<name>/{site-packages,src,...}`, `<dest>/web` (wallet page) — consumed by the Electron supervisor (`apps/desktop/main/services/runtime-paths.ts:81-86`)
- `.copilot-version` marker (`lib/paths.mjs:64`), `~/.cache/enterprise-desktop-runtime` download cache (`lib/paths.mjs:38-42`, `stage.mjs:50-54`, cached by `release-desktop.yml:85`)
- npm package manifest: `bin`, `files` incl. prepack-built `payload/` (`tools/cli/package.json:6-17`), validated by `scripts/pack-manifest-check.mjs`

**Harness HTTP control API**: `POST http://127.0.0.1:${CTL_PORT}/rpc` with `{cmd, ...args}` — cmds `status`, `screenshot`, `click`, `press`, `typeText`, `fill`, `waitFor`, `text`, `pageEval`, `dumpDom`, `openedUrls`, `openExternalReal`, `quit` (`driver.mjs:177-274`).

## Internal Structure

| module/group        | files                                                                              | ~LOC | responsibility                                                                                                                     |
| ------------------- | ---------------------------------------------------------------------------------- | ---- | ---------------------------------------------------------------------------------------------------------------------------------- |
| cli-entry           | tools/cli/bin/copilot.mjs                                                          | 221  | arg parsing, command dispatch, start/install flows, signal forwarding (win32 taskkill tree-kill)                                    |
| cli-lifecycle       | tools/cli/lib/paths.mjs, stage.mjs, launch.mjs, ui.mjs                             | 375  | payload-vs-dev root resolution, electron binary resolution, staging wrapper (spawns desktop-runtime/stage.mjs), app launch env, TTY output |
| cli-maintenance     | tools/cli/lib/doctor.mjs, repair.mjs, uninstall.mjs                                | 567  | diagnosis (signing spot-checks, orphaned-pg detection), non-destructive recovery, guarded full uninstall with process sweep         |
| mac-shell           | tools/cli/lib/mac-shell.mjs                                                        | 159  | branded macOS shell: CoW-clone Electron.app, rewrite Info.plist identity, swap icns, re-sign ad-hoc, stamp-cached                   |
| payload-assembly    | tools/cli/scripts/assemble-payload.mjs, pack-manifest-check.mjs                    | 295  | prepack: build desktop+frontend, mirror monorepo subset into `payload/`, bundle Google OAuth default; CI manifest validation        |
| runtime-stager      | tools/desktop-runtime/stage.mjs, manifest.json                                     | 880  | download+sha256-verify+extract python/postgres, per-service pip w/ stamps, pin-check, prune, compileall, web assets, ad-hoc signing |
| runtime-smoke       | tools/desktop-runtime/run-local.mjs                                                | 541  | executable boot contract: initdb → pg_ctl → create DBs via psycopg → migrate.py → 3× uvicorn → health/providers/dev-IdP-absent smoke |
| live-smoke-harness  | tools/cli-testing/harness/driver.mjs                                               | 320  | Playwright-Electron launch mirroring the CLI env, openExternal intercept, HTTP /rpc control server, log/screenshot capture          |
| siwe-session        | tools/cli-testing/harness/siwe-session.mjs                                         | 125  | real SIWE nonce→sign(viem)→verify against live facade, loopback handoff to the app                                                  |
| cluster docs        | tools/cli/{README,TROUBLESHOOTING}.md, tools/desktop-runtime/README.md, tools/cli-testing/{README,FIX-PLAN,FIX-VERIFICATION}.md | 660  | user docs, boot-contract doc, sharp-edges list, remediation PRDs + verification record                                              |
| manifests           | tools/cli/package.json, .gitignore, LICENSE; tools/cli-testing/package.json, package-lock.json, .gitignore | 380  | publish manifest (bin/files/engines/os), harness deps (playwright, viem)                                                            |

Architecture notes. The CLI is deliberately layered: `bin/copilot.mjs` owns UX and dispatch only; `lib/paths.mjs` is the single resolution point for the two layouts (published `payload/` vs monorepo dev checkout, `resolveRoots` at `paths.mjs:89-129`); `lib/stage.mjs` is a thin idempotence wrapper that *spawns* `tools/desktop-runtime/stage.mjs` rather than reimplementing staging (`lib/stage.mjs:83`) — the suspected staging-logic duplication does **not** exist. Destructive operations share a defensive `isSafeTarget` guard (absolute, not fs-root, not `$HOME`, ≥2 levels deep) — duplicated verbatim in `repair.mjs:20-30` and `uninstall.mjs:26-36`. `stage.mjs` (desktop-runtime) is stamp-driven throughout: archive-sha stamps for extraction, requirements+shared-source hash stamps for pip, post-sign fingerprint stamp for codesign — warm re-runs are cheap and interrupted runs fail closed by deleting `staging-manifest.json` up front (`stage.mjs:751`). The harness is intentionally not app code: it monkey-patches `shell.openExternal` in the main process via Playwright's `app.evaluate` (`driver.mjs:135-146`) so sign-in handoffs can be captured and driven.

## Dependencies

### Outbound

| target                  | kind  | what                                                                                              | evidence                                                                 |
| ----------------------- | ----- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| desktop-app             | spawn | `spawn(electron, [appDir])` with COPILOT_RUNTIME_DIR + COPILOT_PRODUCTION                         | tools/cli/lib/launch.mjs:58-73                                           |
| desktop-app             | env   | posture contract (COPILOT_PRODUCTION/COPILOT_DEV) + supervisor-on switch                          | tools/cli/lib/launch.mjs:64-69; apps/desktop/main/posture.ts:26-27       |
| desktop-app             | build | payload assembly builds `@0x-copilot/desktop` and ships its `out/` bundle; dev checkout rebuilds on every start | tools/cli/scripts/assemble-payload.mjs:93,169; tools/cli/lib/launch.mjs:38-41 |
| desktop-app             | file  | mac-shell reads `out/main/icon.icns`; BUNDLE_ID/APP_NAME mirror branding.ts + electron-builder.yml | tools/cli/lib/mac-shell.mjs:44,83; apps/desktop/main/branding.ts:21,26   |
| frontend-web            | build | builds/stages `apps/frontend/dist` (wallet.html) → `<runtime>/web`                                | tools/desktop-runtime/stage.mjs:713-731; tools/cli/scripts/assemble-payload.mjs:99-123,235-240 |
| backend-platform        | file  | stages `services/backend/{src,migrations,scripts}` + pip installs its requirements                | tools/desktop-runtime/stage.mjs:56-61,397-459                            |
| backend-facade          | file  | stages `services/backend-facade/src`; run-local spawns `backend_facade.app:app` and smokes `/v1/auth/providers` | tools/desktop-runtime/stage.mjs:62-66; run-local.mjs:428-442,460-485     |
| ai-runtime-api          | file  | stages `services/ai-backend/{src,migrations,scripts,config,skills}`; run-local spawns `runtime_api.app:app` with in-proc worker | tools/desktop-runtime/stage.mjs:67-71; run-local.mjs:399-426             |
| shared-packages         | build | pip-installs `packages/service-contracts` + `packages/audit-chain` into every service's site-packages | tools/desktop-runtime/stage.mjs:74-77,451-452                            |
| backend-identity        | contract | SIWE EIP-4361 message template + statement re-implemented byte-for-byte for signing            | tools/cli-testing/harness/siwe-session.mjs:42,66-76; services/backend/src/backend_app/identity/siwe.py:69,291 |
| backend-facade          | http  | harness SIWE nonce/verify against the live supervised facade                                      | tools/cli-testing/harness/siwe-session.mjs:48,81                         |
| external:github         | http  | downloads pinned python-build-standalone archives                                                 | tools/desktop-runtime/manifest.json:11-29; stage.mjs:208                 |
| external:maven-central  | http  | downloads pinned zonky embedded-postgres jars                                                     | tools/desktop-runtime/manifest.json:40-59; stage.mjs:208                 |
| external:google-oauth   | file  | bundles default Google OAuth client (gitignored json or publish env) into the payload             | tools/cli/scripts/assemble-payload.mjs:199-229                           |
| external:postgres       | spawn | staged `initdb`/`pg_ctl`/`postgres` binaries (embedded cluster lifecycle in smoke + repair)       | tools/desktop-runtime/run-local.mjs:271-305; tools/cli/lib/repair.mjs:32-35,92-94 |

### Inbound

- **build-deploy**: `release-desktop.yml:104` runs `stage.mjs` per (platform, arch) to populate electron-builder `extraResources`; `:85-88` caches `~/.cache/enterprise-desktop-runtime` keyed on `manifest.json`. `ci-cli.yml:38-52` runs the CLI's `check`/`smoke`/`pack-manifest-check` on 3 OSes.
- **desktop-app**: the Electron supervisor consumes the staged tree layout and `staging-manifest.json` contract this cluster produces (`apps/desktop/main/services/runtime-paths.ts:13-86`), and its README defers to `tools/desktop-runtime/README.md` as the boot-contract doc.
- **End users**: `npm i -g @0x-copilot/cli && copilot` (README.md:13-16); the published tarball embeds the payload assembled here.
- **docs-corpus / CLAUDE.md**: root CLAUDE.md instructs devs to run `node tools/desktop-runtime/stage.mjs` + `COPILOT_RUNTIME_DIR` for supervised dev boot.

## Data Owned

- `~/.0xcopilot/` (`STATE_DIR`, override `COPILOT_HOME`) — staged runtime `runtime/<platform>-<arch>/`, `.copilot-version` marker, `shell/0xCopilot.app` branded shell + `shell-stamp.json` (`lib/paths.mjs:30-34,64`; `lib/mac-shell.mjs:86-89`)
- `~/.cache/enterprise-desktop-runtime/` — sha256-keyed download cache of python/postgres archives (`lib/paths.mjs:38-42`; `stage.mjs:50-54`)
- Inside the staged tree: `.stage-stamp.json` (python/postgres), `.pip-stamp.json` per service, `.sign-stamp.json`, `staging-manifest.json` (`stage.mjs:234,268,423,644,809`)
- `tools/cli/payload/` — prepack-assembled publish payload (gitignored; `assemble-payload.mjs:22,126`)
- Harness: `tools/cli-testing/runs/<ts>/` (screenshots/logs/reports, gitignored), `run-config.local.json` (gitignored local config, e.g. Google client id) (`driver.mjs:40-46,71-80`)
- Env vars defined here: `COPILOT_HOME`, `COPILOT_RUNTIME_DIR` (value), `COPILOT_PRODUCTION`, harness `CTL_PORT`/`POSTURE`/`RUN_DIR`/`APP_DIR`, stager flags. Pins owned here: `manifest.json` (CPython 3.13.14+20260623, PostgreSQL 17.10 zonky) and the CLI's electron dependency pin (`tools/cli/package.json:26`).
- **No product data**: app userData (`~/Library/Application Support/0xCopilot/`) is owned by desktop-app; this cluster only locates it for doctor/repair/uninstall (`lib/paths.mjs:72-83`).

## Key Flows

1. **First run / `copilot start`** — `bin/copilot.mjs:83-135`: `resolveRoots` picks payload-vs-dev (`paths.mjs:89-129`) → dev checkouts rebuild the app every start (`launch.mjs:22-52`) → `stageRuntime` spawns `stage.mjs --platform --arch --dest ~/.0xcopilot --adhoc-sign` (`lib/stage.mjs:58-100`) → stage.mjs downloads+verifies (`:190-223`), extracts python/postgres (`:233-305`), per-service copy + pip + pin-check + prune + compileall (`:397-486`), stages web assets (`:713-731`), ad-hoc signs (`:621-701`), writes `staging-manifest.json` (`:784-812`) → `ensureBrandedShell` clones+rebrands Electron.app (`mac-shell.mjs:75-158`) → `launchApp` spawns Electron with the env contract (`launch.mjs:58-73`); signals forwarded, win32 tree-killed (`bin/copilot.mjs:114-127`).
2. **npm publish path** — `prepack` → `assemble-payload.mjs`: build desktop (`:79-97`) + frontend (`:99-123`), mirror stage.mjs's SERVICES subset + shared packages + stage tooling into `payload/` (`:126-164`), copy built app + synthesize minimal `package.json` (`:166-197`), bundle Google OAuth default from gitignored file or env (`:199-229`), mirror frontend dist (`:231-240`). PR CI validates the manifest without building (`pack-manifest-check.mjs`, `ci-cli.yml:52`).
3. **Boot-contract proof (`run-local.mjs`)** — read `staging-manifest.json`, require `host_exec` (`:231-242`) → initdb + `pg_ctl -w start` with short socket dir (`:271-305`) → create `backend`/`ai_backend` via staged python+psycopg — no psql in zonky (`:307-336`) → `migrate.py apply` ×2 with `postgresql+psycopg://` URLs (`:339-369`) → spawn backend/ai-backend/facade uvicorns with per-run random secrets, `single_user_desktop` profile, `*_ENVIRONMENT=production` (`:371-442`) → health-gate profile, smoke `/v1/auth/providers`, assert `/v1/dev/*` absent (`:444-500`) → reverse-order SIGTERM shutdown (`:127-147`).
4. **Release staging (CI)** — `release-desktop.yml`: native runner per (platform, arch) because wheels can't cross-build; cache keyed on `manifest.json` hash (`:85-88`); `stage.mjs` without `--adhoc-sign` (`:104`); electron-builder signs with Developer ID instead (guarded on secret presence).
5. **Live smoke** — `driver.mjs` resolves the same electron binary as the CLI (`:56-67`), builds the CLI-equivalent env with prod/dev posture (`:82-102`), `electron.launch` via Playwright (`:119-125`), intercepts `shell.openExternal` (`:135-146`), serves `/rpc` (`:276-312`); `siwe-session.mjs` then does nonce → viem-sign EIP-4361 → verify → GET the app's loopback with the bearer fields (`:48-117`), yielding a signed-in GUI.
6. **Recovery/uninstall** — `doctor` checks platform/roots/electron/bundle/manifest/signing/orphaned-pg (`doctor.mjs:46-142`); `repair` stops an orphaned postmaster via staged `pg_ctl` and optionally clears session dirs, sparing `boot-env.bin` (`repair.mjs:66-126`); `uninstall` path-prefix-matches our processes via `ps`, SIGTERM→SIGKILL sweep with respawn re-check, then guarded `rmSync` with one retry (`uninstall.mjs:56-231`).

## Test Posture

- **CI for tools/cli** (`ci-cli.yml`): `node --check` on every source, `copilot version/help` smoke, and `pack-manifest-check` on ubuntu/macos/windows. That is syntax + manifest only — **no behavioral tests exist anywhere in the cluster** (no `*.test.*` files under any of the three dirs).
- The destructive/safety-critical logic — `isSafeTarget`, uninstall's process matching + kill sweep, repair's pid parsing/`EPERM` handling, mac-shell plist surgery — is untested by automation. Regressions here delete user data or brick launches.
- `tools/desktop-runtime` has **zero CI coverage**: `ci-desktop.yml` path-filters only `apps/desktop/**` + `packages/*` (`:15-21`), no workflow even syntax-checks `stage.mjs`/`run-local.mjs`; the only CI execution of `stage.mjs` is the tag-triggered `release-desktop.yml`. A PR breaking staging surfaces at release time or on user machines.
- `run-local.mjs` is a strong manual integration test of the boot contract but is invoked by humans only (referenced from READMEs, no Makefile/workflow hook).
- The cli-testing harness is deliberately manual/LLM-judged; its FIX-VERIFICATION doc shows it catching four production-down bugs unit fakes missed — valuable, but ad-hoc: nothing re-runs it on a cadence.
- Compensating controls that do exist and are good: sha256 pinning with refusal (`stage.mjs:213-217`), `--require-hashes` for backend/facade pips, the post-install pin-check (`stage.mjs:358-395`), doctor's codesign spot-checks (`doctor.mjs:165-181`), and the supervisor side of the boot contract being well unit-tested in `apps/desktop/main/services/*.test.ts` (13 test files).

## Health Assessment

**Strengths.** This is well-crafted systems glue. The layering is right: one staging engine (`stage.mjs`) with two drivers (CLI and release CI), a thin CLI wrapper that spawns rather than reimplements, and a payload layout that makes the published package literally a mini-monorepo so `stage.mjs` runs unmodified in both worlds. Supply-chain hygiene is above average for this kind of tooling: everything downloaded is sha256-pinned with hard refusal, backend/facade pips are hash-locked, and a pin-check audits the installed set. Failure-mode thinking is pervasive — staging invalidates its completion marker up front, destructive ops have depth guards, uninstall sweeps respawned children, the branded shell falls back to stock Electron on any error. Comments explain *why* (the yoyo/psycopg2 driver trap, the 104-byte socket-path limit, why `app.isPackaged` lies under the CLI). The FIX-PLAN/FIX-VERIFICATION cycle shows the harness earning its keep.

**Weaknesses.** The cluster's facts are scattered hand-synced copies: the SERVICES/dirs list (stage.mjs ↔ assemble-payload.mjs), the boot-contract env (run-local.mjs ↔ `service-env.ts`, already drifted — run-local never sets `SIWE_ORIGIN`/`FACADE_WEB_DIST_DIR`, so the smoke no longer proves the wallet-page part of the contract its README claims it proves), the cache/state paths, the branding constants, and a third copy of the SIWE EIP-4361 template. The single most consequential drift is the Electron pin: the CLI ships and runs the app on Electron **42.1.0** while the app is developed, CI-tested, and electron-builder-released on **43.1.1** — end users on the npm path run a bundle on a different Electron major than everything else validates. Test coverage is syntax-only for code that deletes user directories. Publishing is manual with a gitignored credential file folded into the public tarball at prepack — no reproducibility or review gate.

**Overall**: healthy design, strong craftsmanship, but SSOT debt at the seams and a version-skew risk that deserves immediate attention; test posture is the weakest dimension.

## Findings

F1. **[ssot-violation | high | high]** CLI ships Electron 42.1.0 while the desktop app builds/tests on 43.1.1 — `tools/cli/package.json:26` pins `"electron": "42.1.0"` as the runtime end users launch; `apps/desktop/package.json:46` pins `"electron": "43.1.1"` for dev, CI, and electron-builder releases. The payload's `out/` bundle is esbuild-built against the 43 toolchain but executed on 42's Node/Chromium via the CLI — an entire released surface running on an Electron major nothing in CI exercises. evidence: tools/cli/package.json, apps/desktop/package.json. Suggestion: make the desktop app's electron version the single source (read it at assemble/prepack time or lint the two pins for equality in `ci-cli.yml`), and bump the CLI to 43.1.1.

F2. **[duplication | medium | high]** The supervised boot contract is implemented twice and has drifted — `run-local.mjs:371-442` hand-mirrors the env/spawn sequence that `apps/desktop/main/services/service-env.ts` + `desktop-supervisor.ts` implement, and `tools/desktop-runtime/README.md:5-7` claims the supervisor "spawns exactly the processes run-local.mjs spawns". The supervisor now also sets `SIWE_ORIGIN` and `FACADE_WEB_DIST_DIR` (`service-env.ts:191,242` — the PRD-2 wallet fix); run-local sets neither, so the smoke no longer proves the wallet-page serving that was this stack's confirmed production-down bug. evidence: tools/desktop-runtime/run-local.mjs, apps/desktop/main/services/service-env.ts, tools/desktop-runtime/README.md. Suggestion: add the web-dir/SIWE env to run-local (and a `GET /wallet.html` smoke assertion), or extract the desktop-profile env table into a shared JSON both sides read.

F3. **[ssot-violation | medium | high]** SERVICES + SHARED_PACKAGES lists hand-mirrored between stager and payload assembler — `assemble-payload.mjs:24-34` says "Mirror tools/desktop-runtime/stage.mjs SERVICES" and re-declares the per-service dir lists and shared packages that `stage.mjs:56-77` owns. A service adding a staged dir (as ai-backend did with `config`/`skills`) must be edited in both or published payloads silently lack sources. evidence: tools/cli/scripts/assemble-payload.mjs, tools/desktop-runtime/stage.mjs. Suggestion: export the tables from a small `tools/desktop-runtime/staging-spec.mjs` (or read them out of stage.mjs) and import in both.

F4. **[risk | medium | high]** Harness dev posture writes into the real prod userData — `driver.mjs:93` sets `COPILOT_DESKTOP_USER_DATA_SUBDIR="cli-test-dev"` with the comment "A separate userData dir so the dev session never collides with prod", but a repo-wide grep finds no consumer in `apps/desktop` (or anywhere): the isolation is imaginary, so dev-posture smoke runs share the production `~/Library/Application Support/0xCopilot` secrets/pgdata and can corrupt or pollute a real install on the same machine. evidence: tools/cli-testing/harness/driver.mjs, apps/desktop/main/index.ts. Suggestion: implement the env var in the desktop main (`app.setPath("userData", ...)` before ready) or delete the dead line and have the harness set `COPILOT_HOME` + a distinct app name.

F5. **[risk | medium | medium]** Manual, unreproducible npm publish that folds a gitignored credential file into the public tarball — there is no publish workflow (`.github/workflows/` has release-desktop/release-images only); `@0x-copilot/cli` is published from a maintainer machine where `prepack` → `assemble-payload.mjs:205-229` copies `apps/desktop/google-oauth.json` (gitignored; may contain `client_secret` per `apps/desktop/.gitignore:15-18`) into `payload/desktop/google-oauth.json`. Payload content (service source, built app, bundled OAuth client) depends on uncommitted local state with no CI attestation. evidence: tools/cli/scripts/assemble-payload.mjs, apps/desktop/.gitignore, .github/workflows/. Suggestion: publish from a workflow (env-synthesized OAuth id only — Desktop-app PKCE clients need no secret), and fail prepack if `google-oauth.json` contains `client_secret`.

F6. **[ssot-violation | low | high]** Third hand-copy of the SIWE EIP-4361 template — `siwe-session.mjs:42,66-76` re-implements the statement ("Sign in to Copilot") and full message layout that CLAUDE.md documents as byte-identical *pairs* in `apps/frontend/src/features/auth/siweMessage.ts:16,31` and `services/backend/src/backend_app/identity/siwe.py:69,291`. A template change now has three sync points; the harness copy fails silently at verify time. evidence: tools/cli-testing/harness/siwe-session.mjs, apps/frontend/src/features/auth/siweMessage.ts, services/backend/src/backend_app/identity/siwe.py. Suggestion: least: update the CLAUDE.md sync note to include the harness; better: have the harness import the frontend's `siweMessage.ts` builder (it is test tooling — a dev-dep import of the source module is acceptable) or fetch the template shape from the backend.

F7. **[risk | medium | high]** Zero behavioral tests + zero CI for the stager — no `*.test.*` files exist in any of the three tool dirs; `ci-cli.yml` runs only `node --check`/help-smoke/manifest-check, and no workflow path-filters `tools/desktop-runtime/**` at all (`ci-desktop.yml:15-21` covers only `apps/desktop` + packages), so `stage.mjs`/`run-local.mjs` breakage first surfaces on a release tag or a user's first run. The untested code includes `rm -rf`-adjacent logic (`uninstall.mjs:122-231`, `repair.mjs:66-126`) whose bugs destroy user data. evidence: .github/workflows/ci-cli.yml, .github/workflows/ci-desktop.yml, tools/cli/lib/uninstall.mjs. Suggestion: unit-test `isSafeTarget`/pid-parsing/process-matching with node:test (zero-dep, fits the cluster's style); add `node --check` for desktop-runtime + a path filter; consider a weekly scheduled `run-local.mjs` job on macos-14.

F8. **[duplication | low | high]** Constant/path facts duplicated across files — (a) `~/.cache/enterprise-desktop-runtime` in `tools/cli/lib/paths.mjs:38-42`, `tools/desktop-runtime/stage.mjs:50-54`, and hard-coded in `release-desktop.yml:85`; (b) default state dir `~/.0xcopilot` in `paths.mjs:30-33` and `driver.mjs:87`; (c) `APP_NAME="0xCopilot"` (`paths.mjs:26`) and `BUNDLE_ID="com.0x-copilot.app"` (`mac-shell.mjs:44`) hand-synced with `apps/desktop/main/branding.ts:21,26` and `electron-builder.yml:14`; (d) `isSafeTarget` duplicated verbatim in `repair.mjs:20-30` and `uninstall.mjs:26-36`. All are comment-documented, none machine-checked. evidence: tools/cli/lib/paths.mjs, tools/desktop-runtime/stage.mjs, tools/cli/lib/mac-shell.mjs, tools/cli/lib/repair.mjs, tools/cli/lib/uninstall.mjs. Suggestion: within tools/cli, hoist `isSafeTarget` and the branding constants into lib/paths.mjs; accept the CLI↔stager cache-path copy (package-boundary) but assert it in the stager (`--cache-dir` flag with the CLI passing it).

F9. **[dead-code | low | high]** Small dead surface in the CLI — `needsStage()` is exported from `tools/cli/lib/stage.mjs:49-51` but imported nowhere (its logic is inlined at `stage.mjs:62`); `existsSync` is imported unused in `bin/copilot.mjs:13`; `repair({ yes })` accepts a `--yes` flag it never uses (`repair.mjs:128`, help text advertises "--yes (skip prompts)" for a command with no prompts). evidence: tools/cli/lib/stage.mjs, tools/cli/bin/copilot.mjs, tools/cli/lib/repair.mjs. Suggestion: delete the export and unused import; scope `--yes` to uninstall in the help text.

F10. **[inconsistency | low | high]** Stale "broken wallet.html" comment in the harness — `siwe-session.mjs:3-5` still says it "BYPASSES the broken facade-served wallet.html (see FINDINGS)", but FIX-VERIFICATION.md:22-23 records PRD-2 as fixed and re-tested (`GET /wallet.html` → 200). A reader of the harness would believe the primary login is still dead. Also minor: `driver.mjs:57` iterates a one-element array (`for (const base of [REPO_ROOT])`) — vestigial generality after diverging from `paths.mjs#resolveElectronBinary`'s multi-base version. evidence: tools/cli-testing/harness/siwe-session.mjs, tools/cli-testing/FIX-VERIFICATION.md, tools/cli-testing/harness/driver.mjs. Suggestion: reword the header ("drives SIWE headlessly without the wallet page UI"); simplify the loop.
