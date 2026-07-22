# P7 — FTUE E2E parity + make-green — verification plan

> Scope: turn the four merged FTUE journey scaffolds (`first-run-j1/j2/j4/j5`) into
> live-green coverage on the **real supervised desktop stack**, add a
> `ui-design-reviewer` parity audit per FTUE state, and wire both into
> path-filtered CI with no prod secrets. Everything below is grounded in the
> shipped code — selectors, copy, and boot mechanics are cited to file+line.
>
> Author's posture: this is a **UI + parity + up-to-handoff** verification lane.
> The run→stream **execution** tail is deliberately kept in its existing home
> (`desktop-supervised-boot-drill.yml` + Tier-A hermetic tests) so we do **not**
> weaken the fail-closed `RUNTIME_FAKE_MODEL` guarantee to green a UI journey.
> See §1.4 and §5-Q1 for the trade-off and the optional seam.

---

## 0. What already exists (grounded inventory)

| Asset | Path | State |
| --- | --- | --- |
| Journey coverage map | `tools/cli-testing/harness/journeys/JOURNEYS-P7.md` | merged; PARTIAL by design |
| Shared harness (SEL + COPY + driver session) | `tools/cli-testing/harness/journeys/firstRunHarness.mjs` | merged |
| J1 local-first | `tools/cli-testing/harness/journeys/first-run-j1-local-first.mjs` | asserts prefix, tail BLOCKED |
| J2 BYOK | `tools/cli-testing/harness/journeys/first-run-j2-byok.mjs` | asserts prefix, tail gated on env key |
| J4 skip | `tools/cli-testing/harness/journeys/first-run-j4-skip.mjs` | fully assertable today |
| J5 returning | `tools/cli-testing/harness/journeys/first-run-j5-returning.mjs` | assertable today; 2 tightenings pending |
| Underlying RPC driver | `tools/cli-testing/harness/driver.mjs` | Playwright `_electron`, `POST /rpc` |
| Proven reference journey | `tools/cli-testing/harness/journeys/local-account.mjs` | green live 2026-07-21 (prod posture) |
| Parity agent | `.claude/agents/ui-design-reviewer.md` | Read/Bash/Grep/Glob, sonnet |
| Parity spec | `docs/plan/first-run-onboarding/design-source/SPEC.md` | copy + CSS inventory (no reference PNG) |
| CI template | `.github/workflows/desktop-supervised-boot-drill.yml` | macOS-only, staged runtime, cadence-gated |
| Deterministic model | `services/ai-backend/src/agent_runtime/execution/fake_model.py` | env-gated, fail-closed |

**Key facts the plan leans on (verified in code):**

- The four journeys already exit **0 on PARTIAL** and **1 on FAIL**
  (`firstRunHarness.mjs:105-136`), so "make green" means: (a) the asserted
  prefix actually runs & PASSes on the live stack, and (b) blocked tails are
  un-blocked where it does not cost the fail-closed guarantee.
- The gate mounts the **real** 3-state surface, not the P0 placeholder:
  `apps/desktop/renderer/bootstrap.tsx:106-123` passes
  `renderFirstRun={() => <FirstRunSurfaceMount/>}` — so every `first-run-*`
  selector resolves to `packages/chat-surface/src/onboarding/FirstRunSurface.tsx`,
  not `FirstRunPlaceholder` (`FirstRunGate.tsx:487-534`, which is unused at runtime).
- Hermetic isolation is real in **all** postures: `apps/desktop/main/index.ts:108-113`
  honors `COPILOT_DESKTOP_USER_DATA_SUBDIR` (rejects `..`/`/`) before any
  `getPath("userData")` read; the flag persists to `userData/settings/first-run.json`
  (`apps/desktop/main/services/first-run-store.ts:18,35-36`).
