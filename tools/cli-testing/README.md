# cli-testing — live-smoke harness for the `copilot` desktop app

Drives the **real** Electron app (the one `copilot` / `node tools/cli/bin/copilot.mjs`
launches) through an automatable control API so a person — or an LLM acting as judge —
can walk sign-in and every surface, capture screenshots + service logs, and rule
pass/fail. Built for the "unit fakes hid a real-run breakage" problem: this exercises the
supervised stack end-to-end, not mocks.

## Layout

- `harness/driver.mjs` — launches the app via Playwright's `_electron` driver using the
  **same** electron binary + appDir + env the CLI uses (`COPILOT_RUNTIME_DIR`,
  `COPILOT_PRODUCTION=1`), and exposes an HTTP control server (`POST /rpc`). Commands:
  `status`, `screenshot`, `click`, `fill`, `press`, `typeText`, `waitFor`, `text`,
  `pageEval`, `dumpDom`, `openedUrls` (captured `shell.openExternal` handoff URLs — the
  main process intercept lets sign-in browser flows be driven in a controlled Chrome),
  `quit`. Env: `CTL_PORT` (default 8790), `POSTURE` (`prod`|`dev`), `RUN_DIR`.
- `harness/siwe-session.mjs` — completes a real SIWE login against the live facade
  (nonce → EIP-4361 sign → verify) and feeds the app's loopback handoff, yielding a
  signed-in session for surface testing. Throwaway key by default (`--pk` to override).
- `runs/<ts>/` — per-run outputs (screenshots, service-log snapshots, `REPORT.md`,
  `FINDINGS.md`). Git-ignored.
- `run-config.local.json` — local config (e.g. Google client id). Git-ignored.

## Run

```bash
npm install --prefix tools/cli-testing            # playwright + viem
# stage + build the app the same way a user would, once:
node tools/cli/bin/copilot.mjs install
npm run build --workspace @0x-copilot/desktop

# launch the driver (keeps the app alive; control it over curl)
CTL_PORT=8790 POSTURE=prod RUN_DIR="$PWD/tools/cli-testing/runs/$(date +%Y%m%d-%H%M%S)" \
  node tools/cli-testing/harness/driver.mjs &

# example: screenshot the current screen
curl -s -X POST http://127.0.0.1:8790/rpc -H 'content-type: application/json' \
  -d '{"cmd":"screenshot","name":"01"}'
```

The facade port is allocated dynamically by the supervisor per boot; find it with
`lsof -nP -iTCP -sTCP:LISTEN | grep python` and probe `/v1/auth/providers`.

Service logs for the supervised stack: `~/Library/Application Support/0xCopilot/logs/`.
