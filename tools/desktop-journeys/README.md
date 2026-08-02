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

**Two harness properties that have already cost real debugging time. Read them
before you trust a result:**

1. [The staged runtime is a SNAPSHOT](#1b-the-staged-runtime-is-a-snapshot--re-stage-after-any-services-change)
   of `services/*`. A stale stage tests code that no longer exists.
2. [A journey can never complete an OAuth connect](#a-journey-can-never-complete-an-oauth-connect--do-it-out-of-band)
   — the driver suppresses the browser handoff, so connects must be done out of
   band, before the journey runs.

## Layout

```
tools/desktop-journeys/
  README.md              ← you are here (setup + how to run)
  _lib.py                ← shared harness: DriverSession, load_env_key, common actions
  runs/                  ← per-journey screenshots + logs (git-ignored)
  provider-key-byok/     ← a SET of journeys → one JOURNEYS.md + runnable scripts
  focus-mode/
  chat-rich-cards/       ← required live tool + subagent card matrix
  chat-nav-model/
  shell-overflow/        ← the shell must never scroll the document (incl. short windows)
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
`<COPILOT_HOME>/runtime/<platform>-<arch>/` (the Python services + Postgres) and the
desktop bundle at `apps/desktop/out/`.

**Where `COPILOT_HOME` defaults to depends on the journey's target**, because the two
targets stage to different places and are not interchangeable:

| Target                                          | Default `COPILOT_HOME`   | Staged by                 |
| ----------------------------------------------- | ------------------------ | ------------------------- |
| `source` (most journeys)                        | `apps/desktop/resources` | `make desktop-supervised` |
| `installed-payload` (G1, G2, G3–G10, the smoke) | `~/.0xcopilot`           | `make desktop-install`    |

Set `COPILOT_HOME` explicitly to override either — that is how an isolated worktree
reuses a stage owned by the primary checkout (see the branch-build recipe below).
Note that pointing an `installed-payload` journey at `apps/desktop/resources` defeats
its purpose: it would prove the source tree rather than the shipped npm artifact, so
there is deliberately no fallback in that direction.

`tools/desktop-journeys/_lib.py` owns this rule for both the preflight checks and
`DriverSession` (`resolve_copilot_home` / `staged_runtime_dir`), so a journey always
inspects the same staged runtime it launches. If you add a journey, take its target
from there rather than re-deriving a default.

### 1b. The staged runtime is a SNAPSHOT — re-stage after any `services/*` change

`<COPILOT_HOME>/runtime/<platform>-<arch>/services/**` is a **copy** of
`services/*` taken at stage time. It is not a link, and nothing re-syncs it. A
journey launched against a stale stage runs **old backend code** and reports its
verdict with total confidence — the single most expensive way to be wrong here,
because the evidence looks impeccable.

This is not hypothetical, and a stale stage is the normal state rather than the
exception — a stage only advances when someone re-runs the stager, so it falls
behind on the very first commit after it was built. Every stage found on this
machine while writing this section was behind the checkout it was being used to
test. **Check the stage's age before trusting any backend verdict:**

```bash
# Left = when the runtime was staged. Right = newest commit touching the service.
# If the staged copy is OLDER, your journey is testing code you have since changed.
ls -ld apps/desktop/resources/runtime/*/services/ai-backend/src
git log -1 --format='%cd  %h %s' --date=format:'%Y-%m-%d %H:%M' -- services/ai-backend/src
```

Re-stage whenever anything under `services/*` changed:

```bash
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64   # match your host
# or, for the installed-payload target:
make desktop-install
```

Frontend-only changes need no re-stage — just
`npm run build --workspace @0x-copilot/desktop`.

### 2. Playwright (once)

```bash
npm install     # root workspace owns the Playwright version used by the driver
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

# The required desktop rich-chat matrix: direct web search, one subagent,
# two parallel subagents, and web search + two subagents in ONE message.
python3 tools/desktop-journeys/chat-rich-cards/rich_chat.py
```

Each script spawns its own driver on `CTL_PORT` (default 8790), runs hermetically in
a throwaway userData subdir (fresh first-run), writes screenshots + a `driver.log`
under `runs/<name>/`, exits non-zero unless it passed, and cleans up the app.

### Exit codes — only `0` means the journey ran and passed

| Code | Meaning                                                                                                    |
| ---- | ---------------------------------------------------------------------------------------------------------- |
| `0`  | **Passed.** The journey ran end-to-end and every assertion held.                                           |
| `1`  | **Failed.** An assertion failed (traceback + screenshots under `runs/`).                                   |
| `2`  | **Blocked.** A declared product/harness capability is absent (G3–G10).                                     |
| `3`  | **Skipped.** A local prerequisite is absent — no staged runtime, no desktop bundle, no BYOK key in `.env`. |

A skip is **not** a pass: the journey never ran, so it exits non-zero and cannot be
mistaken for success by `&&`, `set -e`, or a CI step. The reason is also printed as
structured JSON (`{"journey": "...", "outcome": "skipped", "reason": "..."}`), so a
caller that legitimately wants to tolerate a skip can match on the code or the
`outcome` field rather than on silence:

```bash
python3 tools/desktop-journeys/generative-workflows/g2a_csv_artifact_surface.py; code=$?
[ "$code" -eq 0 ] || [ "$code" -eq 3 ] || exit "$code"
```

### Verifying the globally installed npm payload

Use this after `make desktop-install` to drive the exact global
`@0x-copilot/cli` payload — including its packaged desktop bundle and Electron
dependency — instead of the source checkout:

```bash
python3 tools/desktop-journeys/installed-payload/installed_payload_smoke.py
```

The driver reports `target: installed-payload`, its global CLI package root, and
the `payload/desktop` app directory through `status`; the smoke asserts those
fields before touching the DOM. To run any existing suite against the exact
same installed artifact, set the target once:

```bash
COPILOT_DESKTOP_TEST_TARGET=installed-payload \
  python3 tools/desktop-journeys/chat-rich-cards/rich_chat.py
```

`APP_DIR` is rejected for this target. That guard prevents an apparently green
“installed” run from silently falling back to `apps/desktop` in the checkout.

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

`DriverSession(app_dir=..., copilot_home=...)` accepts the source-worktree
overrides directly. `DriverSession(installed_payload=True)` uses the installed
CLI target and intentionally cannot take `app_dir`.

## A journey can NEVER complete an OAuth connect — do it out of band

**The driver suppresses the browser handoff.** `driver.mjs` replaces
`shell.openExternal` in the Electron main process and only _records_ the URL
([lines 149-162](../cli-testing/harness/driver.mjs) — its own comment: "suppress
the OS-browser open; the test drives Chrome itself"). Every journey here runs
through that driver, so no journey can finish a provider OAuth flow:

1. Connect is clicked (by a human or a script);
2. `ConnectorOAuthCoordinator.connect`
   (`apps/desktop/main/connectors/oauth-coordinator.ts`) binds the loopback and
   calls `openExternal(authorization_url)` — which is the stub, so **no consent
   screen ever opens**;
3. the loopback therefore never receives a `?code`, and the flow can only end as
   a redirect-stage `ConnectorOAuthError` — **`connect cancelled`** when anything
   cancels it (a Cancel click, the harness quitting the app), or
   **`loopback redirect timed out`** (`apps/desktop/main/auth/loopback-server.ts`)
   if it is left to expire. Those two strings are the signature of this harness
   property, not of a broken connector.

`openedUrls` returns what was captured and `openExternalReal` can re-open a
captured URL for real, but neither makes a connect something a journey can drive
unattended. **Treat "is X connected?" as a precondition, never as a step** — the
same reason `jF_linear_mcp.py` reports `blocked` (exit `2`) instead of pretending.

### The recipe: connect once, by hand, in the profile the journey reuses

`DriverSession(name=..., fresh=False)` uses the userData subdir
`journey-<name>-reuse` (`_lib.py`), so a connection made in that exact profile is
still there on the next run. Launch the app yourself against the **same stage and
the same profile** — a normal launch has a real `shell.openExternal` — complete
the consent, quit, then run the journey:

```bash
# 1. Connect by hand, in the journey's own profile. FS-F is
#    DriverSession(name="fs-f-linear-mcp"), so the subdir is:
COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" \
COPILOT_DESKTOP_USER_DATA_SUBDIR=journey-fs-f-linear-mcp-reuse \
  npm run dev --workspace @0x-copilot/desktop
#    → "Tools" in the left rail → Linear → Connect → finish OAuth in the browser
#    → confirm the row reads Connected → QUIT the app.

# 2. The journey now finds it connected. COPILOT_HOME must be the SAME path
#    that COPILOT_RUNTIME_DIR pointed at above, or you get a different database.
COPILOT_HOME="$PWD/apps/desktop/resources" \
COPILOT_JOURNEY_DOTENV=/path/to/services/ai-backend/.env \
  python3 tools/desktop-journeys/filesystem-access/jF_linear_mcp.py
```

What silently defeats this:

- **A different subdir is a different device account.** It must be
  `journey-<DriverSession name>-reuse`, exactly. Anything else has zero installed
  MCP servers, and the journey correctly reports "not connected".
- **The stage must match** — `COPILOT_RUNTIME_DIR` above is the journey's
  `COPILOT_HOME`. The connection lives in the supervised Postgres under that
  staged runtime, not in the app bundle, so a different stage is a different
  database.
- **Do not set `COPILOT_DEV=1` or `COPILOT_AUTH_MODE=dev-mint`.** A staged
  `COPILOT_RUNTIME_DIR` alone already resolves PRODUCTION posture
  (`apps/desktop/main/posture.ts`), which is what the driver launches with
  (`POSTURE=prod` ⇒ `COPILOT_PRODUCTION=1`). A dev-mint launch signs in a
  different persona, and the journey would not see what that persona connected.
- **`FS_F_FRESH=1` throws the profile away** on purpose — a virgin install has no
  MCP servers, which is a different (and narrower) question.

The same handoff applies to anything else needing a browser round trip: Google
sign-in, a custom MCP server's OAuth, any future connector.

## The driver control API (what `_lib.py` wraps)

`driver.mjs` launches Electron via Playwright and exposes `POST /rpc` with:
`status`, `screenshot`, `click`, `fill`, `press`, `typeText`, `waitFor`, `text`,
`pageEval`, `resizeWindow`, `dumpDom`, `openedUrls`, `quit`. `_lib.py` adds:

- `sign_in_local()` / `ftue_add_key(provider, key)` / `send_first_run_message(text)` —
  the common first-run actions, by real testId.
- `resize(width, height)` — resize the REAL window's content area, as a user dragging
  it would. Use it to exercise layout that only breaks on a short or narrow window;
  assert on the returned `viewport`, since a window manager may refuse a size.
- `document_scroll()` — measure whether the DOCUMENT can scroll. It must never be able
  to: the desktop window is a fixed frame and every scroll region lives inside
  `.desktop-window-frame`. See [`shell-overflow/`](./shell-overflow/JOURNEYS.md).
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

## Rich chat is a required matrix, not a single happy path

[`chat-rich-cards/JOURNEYS.md`](./chat-rich-cards/JOURNEYS.md) is the canonical
desktop proof for chat cards. It performs four real, keyed runs in one fresh
desktop session:

| Case               | What must appear in the desktop transcript                                                 |
| ------------------ | ------------------------------------------------------------------------------------------ |
| Direct web search  | exactly one built-in `web_search` card with accumulated args/result and no invented source |
| One subagent       | exactly one singular fleet card with one successfully completed child                      |
| Parallel subagents | exactly one two-agent fleet, successful child rows, and a real nested `web_search` trace   |
| Mixed run          | a direct `web_search` tool card **and** a two-agent fleet in the same message              |
| Retained history   | after the next message starts, the completed prior subagent remains in the Agents panel    |

It then verifies the actual desktop controls: tool-card disclosure by pointer,
Space, and Enter; a terminal fleet-card disclosure by pointer, Space, and Enter;
a **live** fleet-child expansion by pointer, Space, and Enter; and the same
keyboard contract for that exact task in the Agents-side-panel row. Both
subagent disclosures must render the real nested tool timeline. A missing
required card, wrong cardinality, failure status, stale arguments, missing nested
activity, missing payload, vanished completed child, or reintroduced multi-run
selector is a test failure. It is not reported as a benign
“blocked” run.