- The driver runs **prod posture** (`firstRunHarness.mjs:181` `POSTURE:"prod"` →
  `driver.mjs:88-101` sets `COPILOT_PRODUCTION=1`, `COPILOT_RUNTIME_DIR=$COPILOT_HOME`),
  which mints the **local device account** via the host-token path — the exact path
  `local-account.mjs` already proves green live.

---

## 1. Make J1 / J2 / J4 / J5 green on the live desktop stack

### 1.0 One-time prereqs (identical to `local-account.mjs`)

```bash
# from repo root
npm install --prefix tools/cli-testing                                  # playwright 1.49.1 + viem
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64      # match host; --adhoc-sign in CI
npm run build --workspace @0x-copilot/desktop                            # build main + renderer
```

`stage.mjs` populates `apps/desktop/resources/runtime/<platform>-<arch>/` (CPython +
PostgreSQL + the three service wheels). `COPILOT_HOME` must point at the dir
**containing** `runtime/<platform>-<arch>` — i.e. `apps/desktop/resources`
(`driver.mjs:86-87`: `COPILOT_RUNTIME_DIR = COPILOT_HOME || ~/.0xcopilot`).

### 1.1 Harness wiring recap (what the plan does not need to change)

The shared harness already implements every mechanic the task asks for; the
make-green work is mostly **running it** plus small tail un-blocks:

- **COPILOT_HOME override + isolated userData** — `startDriver({subdir,port,runDir,env})`
  (`firstRunHarness.mjs:169-270`) spawns `driver.mjs` with `COPILOT_DESKTOP_USER_DATA_SUBDIR:subdir`
  and `POSTURE:"prod"`. Each journey computes a unique `subdir` (`journey-jN-${Date.now()}`)
  and a distinct default `CTL_PORT` (J1 8792, J2 8793, J4 8794, J5 8795), so four
  journeys can even run concurrently.
- **Boot wait** — `waitForSignIn()` polls `sign-in-button` through the 240 s
  first-boot window (`firstRunHarness.mjs:229-243`) — embedded-PG initdb + migrations.
- **Drive gate→choice→composer→send→ack→workspace** — `signInLocal()` clicks
  `[data-testid="sign-in-button"]` (`SignInGate.tsx:350` → shared button renders
  `data-testid={testId}` at `SignInGate.tsx:390`); then `rpc("click"/"fill"/"waitFor"/"pageEval")`
  drive the surface. Every selector in `SEL` (`firstRunHarness.mjs:41-89`) and copy
  string in `COPY` (`firstRunHarness.mjs:92-102`) is verified below against the real DOM.

**Selector/copy cross-check (all resolve — no scaffold drift):**

| Harness `SEL` | Renders at |
| --- | --- |
| `sign-in-button` / `-wallet-button` / `-google-button` | `SignInGate.tsx:330,338,350` (via shared `testId` prop → `:390`) |
| `first-run-surface` / `-skip` / `-brand` / `-footer` / `-wallet-slot` | `FirstRunSurface.tsx:463,484,465,492,475` |
| `first-run-loading` | `FirstRunGate.tsx:138` (gate loading phase) |
| `first-run-gate` | `Gate.tsx:112` |
| `first-run-local-card` / `-start-download` | `Gate.tsx:62,76` (also `FirstRunLocalCard.tsx:49,125`) |
| `first-run-key-card` / `-add-key` | `Gate.tsx:115,136` |
| `first-run-keyform` / `-key-input` / `-key-note` / `-key-connect` | `KeyForm.tsx:143,167,170,203` |
| `first-run-composer` / `-composer-h1` | `OnboardingComposer.tsx:154,156` |
| `first-run-chips` / `-chip-watch-wallet` / `-chip-draft-thread` / `-chip-explain-csv` | `SuggestionChips.tsx:73,81` (id-templated from `FIRST_RUN_SUGGESTIONS:36-58`) |
| `composer-textarea` / `-model-toggle` / `-send` | `Composer.tsx:1049,1115,1182` |
| `first-run-ack` / `-ack-title` | `Acknowledgment.tsx:48,49` |
| `destination-outlet` | `apps/desktop/renderer/DestinationOutlet.tsx` |
| `run-empty-state` / `run-empty-setup-cta` | `RunEmptyState.tsx:132,162` |

