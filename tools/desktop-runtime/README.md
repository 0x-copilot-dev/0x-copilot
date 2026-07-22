# Desktop runtime tooling

Stages and boots the self-contained desktop runtime (bundled CPython 3.13 +
PostgreSQL 17 + the three backend services) **without Electron**. The Electron
supervisor later ships the staged tree as app resources and spawns exactly the
processes `run-local.mjs` spawns.

## Files

| File            | Purpose                                                                                                              |
| --------------- | -------------------------------------------------------------------------------------------------------------------- |
| `manifest.json` | sha256+url pinned binary inputs (python-build-standalone 3.13.14+20260623, zonky embedded-postgres 17.10.0).         |
| `stage.mjs`     | Downloads (cache: `~/.cache/enterprise-desktop-runtime`), sha256-verifies, extracts, pip-installs, prunes, compiles. |
| `run-local.mjs` | Boots the staged stack end-to-end on this mac and smoke-tests it. Exit 0 = PASS.                                     |

Zero non-builtin node deps. External tools: system `tar` (bsdtar — reads
.tar.gz, .txz and the zonky .jar, which is a zip) and the _staged_ python.

## Usage

```sh
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64              # full staging on an arm64 mac
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64 --adhoc-sign # + credential-free ad-hoc code-signing (macOS host)
node tools/desktop-runtime/stage.mjs --platform win32  --arch x64                # download+extract only (no exec cross-platform)
node tools/desktop-runtime/run-local.mjs                                         # boot + smoke + clean shutdown
node tools/desktop-runtime/run-local.mjs --keep                                  # leave the stack running
```

`--adhoc-sign` (macOS host only) ad-hoc code-signs every staged Mach-O binary
(`codesign --sign -`) and strips the quarantine xattr, so an **unsigned** bundle
runs on Apple Silicon without Apple Developer credentials. This is what the
`copilot` CLI ([tools/cli](../cli)) uses to distribute through npm/bun. The
electron-builder packaging path leaves it **off** and signs with a real
Developer ID instead (`build/sign-nested.js`), so the flag is opt-in.

Staged output lands in `apps/desktop/resources/runtime/<platform>-<arch>/`
(gitignored — never commit staged binaries). Cross-target staging verifies
sha256 and extracts, but skips pip/compileall; run the same command on a
matching host (or CI runner) to populate `site-packages`.

## Staged layout (what the Electron supervisor finds under `resourcesPath/runtime`)

```
runtime/<platform>-<arch>/
├── staging-manifest.json        # versions, sha256s, host_exec flag
├── python/                      # python-build-standalone install_only tree
│   └── bin/python3.13           # (win32: python.exe at tree root)
├── postgres/                    # zonky tree: bin/ lib/ share/ — bin has ONLY initdb, pg_ctl, postgres
└── services/
    ├── backend/        {site-packages, src, migrations, scripts}
    ├── backend-facade/ {site-packages, src}
    └── ai-backend/     {site-packages, src, migrations, scripts, config, skills}
```

`site-packages` includes the pinned third-party set (`--require-hashes` for
backend/facade) plus `copilot-service-contracts` and
`copilot-audit-chain`. Every service process runs with
`PYTHONPATH=<svc>/site-packages:<svc>/src` — nothing else.

## Boot contract (proven by run-local.mjs)

1. `postgres/bin/initdb -D <data> -U postgres -A trust -E UTF8 --no-locale --no-instructions`
2. `postgres/bin/pg_ctl -D <data> -l <log> -o "-p <port> -c listen_addresses=127.0.0.1 -c unix_socket_directories=<short-dir>" -w start`
3. Create DBs `backend` + `ai_backend` via staged python + psycopg (**no psql/createdb in the zonky bundle**)
4. `python services/backend/scripts/migrate.py apply` with `BACKEND_DATABASE_URL=postgresql+psycopg://…/backend`
   and `python services/ai-backend/scripts/migrate.py apply` with `RUNTIME_DATABASE_URL=postgresql+psycopg://…/ai_backend`
   (yoyo needs the explicit `+psycopg` driver marker; the bare `postgresql://` scheme resolves to psycopg2, which is not bundled)
