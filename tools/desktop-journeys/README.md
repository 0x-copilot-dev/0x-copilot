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
  MIGRATION.md           ← why there are nine files and not sixty-four
  _lib.py                ← DriverSession, the phase runner, shared run helpers
  _workspace_lib.py      ← workspace lanes, grants, macOS native-dialog driving
  _release_matrix_lib.py ← the G3-G10 release-matrix vocabulary
  runs/                  ← per-journey screenshots + evidence + logs (git-ignored)

  first_run.py               shell_and_projects.py     workspace_consent.py
  transcript_rendering.py    artifacts_and_surfaces.py workspace_bypass.py
  composer_and_budgets.py    mcp_connected.py          installed_payload.py
  virtuals_provider.py       virtuals_thinking.py      focus_panel_default.py
```

**Nine journeys, not sixty-four scripts.** One supervised boot costs initdb +
migrations + three service starts, so the old shape spent most of its wall clock
booting. Journeys are grouped by **what they need from the machine** — target,
profile, lane, model — and each group shares one boot.

Sharing a boot does not cost the per-claim verdict. Each file runs **phases**:
isolated, individually reported as `passed` / `failed` / `blocked` / `skipped`,
and the next one runs whatever the last one did. Read the phase table the run
prints; the exit code is only its aggregate.

| Journey                     | What it covers                                                     |
| --------------------------- | ------------------------------------------------------------------ |
| `first_run.py`              | sign-in, BYOK, model preselect, where the first message lands      |
| `transcript_rendering.py`   | rich cards, Focus, thinking, interleaving, timeline, citations     |
| `composer_and_budgets.py`   | the Tools pill, todo checklist, tool-call limits, run health       |
| `shell_and_projects.py`     | the fixed window frame, and filing a chat into a project           |
| `workspace_consent.py`      | the folder bar, the ungranted ask, approvals (DEFAULT lane)        |
| `workspace_bypass.py`       | the execution-mode pill and what a grant buys (default + enforce)  |
| `artifacts_and_surfaces.py` | Studio artifacts, the canvas, identity colour, the inference floor |
| `mcp_connected.py`          | everything needing a REAL connected MCP server (reuse profile)     |
| `installed_payload.py`      | the shipped npm artifact and the G3-G10 release matrix             |
| `virtuals_provider.py`      | Virtuals as a native provider — **needs no key** (see below)       |
| `virtuals_thinking.py`      | a gateway model's reasoning survives to the transcript (Kimi K3)   |
| `focus_panel_default.py`    | Focus opens with the Run-details column folded, and still opens    |

Two `installed-payload` journeys are still standalone and unfolded:
`generative-workflows/g1_markdown_lifecycle.py` and `g2_csv_lifecycle.py`. See
[MIGRATION.md](./MIGRATION.md).

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

> **`virtuals_provider.py` is the exception, deliberately.** Its central claim is
> that a WRONG key is rejected, so it supplies its own bad one and needs nothing
> from `.env` — it is the only BYOK-shaped journey that runs on a machine with no
> keys at all. It exists because Virtuals' `/v1/models` answers 200 with no
> credential, so a listing-based probe would report "connected" for a typo; the
> journey fails if that false-pass ever returns.

> A **fresh install has no keys** — the app starts at the sign-in gate with nothing
> configured, and the user adds a key during first-run. The BYOK journeys reproduce
> exactly that: they do NOT pre-inject keys into the app's environment. (Note: the
> desktop supervisor _does_ forward `OPENAI/ANTHROPIC/GOOGLE_API_KEY` from the
> launching shell if they are exported — so to reproduce the true keyless first-run,
> launch with them unset, which these scripts do by not exporting them.)

## Running a journey

```bash
# from the repo root — 3.11+ required (StrEnum); a miniconda 3.10 on PATH will
# die at import before executing a line of product code.
/opt/homebrew/bin/python3.13 tools/desktop-journeys/first_run.py
/opt/homebrew/bin/python3.13 tools/desktop-journeys/transcript_rendering.py
```

Each file spawns its own driver on `CTL_PORT` (default 8790), runs its phases,
writes screenshots + evidence + a `driver.log` under `runs/<name>/`, prints a
per-phase table plus a machine-readable JSON summary, and tears the app down.

### Exit codes — only `0` means every phase ran and passed

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
/opt/homebrew/bin/python3.13 tools/desktop-journeys/artifacts_and_surfaces.py; code=$?
[ "$code" -eq 0 ] || [ "$code" -eq 3 ] || exit "$code"
```