`COPY.gateH1 "First, give it a model."` = `firstRun.ts:104`;
`COPY.composerH1 "What should we run first?"` = `OnboardingComposer.tsx:41`;
`COPY.ackStarting/ackQueued` = `Acknowledgment.tsx:14-15`;
`COPY.modelPresetName "Qwen 3 4B"` = the on-device catalog row name
(`useOnboardingComposerModels.ts:127-130`, from `QWEN3_4B_PRESET.name`).

### 1.2 Per-journey — asserted prefix, blocked tail, un-block

#### J4 — Skip → **green today** (target: PASS, not PARTIAL)

Fully assertable now (`first-run-j4-skip.mjs`): sign-in → gate (`first-run-skip`) →
click skip → `destination-outlet` + `run-empty-state` → **relaunch same subdir** →
gate absent. This is the load-bearing proof that `first-run.json` persisted
(`first-run-store.ts`) and that a returning launch resolves to `complete`
(`FirstRunGate.tsx:99-115`).

- **Only soft spot:** the `run-empty-setup-cta` check is BLOCKED-IF-absent
  (`first-run-j4-skip.mjs:55-62`) because a pre-configured model hides the CTA.
  On a **fresh hermetic subdir with no BYOK key and no local pull**, no model is
  configured, so the CTA is deterministically present. Make-green action: assert
  it present (drop the BLOCKED branch) **because** the journey guarantees the
  clean-slate precondition — the CTA absence would itself be a regression on a
  fresh install.
- **Acceptance:** J4 = **PASS** (0 blocked) on a staged runner.

#### J5 — Returning → **green today**, two tightenings

Setup launch #1 completes onboarding via skip, launch #2 asserts
`destination-outlet` present and `first-run-surface`/`first-run-gate` absent
(`first-run-j5-returning.mjs:44-71`). Two documented un-blocks:

1. **Strict "never flashed" (currently a NOTE, `:72-74`).** Un-skip: after
   `waitForSignIn()` on launch #2, poll for `first-run-surface` at high frequency
   across the sign-in→shell transition and assert it is **never** present before
   `destination-outlet` appears — proving the gate resolved to `complete` before
   first paint, not that it merely unmounted. Implement as a tight `isPresent`
   loop bounded by the `destination-outlet` wait.
2. **Cold pre-seed (`seedFirstRunComplete`, `:98-110`, currently unused).** Un-skip
   requires the device **accountKey** the flag is keyed by. Post-P0-hardening the
   key is a SHA-256 of the verified session `sub` (STATUS "P0 — key by `claims.sub`",
   `161ada6e`), assigned by the local mint at runtime — not knowable before launch.
   Resolution options (pick one, §5-Q2): (a) add a debug `first-run.*` IPC read that
   returns the resolved key after mint, seed the file, then relaunch; or (b) accept
   the complete-once-then-relaunch setup as the canonical J5 (it already proves the
   returning experience) and leave the cold pre-seed documented-but-parked.
- **Acceptance:** J5 = **PASS** with tightening (1) landed; cold pre-seed stays a
  documented optional per §5-Q2.

#### J1 — Local-first → **prefix green today**, execution tail is Tier-B

Asserted prefix (`first-run-j1-local-first.mjs:46-114`) is greenable now and needs
**no model**: gate (hero `First, give it a model.`) → `first-run-start-download` →
`first-run-composer` (`What should we run first?`) → model pill contains `Qwen 3 4B`
→ `first-run-chip-watch-wallet` fills `composer-textarea` → `composer-send` →
`first-run-ack` titled **`Queued — starts when the model lands`**.

