# desktop-journeys — live user-journey tests for the 0xCopilot desktop app

Scripted end-to-end **user journeys** that drive the **real packaged desktop app**
(supervised Electron + embedded PostgreSQL + the three Python services) exactly as
a person would — sign-in, FTUE, adding a provider key, sending a message, switching
destinations — then **assert the outcome** with screenshots and service logs.

They exist so an agent (or a human) can **reproduce a reported bug** or **verify a
fix** against the honest end-to-end stack, not a mock. Every action is a real DOM
click / fill or an authenticated call made _through_ the running app, so a green
journey proves the actual wiring. This is the harness that repeatedly caught
"unit-tests-pass-but-the-real-app-is-broken" regressions.

> These journeys drive the app through the Playwright control server in
> [`tools/cli-testing/harness/driver.mjs`](../cli-testing/harness/driver.mjs) — the
> canonical desktop driver. `_lib.py` here spawns it and wraps its `/rpc` API.

## Layout

```
tools/desktop-journeys/
  README.md              ← you are here (setup + how to run)
  _lib.py                ← shared harness: DriverSession, load_env_key, common actions
  runs/                  ← per-journey screenshots + logs (git-ignored)
  provider-key-byok/     ← a SET of journeys → one JOURNEYS.md + runnable scripts
  focus-mode/
  chat-nav-model/
```

One **folder per set** of related journeys; each set has one **`JOURNEYS.md`**
describing the user story + expected outcomes + the testIds it asserts, plus one or
more runnable `*.py` scripts.

## Prerequisites

### 1. Build the app once

The journeys launch the **real** app, so the supervised runtime (Python services +
embedded Postgres) and the desktop bundle must be staged/built first. Two options:

```bash
# A) The packaged install a user gets — build the CLI, stage the runtime, launch:
make desktop-install

# B) Stage + build + launch the supervised app in place (dev of the desktop shell):
make desktop-supervised            # add ARGS="--skip-stage" to reuse a prior stage
```

Either way this produces the staged runtime at
`apps/desktop/resources/runtime/<platform>-<arch>/` (the Python services + Postgres)
and the desktop bundle at `apps/desktop/out/`. The journeys point `COPILOT_HOME` at
`apps/desktop/resources` by default.

> **Re-stage after backend changes.** `apps/desktop/resources/runtime/**` is a
> _snapshot_ of the Python services. If you changed `services/*`, re-stage
> (`node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64`, or
> `make desktop-install`) or the journey runs stale backend code. Frontend-only
> changes just need `npm run build --workspace @0x-copilot/desktop`.

### 2. Playwright (once)

```bash
npm install --prefix tools/cli-testing     # playwright + viem, used by the driver
```

### 3. Provider keys (from `.env`, never hardcoded)

Journeys that add a BYOK key read it from **`services/ai-backend/.env`** via
`load_env_key("openai" | "anthropic" | ...)`. Put your keys there:

```
# services/ai-backend/.env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

The value is passed straight into the app's keychain field and is **never printed,
logged, or committed** — only lengths / HTTP statuses ever surface. `.env` is
git-ignored.

> A **fresh install has no keys** — the app starts at the sign-in gate with nothing
> configured, and the user adds a key during first-run. The BYOK journeys reproduce
> exactly that: they do NOT pre-inject keys into the app's environment. (Note: the
> desktop supervisor _does_ forward `OPENAI/ANTHROPIC/GOOGLE_API_KEY` from the
> launching shell if they are exported — so to reproduce the true keyless first-run,
> launch with them unset, which these scripts do by not exporting them.)

## Running a journey

```bash
# from the repo root
python3 tools/desktop-journeys/chat-nav-model/new_chat.py
```

Each script spawns its own driver on `CTL_PORT` (default 8790), runs hermetically in
a throwaway userData subdir (fresh first-run), writes screenshots + a `driver.log`
under `runs/<name>/`, exits non-zero on failure, and cleans up the app.

### Verifying a branch build (keep `main` clean — work in a worktree)

To exercise an unmerged branch without touching the main checkout:

```bash
# 1. isolated worktree off main
git worktree add -b <branch> .claude/worktrees/<name> origin/main
cd .claude/worktrees/<name>

# 2. give the worktree its own node_modules (workspace links point at ITS packages)
npm install
npm run build --workspace @0x-copilot/desktop     # bundles the branch's renderer

# 3. run a journey from the MAIN checkout's driver (it has playwright+electron),
#    but point it at the WORKTREE's app + reuse main's staged services
#    (frontend-only changes need no re-stage):
cd /path/to/main/checkout
APP_DIR="$PWD/.claude/worktrees/<name>/apps/desktop" \
COPILOT_HOME="$PWD/apps/desktop/resources" \
  python3 tools/desktop-journeys/<set>/<journey>.py

# 4. after merge: git worktree remove -f .claude/worktrees/<name> && git branch -D <branch>
```

`DriverSession(app_dir=..., copilot_home=...)` accepts the same overrides directly.

## The driver control API (what `_lib.py` wraps)

`driver.mjs` launches Electron via Playwright and exposes `POST /rpc` with:
`status`, `screenshot`, `click`, `fill`, `press`, `typeText`, `waitFor`, `text`,
`pageEval`, `dumpDom`, `openedUrls`, `quit`. `_lib.py` adds:

- `sign_in_local()` / `ftue_add_key(provider, key)` / `send_first_run_message(text)` —
  the common first-run actions, by real testId.
- `transport(method, path)` — an **authenticated** facade call made through the app
  (`window.bridge.ipc.invoke("transport.request", …)`), e.g. `transport("GET",
"/v1/agent/models")` to read the model catalog as the signed-in user.
- `open_destination(label)`, `on_run()`, `run_mode()`, `model_pill()` — assertions.

Service logs for the supervised stack: `~/Library/Application Support/0xCopilot/logs/`
(or the run's `runs/<name>/driver.log` for the Electron main-process output).

## Writing a new journey set

1. `mkdir tools/desktop-journeys/<set>/` with a `JOURNEYS.md` (user story, steps,
   expected outcome, testIds asserted, and what BLOCKS full coverage if anything).
2. Add `<journey>.py` that `from _lib import DriverSession, load_env_key`, walks the
   flow, asserts, and screenshots. Keep testIds in `_lib.py`'s common actions when
   shared, so a renamed testId is fixed in one place.
3. Never hardcode a key; never print a key. Prefer asserting through `transport()`
   for backend truth and DOM reads for what the user actually sees.