5. Spawn, in order — see run-local.mjs for the full env of each:
   - backend: `python -m uvicorn backend_app.desktop_app:app` (`BACKEND_ENVIRONMENT=production`, plain `DATABASE_URL`, the four generated secrets)
   - ai-backend: `python -m uvicorn runtime_api.app:app` (`RUNTIME_ENVIRONMENT=production`, `RUNTIME_STORE_BACKEND=postgres`, `RUNTIME_START_IN_PROCESS_WORKER=true`, **`RUNTIME_MIGRATIONS_AUTO_APPLY=false`**, `RUNTIME_ENABLE_LOCAL_MODELS=true`, `RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME=true`). The two local-model flags are separate authorisations:
     - `RUNTIME_ENABLE_LOCAL_MODELS=true` surfaces the `/v1/local-models/*` API — **Settings → Local models** and the first-run local-model card (download an HF GGUF + run via a user-installed [Ollama](https://ollama.com/download) at `localhost:11434`). Every route but `/status` 404s when it is off.
     - `RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME=true` additionally authorises ai-backend to **detect the Ollama binary on this machine and start it** (`POST /v1/local-models/runtime/start`). It is what lets `/status` report a real `runtime_state` — telling "Ollama not installed" apart from "Ollama installed but stopped", which are otherwise the same `ollama_running:false` — and it is what the card's **Restart Ollama** action calls. **Desktop-only.** Containerised deployments (self-host, where `OLLAMA_BASE_URL` points at `host.docker.internal`) leave it off: they can neither see nor spawn a binary on the host, so they honestly report `runtime_state:"unknown"` and render no button that cannot work. The ai-backend default is `false`; the packaged supervisor sets both flags identically (`apps/desktop/main/services/service-env.ts`).
   - facade: `python -m uvicorn backend_facade.app:app` (`FACADE_ENVIRONMENT=production`, `BACKEND_URL`, `AI_BACKEND_URL`)
     All three get `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop` and `OTEL_SDK_DISABLED=true`.
6. Health-gate `GET /v1/health` on each (asserts `deployment_profile == single_user_desktop`), then
   `GET {facade}/v1/auth/providers` (200 + providers list) and assert `/v1/dev/*` is 404 (no dev IdP in production).
7. Shutdown: SIGTERM facade → ai-backend → backend, then `pg_ctl -m fast stop`.

## Sharp edges (all hit for real; details in each file's comments)

- `pip install --target` **is** compatible with `--require-hashes` (pip ≥ 25); local dir packages
  (service-contracts, audit-chain) must go in a **separate** un-hashed invocation. A pin-check asserts
  the installed set matches requirements.txt afterwards.
- yoyo + bare `postgresql://` → psycopg2 import error. Always pass `postgresql+psycopg://` to the
  migrate scripts; keep plain `postgresql://` for the apps' psycopg pools.
- ai-backend's postgres store auto-applies migrations at startup using its plain `DATABASE_URL` —
  it re-enters yoyo and crashes on psycopg2. Set `RUNTIME_MIGRATIONS_AUTO_APPLY=false`; the
  supervisor owns migrations as a separate boot step.
- zonky bin/ ships only `initdb`, `pg_ctl`, `postgres`. No psql/createdb — create databases through
  a driver. The macOS trees are relocatable (rpath-relative dylibs); the win32 tree carries its DLLs
  (plus pgAdmin-era wx DLLs) inside `bin/`.
- Unix socket dirs must stay short (~104-byte limit): use a short temp dir for
  `unix_socket_directories`, not a deep app-support path.
- `compileall` may exit 1 on py2-era files inside shipped deps; treat as non-fatal (stage.mjs does).
- Signing note for the mac app: executable Mach-O files live in `python/bin/*`,
  `python/lib/libpython3.13.dylib`, `postgres/bin/*`, `postgres/lib/**/*.dylib|*.so`, and native wheels'
  `services/*/site-packages/**/*.so`. Two distribution paths sign these differently:
  the electron-builder packaged app signs them with a Developer ID + notarizes
  (`build/sign-nested.js`); the `copilot` CLI ad-hoc signs them at stage time via
  `--adhoc-sign` (no credentials — enough for arm64 execution, since npm/curl-staged
  files never get the quarantine xattr that would trigger Gatekeeper notarization).
  `--adhoc-sign` walks the staged tree, signs every Mach-O (executables + `.so`/`.dylib`),
  and stamps a `.sign-stamp.json` so warm re-stages skip it.