Why the ack is deterministically "queued" **without** Ollama (grounded):
`handleStartDownload` sets `engine=local, stage="dl"` synchronously
(`FirstRunSurface.tsx:352-356`), so the composer renders regardless of Ollama
state. With no pull reaching 100 %, `modelReady=false`
(`FirstRunSurfaceMount.tsx:264-265`; `FirstRunSurface.tsx:372-376`). On send,
`useFirstRunLaunch.launch` takes the `!modelReady` branch → phase `"queued"`
**without calling `createFirstRun`** (`useFirstRunLaunch.ts:142-159`) — no backend,
no key. The binder's phase-watch flips the surface to the ack
(`FirstRunSurfaceMount.tsx:300-305`), variant `"queued"`
(`FirstRunSurfaceMount.tsx:438-439`) → title from `Acknowledgment.tsx:15`.

Blocked tail (`:83-84,116-119`): the model-pill `· N%` progress text and
`100% → run-create → stream → workspace handoff`. Both need the on-device pull to
advance — that is a real Ollama + ~4.3 GB download, not a smoke step. **Un-block
options** (§1.4): a real tiny Ollama model on the runner, or the optional
fail-closed seam. **Recommendation:** leave the J1 execution tail to Tier-B
(`desktop-supervised-boot-drill.yml`) and keep J1's acceptance = the asserted
prefix (**PARTIAL, exit 0**, 2 blocked). The workspace-handoff *transition* is
already proven hermetically by J4/J5 (skip → `destination-outlet`).

#### J2 — BYOK → **prefix green today**, tail behind a gated secret

Asserted prefix (`first-run-j2-byok.mjs:49-65`): gate → `first-run-key-card`
(`Bring your own key`) → `first-run-add-key` → inline KeyForm
(`first-run-keyform` + `-key-input` + `-key-note`). No key needed; reports PARTIAL.

Tail (`:73-120`) needs a **real working key** via env
(`FIRST_RUN_BYOK_PROVIDER` + `FIRST_RUN_BYOK_KEY`, `:36-38`) — read from env only,
never hardcoded/logged (`JOURNEYS-P7.md:49`). Why a fake key is insufficient:
`PUT /v1/settings/provider-keys` accepts a format-valid-but-unreachable key with
`live_check=skipped_unreachable` (JOURNEYS.md:21), so the surface *could* flip to
State B — but the subsequent run-create fails with a provider error → phase
`"error"` (`useFirstRunLaunch.ts:134-137`) → ack never renders → step 4 FAILs.
So the tail is honest only with a real key.

- **Un-block:** supply `FIRST_RUN_BYOK_KEY` from a **repo secret gated to non-fork
  PRs** (§3). Keyless CI stays PARTIAL (exit 0).
- **Acceptance:** J2 = **PARTIAL** keyless (1 blocked); **PASS** when the gated key
  is present (drives save → composer with a non-local pill → ack
  `Starting your first run` → `destination-outlet`).

### 1.3 The deterministic-model question (why it isn't injected through the app)

`RUNTIME_FAKE_MODEL` substitutes the chat model at the single build funnel and is
**fail-closed by construction**: it activates only on an explicit truthy env and
the shipped desktop "neither sets nor allowlists it" (`fake_model.py:13-16`).
Verified: `buildServiceEnv` starts from `{}` and only copies
`ENV_PASSTHROUGH_ALLOWLIST` (`service-env.ts:173-178`), which contains
**no** `RUNTIME_FAKE_MODEL` (`service-env.ts:11-36`). Therefore a run driven
through the **shipped supervisor** (what `driver.mjs` launches) cannot use the fake
model — by design, and this plan does **not** propose changing that for a UI test.

The deterministic model's legitimate home is `run-local.mjs`, which boots the same
topology as a **staging** process and sets `RUNTIME_FAKE_MODEL=1` itself
(`run-local.mjs:318-323`, wired at `:695`), driving conversation→run→SSE to a
terminal `run_completed` hermetically. That is exactly `desktop-supervised-boot-drill.yml`'s
job. The FTUE journeys and that drill are **complementary Tiers**, not duplicates:
FTUE = the real UI + gate/persist/handoff on the shipped app; the drill = the
execution/topology/real-HTTP+SSE tail with the fake model.

