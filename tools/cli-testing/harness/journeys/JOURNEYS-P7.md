# FTUE P7 verification journeys — coverage map

Live-smoke drivers for the First-Run (FTUE) user journeys in
`docs/plan/first-run-onboarding/JOURNEYS.md`, in the `cli-testing` harness style
(one driver per journey, over the real supervised Electron app). Each journey
spawns its own `driver.mjs`, runs hermetically in a throwaway userData subdir
(`COPILOT_DESKTOP_USER_DATA_SUBDIR`), asserts on the **real** shipped testIds +
verbatim copy, screenshots each state, and writes `runs/<ts>-<name>/REPORT.md`.

These are **scaffolds**: they assert everything reachable without a live model /
real key / P4 tools, and mark the rest `BLOCKED` (honest coverage). A later
"make it green" pass fills the blocked tails. Result vocabulary:

- **PASS** — every asserted step held.
- **PARTIAL** — the asserted prefix held; a documented blocked tail was skipped
  (exit 0).
- **FAIL** — a step that should hold did not (exit 1).

Selectors + copy live in one place: `firstRunHarness.mjs` (`SEL`, `COPY`),
sourced from `FirstRunSurface.tsx` / `Gate.tsx` / `KeyForm.tsx` /
`OnboardingComposer.tsx` / `SuggestionChips.tsx` / `Acknowledgment.tsx` /
`RunEmptyState.tsx` / `SignInGate.tsx` / `Composer.tsx`.

## Journey → asserted vs blocked

### J1 — Local-first (`first-run-j1-local-first.mjs`)

| Step                                                        | Coverage                                                                                                    |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| sign-in → "Use locally" → FTUE gate (State A)               | ASSERTED (`first-run-surface`/`first-run-gate`/`first-run-local-card`, hero copy "First, give it a model.") |
| "Start download" → State B composer                         | ASSERTED (`first-run-start-download` → `first-run-composer`, H1 "What should we run first?")                |
| model pill leads with "Qwen 3 4B"                           | ASSERTED (`composer-model-toggle` text contains the preset name)                                            |
| model-pill "· N%" download progress text                    | **BLOCKED** — needs a live Ollama pull in flight                                                            |
| starter chip fills the draft                                | ASSERTED (`first-run-chip-watch-wallet` → `composer-textarea` value)                                        |
| send in-flight → ack "Queued — starts when the model lands" | ASSERTED (`composer-send` → `first-run-ack-title`)                                                          |
| download 100% → run-create → stream → workspace handoff     | **BLOCKED** — needs Ollama + full ~4.3 GB pull                                                              |

### J2 — BYOK (`first-run-j2-byok.mjs`)

| Step                                           | Coverage                                                                                                       |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| sign-in → FTUE gate → key card                 | ASSERTED (`first-run-key-card`, copy "Bring your own key")                                                     |
| "Add a key" → inline KeyForm                   | ASSERTED (`first-run-add-key` → `first-run-keyform`/`-key-input`/`-key-note`)                                  |
| provider pick + paste + Connect → save         | **BLOCKED** — needs a real key (`FIRST_RUN_BYOK_PROVIDER` + `FIRST_RUN_BYOK_KEY`); server live-check must pass |
| State B composer with a real (non-local) model | **BLOCKED** (same) — asserts pill is not the local preset                                                      |
| send → ack "Starting your first run"           | **BLOCKED** (same)                                                                                             |
| handoff → workspace (`destination-outlet`)     | **BLOCKED** (same)                                                                                             |

The key is read from the environment only — **never hardcoded, never logged**.
When the env is unset the journey asserts steps 1–2 and reports PARTIAL.

### J4 — Skip (`first-run-j4-skip.mjs`)

| Step                                          | Coverage                                                                                                     |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| sign-in → FTUE gate (State A)                 | ASSERTED (`first-run-surface`/`first-run-skip`)                                                              |
| "skip — open the workspace" → Run cockpit     | ASSERTED (`first-run-skip` → `destination-outlet` + `run-empty-state`)                                       |
| Run empty-state "Set up your model" CTA       | ASSERTED-IF-PRESENT (`run-empty-setup-cta`; BLOCKED-noted if a model is already configured)                  |
| relaunch (same userData) → gate never renders | ASSERTED (2nd launch → `destination-outlet`, `first-run-surface` absent — proves `first-run.json` persisted) |

Fully assertable today (no model/key needed) once the runtime is staged.

### J5 — Returning user (`first-run-j5-returning.mjs`)

| Step                                                   | Coverage                                                                                                                                  |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| setup: launch #1 completes onboarding (skip)           | ASSERTED (flag persisted)                                                                                                                 |
| launch #2 → gate never renders → straight to workspace | ASSERTED (`destination-outlet` present; `first-run-surface`/`first-run-gate` absent)                                                      |
| cold pre-seed of `first-run.json` before first launch  | **BLOCKED** — needs the device-account `workspaceId` (assigned at runtime by the local mint); `seedFirstRunComplete` shows the file shape |
| strict "surface never even flashed" (poll transition)  | **BLOCKED** — tightening left for the make-it-green pass                                                                                  |

## Blocked-until, summarized

- **Live local model (J1 tail):** Ollama up + the curated Qwen 3 4B pull to 100%.
  Enables the `· N%` progress text, `100% → run-create → stream`, and the handoff.
- **Real provider key (J2 tail):** `FIRST_RUN_BYOK_PROVIDER` + `FIRST_RUN_BYOK_KEY`
  so the server live-check + run-create succeed.
- **P4 tools / wallet chip (cross-cutting):** the tools popover (web search,
  connector 1-click) and the top-bar wallet chip (`first-run-wallet-slot` exists
  as a slot but is not yet filled). No journey drives them yet.
- **Device workspaceId (J5 cold pre-seed):** resolve/inject the local-mint
  workspaceId to write `first-run.json` before the very first launch.

## How to run (once the stack is staged)

Prereqs (same as `local-account.mjs`): `npm install --prefix tools/cli-testing`,
a staged runtime (`node tools/desktop-runtime/stage.mjs --platform … --arch …`),
and a built desktop app (`npm run build --workspace @0x-copilot/desktop`).

```bash
# from the repo root; COPILOT_HOME points at the dir containing runtime/<platform>-<arch>
COPILOT_HOME="$PWD/apps/desktop/resources" \
  node tools/cli-testing/harness/journeys/first-run-j1-local-first.mjs

COPILOT_HOME="$PWD/apps/desktop/resources" \
  node tools/cli-testing/harness/journeys/first-run-j4-skip.mjs

COPILOT_HOME="$PWD/apps/desktop/resources" \
  node tools/cli-testing/harness/journeys/first-run-j5-returning.mjs

# J2 with a real key (optional; asserts steps 1–2 without it):
COPILOT_HOME="$PWD/apps/desktop/resources" \
  FIRST_RUN_BYOK_PROVIDER=anthropic FIRST_RUN_BYOK_KEY=sk-ant-… \
  node tools/cli-testing/harness/journeys/first-run-j2-byok.mjs
```

Each journey uses a distinct default control port (J1 8792, J2 8793, J4 8794,
J5 8795); override with `CTL_PORT`. Outputs land in
`tools/cli-testing/runs/<ts>-<journey>/` (screenshots + `REPORT.md`, git-ignored).

```

```