### Verifying the globally installed npm payload

Use this after `make desktop-install` to drive the exact global
`@0x-copilot/cli` payload — including its packaged desktop bundle and Electron
dependency — instead of the source checkout:

```bash
/opt/homebrew/bin/python3.13 tools/desktop-journeys/installed_payload.py
```

The driver reports `target: installed-payload`, its global CLI package root, and
the `payload/desktop` app directory through `status`; the smoke asserts those
fields before touching the DOM. To run any existing suite against the exact
same installed artifact, set the target once:

```bash
COPILOT_DESKTOP_TEST_TARGET=installed-payload \
  /opt/homebrew/bin/python3.13 tools/desktop-journeys/transcript_rendering.py
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
  /opt/homebrew/bin/python3.13 tools/desktop-journeys/<journey>.py

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
# 1. Connect by hand, in the journey's own profile. mcp_connected.py uses
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
  /opt/homebrew/bin/python3.13 tools/desktop-journeys/mcp_connected.py
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
- **A fresh profile throws the connection away** on purpose — a virgin install has no
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
  `.desktop-window-frame`. See `shell_and_projects.py` phases SP-1 … SP-5.
- `transport(method, path)` — an **authenticated** facade call made through the app
  (`window.bridge.ipc.invoke("transport.request", …)`), e.g. `transport("GET",
"/v1/agent/models")` to read the model catalog as the signed-in user.
- `open_destination(label)`, `on_run()`, `run_mode()`, `model_pill()` — assertions.

Service logs for the supervised stack: `~/Library/Application Support/0xCopilot/logs/`
(or the run's `runs/<name>/driver.log` for the Electron main-process output).

## Adding a claim

**Add a phase to the journey that already boots what you need — do not add a
tenth file.** Find the journey whose boot class matches (target, profile, lane,
model) and append a phase:

```python
def xx7_the_thing_you_are_proving(s: DriverSession) -> None:
    """One line stating the claim, then why a unit test cannot make it."""
    ...assert...

# in main()'s phases list:
("XX-7", "the thing you are proving", xx7_the_thing_you_are_proving),
```

Four rules, each learned the expensive way:

1. **Order phases by the state they consume**, and say so in the docstring. A
   phase needing the virgin FTUE composer must precede anything that sends.
2. **Dependent phases declare what they need.** `require(STATE.get(k), ...)` so a
   missing predecessor SKIPS rather than failing with a confusing error.
3. **Model-driven shape is `blocked`, not `failed`.** If the model declines to
   call web search, the shape under test never occurred — that is a statement
   about the run, not about the renderer.
4. **A phase that writes a persisted setting runs last**, or it silently
   rewrites an earlier phase's premise.

A genuinely new boot class (a lane or target no journey uses) is the only reason
to add a file. Never hardcode a key; never print one. Prefer `transport()` for
backend truth and DOM reads for what the user actually sees.

## Rich chat is a required matrix, not a single happy path

`transcript_rendering.py` phases **TR-1 … TR-5** are the canonical desktop proof
for chat cards. They perform four real, keyed runs in one session:

| Case               | What must appear in the desktop transcript                                                 |
| ------------------ | ------------------------------------------------------------------------------------------ |
| Direct web search  | exactly one built-in `web_search` card with accumulated args/result and no invented source |
| One subagent       | exactly one singular fleet card with one successfully completed child                      |
| Parallel subagents | exactly one two-agent fleet, successful child rows, and a real nested `web_search` trace   |
| Mixed run          | a direct `web_search` tool card **and** a two-agent fleet in the same message              |
| Retained history   | after the next message starts, the completed prior subagent remains in the Agents panel    |

They are ordered and DEPENDENT: TR-3 asserts that TR-1's tool card and TR-2's
fleet card SURVIVE a new run binding, so it skips rather than fails if its
predecessors produced nothing.

They then verify the actual desktop controls: tool-card disclosure by pointer,
Space, and Enter; a terminal fleet-card disclosure by pointer, Space, and Enter;
a **live** fleet-child expansion by pointer, Space, and Enter; and the same
keyboard contract for that exact task in the Agents-side-panel row. Both
subagent disclosures must render the real nested tool timeline. A missing
required card, wrong cardinality, failure status, stale arguments, missing nested
activity, missing payload, vanished completed child, or reintroduced multi-run
selector is a test failure. It is not reported as a benign
“blocked” run.