### 1.4 Inventory of every `.skip` / TODO / BLOCKED and its un-skip

These are node scripts, not a test-framework with `.skip`; the "skips" are explicit
conditional branches. Complete list:

| # | Where | Kind | Un-skip action |
| --- | --- | --- | --- |
| 1 | J1 `:84` | `blocked('model-pill "· N%"')` | Ollama pull in flight (Tier-B) or seam §5-Q1 |
| 2 | J1 `:116-119` | `blocked(100%→run→stream→handoff)` | same as #1; else keep PARTIAL |
| 3 | J2 `:67-72` `!HAVE_KEY` | env-gated skip of steps 3–5 | set gated `FIRST_RUN_BYOK_KEY` (§3) |
| 4 | J4 `:55-62` | `blocked` if setup-CTA absent | assert present on clean subdir (§1.2 J4) |
| 5 | J5 `:72-74` | NOTE: strict "never flashed" | add the high-freq poll (§1.2 J5-1) |
| 6 | J5 `:98-110` | `seedFirstRunComplete` unused | resolve device accountKey (§5-Q2) |

No literal `it.skip`/`describe.skip` exist in the harness — grep confirms the
above are the entire blocked/TODO surface.

---

## 2. The `ui-design-reviewer` parity audit method

The agent (`.claude/agents/ui-design-reviewer.md`) is a **sonnet subagent** with
Read/Bash/Grep/Glob that reads screenshots via `Read` and measures against a
reference + the CSS ground truth. It has no browser — it consumes PNGs the harness
already captures. The method:

### 2.1 Capture one screenshot per FTUE state (per host)

The journeys already `shot(...)` most states into `runs/<ts>-<name>/screenshots/`
(`driver.mjs:204-208`). Coverage today + the gaps to add:

| FTUE state | Captured by | Gap to add for the audit |
| --- | --- | --- |
| **login / sign-in gate** | `local-account.mjs` `sign-in-gate` | add a `signin` shot at the top of one FTUE journey (or reuse local-account) |
| **gate (choice, State A)** | J1/J2/J4 `state-a-gate` | — |
| **KeyForm open** | J2 `keyform` | — |
| **composer (State B)** | J1 `state-b-composer` | — (has `Qwen 3 4B` pill) |
| **chip-filled draft** | J1 `chip-filled` | — |
| **local card downloading (`dl`)** | none | needs the local-model pull → capture `first-run-local-progress` state (seam/Ollama) |
| **local card ready** | none | needs pull→100 → `first-run-local-ready` |
| **ack (State C)** | J1 `state-c-ack-queued`, J2 `state-c-ack-starting` | — |
| **workspace after handoff** | J2 `workspace`, J4 `workspace-after-skip` | — |

Run each host in the viewer's **light and dark** theme (chat-surface tokens are
theme-aware) — add a `resize_window`/theme pass or capture both `COLOR_SCHEME`s so
the reviewer checks "no second accent; sky-only" (SPEC:46) in both.

### 2.2 Baseline: web surface + SPEC (design-source has no reference PNG)

`docs/plan/first-run-onboarding/design-source/` contains **only** `SPEC.md` — no
mock PNG. So the parity baseline is three-legged:

1. **Live web render of the same shared surface** — the web binder is merged
   (`apps/frontend/src/features/onboarding/FirstRunSurfaceMount.tsx` +
   `FirstRunGate.tsx`), and both hosts mount the identical
   `packages/chat-surface/src/onboarding/*`. Screenshot the web FTUE (Vite dev or a
   built web bundle) for the **exact web baseline** the agent's rubric expects
   ("state the desktop value, the web value, and whether they match").
2. **SPEC.md** — verbatim copy strings (SPEC:19-29) and the `fr-*` CSS inventory
   with target sizes (SPEC:39-42): hero `600 23px/1.2`, card radius 12, mono label
   9–10.5, jade check, footer `space-between`.
3. **Canonical mock via DesignSync** — re-fetch project `73f810d9` files
   (`copilot-firstrun.jsx/.css`) for pixel spot-checks when SPEC is ambiguous
   (SPEC:3). Use the deferred `DesignSync` tool; where SPEC and DesignSync disagree,
   DesignSync wins (SPEC:3).

### 2.3 Invocation + ranked findings

Per state, dispatch the agent with: the desktop PNG path(s), the web PNG path(s),
the SPEC section, and the CSS files it should grep for ground truth
(`packages/chat-surface/src/onboarding/onboarding.css`,
`apps/desktop/renderer/firstrun.css`, `packages/design-system/src/styles.css`).
Output is its fixed format (`.claude/agents/ui-design-reviewer.md:52-58`):
`SHIP`/`SHIP WITH NITS`/`DO NOT SHIP` + a findings table (area · verdict · measured
observation · web comparison · fix) + top-3 must-fix. Aggregate across states into
one ranked P7 parity report.

### 2.4 Known **intended** divergences (feed to the agent to prevent false-positives)

- **Local-card size copy:** code shows `Qwen 3 4B · 4.3 GB · free forever`
  (`firstRun.ts:110`) vs SPEC's `5.6 GB` (SPEC:22). This is a **deliberate**
  reconcile — the shipped Q8_0 preset is 4.3 GB real (STATUS P2 row; commit
  `7282858d`). Not a regression. (§5-Q4: confirm the canonical number so both
  SPEC and the mock are reconciled.)
- **Footer left** `v2.1.0 · local build` (`firstRun.ts:135`, SPEC:28) — the version
  is copy-data, not a live version; do not flag as stale.
- **Trial hatch / Haiku starter row** — SHELVED in v1 (SPEC:7); its absence is
  intended, not a missing control.

Everything else (hero tracking, primary-button `--color-accent-contrast`,
pill/popover geometry, jade vs accent usage) is a **real** parity target — flag
divergences.

---

## 3. CI wiring (path-filtered, no prod secrets)

Add `.github/workflows/ci-ftue-e2e.yml`, modeled 1:1 on
`desktop-supervised-boot-drill.yml` (the only workflow that already stages + boots
the supervised darwin runtime):

- **Runner / cadence:** `macos-14`, `timeout-minutes: 40`. The staged runtime ships
  only darwin/win32 and boots darwin on host (`desktop-supervised-boot-drill.yml:75-79`),
  and macOS minutes bill ~10x — so **not every PR**. Triggers:
  `pull_request` + `push:main` **path-filtered** to the FTUE surface, plus
  `schedule` (weekly) + `workflow_dispatch`.
- **Path filter** (the FTUE blast radius):
  ```yaml
  paths:
    - "tools/cli-testing/**"
    - "packages/chat-surface/src/onboarding/**"
    - "apps/desktop/renderer/FirstRunGate.tsx"
    - "apps/desktop/renderer/onboarding/**"
    - "apps/desktop/main/services/first-run-*.ts"
    - "tools/desktop-runtime/**"
    - ".github/workflows/ci-ftue-e2e.yml"
  ```
- **Steps** (reuse the drill's proven sequence, `:82-115`): checkout →
  `setup-node@22` → cache `~/.cache/enterprise-desktop-runtime` keyed on
  `manifest.json` → `npm ci` → `npm install --prefix tools/cli-testing` →
  `stage.mjs --platform darwin --arch arm64 --adhoc-sign` →
  `npm run build --workspace @0x-copilot/desktop` → run **J4, J5, J1** with
  `COPILOT_HOME="$PWD/apps/desktop/resources"`. Upload `tools/cli-testing/runs/**`
  (screenshots + `REPORT.md`) as an artifact for the parity audit + failure triage.
- **No prod secrets (fork-safe):** J4/J5 = PASS, J1 = PARTIAL — all keyless,
  hermetic (local device mint, no network). **J2's key tail** runs only when
  `FIRST_RUN_BYOK_KEY` is present — a repo secret **gated to non-fork events**
  (`github.event.pull_request.head.repo.fork == false`), so a fork PR never sees
  the secret and J2 stays PARTIAL there. Mirrors `ci-*` "PR CI must not require
  production secrets" (root CLAUDE.md CI rules).
- **Always-on net is unchanged:** the per-PR safety net stays the unit suites
  (`ci-desktop.yml` typecheck + vitest over `apps/desktop` + `packages/chat-surface`)
  and the Tier-A hermetic run→stream tests; `desktop-supervised-boot-drill.yml`
  keeps the topology/execution tail. `ci-ftue-e2e.yml` adds the **UI + parity +
  handoff** tier on the shipped app.
- **Optional parity job:** a second `workflow_dispatch` job that, after the journeys
  produce screenshots, invokes the `ui-design-reviewer` pass (§2) and attaches the
  ranked report. Kept dispatch-only initially (subagent + DesignSync are not a
  blocking gate).

---

## 4. Sequence + acceptance

### 4.1 Execution order

1. **Stage + build once** (§1.0) — precondition for all four.
2. **J4** (skip) → make PASS: assert the setup-CTA present on the clean subdir
   (§1.2 J4). This is the cheapest full-green and proves persist + handoff.
3. **J5** (returning) → make PASS: land the strict "never flashed" poll
   (§1.2 J5-1). Decide cold pre-seed per §5-Q2.
4. **J1** (local-first) → confirm the asserted prefix runs green live; keep the
   execution tail BLOCKED (Tier-B). Result: PARTIAL, exit 0.
5. **J2** (BYOK) → confirm the keyless prefix green; wire the gated secret so the
   tail PASSes on non-fork CI; PARTIAL on forks.
6. **Add `ci-ftue-e2e.yml`** (§3) and run it on `workflow_dispatch` to validate the
   staged boot in CI before flipping on the path-filtered PR trigger.
7. **Parity audit** (§2) once screenshots land — feed the four/eight PNGs + web
   baseline + SPEC to `ui-design-reviewer`; triage the ranked findings; fix
   confirmed regressions (not the §2.4 intended divergences).
8. **Flip STATUS** `docs/plan/first-run-onboarding/STATUS.md` P7 row from
   `🟡 scaffolded` to `✅` only when the acceptance below holds.

### 4.2 "A journey is green when…"

- **Exit-code contract (all four):** the process exits **0** and `REPORT.md` records
  `RESULT: PASS` or `RESULT: PARTIAL` with a non-negative documented `blocked` count;
  a `RESULT: FAIL` (exit 1) is never green (`firstRunHarness.mjs:122-136`).
- **J4 green ⇔** PASS with **0 blocked**: gate → skip → `destination-outlet` +
  `run-empty-state` + `run-empty-setup-cta` present, and relaunch (same subdir) shows
  `destination-outlet` with `first-run-surface` **absent**.
- **J5 green ⇔** PASS: launch #2 reaches `destination-outlet` with
  `first-run-surface` **and** `first-run-gate` absent, and the strict flash-poll
  observed the surface **never** present pre-shell.
- **J1 green ⇔** PARTIAL (exit 0) with exactly the two documented blocks: the
  asserted prefix (State A hero copy → State B composer with a `Qwen 3 4B` pill →
  chip fills the draft → send → State C ack `Queued — starts when the model lands`)
  all held. **PASS** only if the local-model tail was supplied (Ollama/seam).
- **J2 green ⇔** PARTIAL keyless (steps 1–2 held: gate → key card → KeyForm), or
  **PASS** with a real key (save → non-local pill → ack `Starting your first run` →
  `destination-outlet`).
- **Parity green ⇔** `ui-design-reviewer` returns `SHIP` or `SHIP WITH NITS` for
  every captured state, with the only `fail`/`warn` rows being the §2.4 documented
  intended divergences (each reconciled or explicitly waived).

---

## 5. Risks & open questions

1. **Q1 — Do we want the run→stream→handoff tail green *through the shipped app*?**
   That requires either a gated real key / a small Ollama model on the runner, or a
   **narrow fail-closed seam** to pass `RUNTIME_FAKE_MODEL` into the supervised
   ai-backend. Any such seam must preserve the guarantee in `fake_model.py:13-16`
   (shipped desktop never sets/allowlists it): e.g. only add it to the child env when
   the parent is **not** in production posture **and** an explicit `COPILOT_E2E=1` is
   set — but note the driver runs prod posture for the real host-token mint, so this
   would force a dev-posture variant (different sign-in path). **Recommendation:** do
   not add the seam; keep the execution tail in `desktop-supervised-boot-drill.yml`.
2. **Q2 — J5 cold pre-seed** needs the device `accountKey` (SHA-256 of verified
   `sub`, per P0-hardening) before first launch. Expose a debug `first-run.*` IPC read
   to resolve it, or keep the complete-once-then-relaunch setup as canonical J5?
3. **Q3 — Parity baseline:** design-source ships only `SPEC.md` (no PNG). Adopt the
   **live web surface** as the pixel baseline (both hosts mount the same package) +
   DesignSync spot-checks, or commit a reference PNG into `design-source/`?
4. **Q4 — Copy reconcile:** confirm the canonical local-model size — code `4.3 GB`
   (`firstRun.ts:110`) vs SPEC/mock `5.6 GB` (SPEC:22) — so the audit doesn't
   false-flag and SPEC gets updated to match the shipped Q8_0.
5. **Q5 — CI minutes:** macOS-only staged boot is ~15 min (pip-install of three
   services) per run. Confirm weekly + path-filtered + dispatch cadence is acceptable
   (same trade-off `postgres-restore-drill.yml` / the boot drill already make).
6. **Q6 — Theme coverage:** capturing both light and dark per state doubles the
   screenshot set; confirm the parity pass wants both (SPEC "sky-only, no second
   accent" is a both-theme claim) or just the default theme for v1.

---

## Open questions

- Q1: Should the run→stream→handoff tail be greened THROUGH the shipped desktop app (needs a gated real key, a small Ollama model on the runner, or a narrow fail-closed RUNTIME_FAKE_MODEL seam that would force a dev-posture variant), or kept in desktop-supervised-boot-drill.yml as it is now? Recommendation: keep it in the drill — do not weaken the fake-model fail-closed guarantee.
- Q2: J5 cold pre-seed needs the device accountKey (SHA-256 of verified sub, post-P0-hardening) resolved before first launch — add a debug first-run.* IPC read to resolve+seed it, or keep the complete-once-then-relaunch setup as canonical J5?
- Q3: design-source/ has only SPEC.md (no reference PNG). Adopt the live web FTUE render as the pixel baseline (both hosts mount the same chat-surface package) plus DesignSync spot-checks, or commit a canonical mock PNG into design-source/?
- Q4: Confirm the canonical local-model download size — shipped code says 4.3 GB (firstRun.ts:110, Q8_0 real) vs SPEC/mock 5.6 GB (SPEC.md:22) — so the parity audit doesn't false-flag it and SPEC is updated to match.
- Q5: macOS-only staged supervised boot is ~15 min/run (pip-installs three services). Confirm weekly + path-filtered + workflow_dispatch cadence (matching desktop-supervised-boot-drill.yml) is acceptable rather than per-PR.
- Q6: Should the parity screenshot pass capture both light and dark themes per FTUE state (SPEC's 'sky-only, no second accent' is a both-theme claim), or just the default theme for v1?

