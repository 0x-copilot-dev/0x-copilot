# PRD-P8 — Ollama runtime states (first-run local-model card)

Status: **APPROVED — in implementation**
Design source: Claude Design project `ceb081f6-94dd-4c36-abc1-5543ea33cd34`,
file `0xCopilot First Run - Ollama States.html` (`copilot-firstrun-ollama.jsx`).
Supersedes: PRD-P2 §3.4 (hook contract), §9 (card foot), :242-247, :317.

---

## 1. Why

The design ships **five** runtime states for the first-run "Download the local
model" card. The decision-maker dropped **Download failed**, leaving four:

| #   | Design tag           | Card foot                                                           |
| --- | -------------------- | ------------------------------------------------------------------- |
| ①   | Ollama not installed | `Get Ollama ↗` + watch line "download starts once it's detected"    |
| ②   | Ollama installed     | `Start download` + note "type your first prompt while it downloads" |
| ③   | Model downloading    | spinner + "Ollama detected — downloading now" + bar + byte line     |
| ④   | Runtime stopped      | ⚠ "Ollama stopped responding" + `Restart Ollama` + resume line      |

Today **two of the four cannot exist** and **two more are unreachable**:

- `LocalModelsStatus = { enabled, ollama_running, ollama_version }` cannot tell
  ① from ④ — both are `ollama_running: false`
  (`services/ai-backend/src/runtime_api/schemas/local_models.py`).
- There is no start/restart operation in any of the five layers
  (ai-backend → facade → api-types → port → hook). ④ has nothing to call.
- The card unmounts the instant a download starts: `handleStartDownload` sets
  `stage = "dl"`, and the Gate renders only at `stage === "choice"`
  (`FirstRunSurface.tsx:351`, `:419`). ③ and ④ render only in unit tests.
- Consequence (verified): `modelReady = pct === null || pct === 100`; the hook
  never resets `pct` on failure; `useFirstRunLaunch`'s `queued` phase has no
  timeout, no failure input, and refuses re-launch (`useFirstRunLaunch.ts:146`).
  A runtime that dies after the user sends their first prompt hangs them on
  "Queued — starts when the model lands" **permanently**.

## 2. Scope

**In:** the first-run local-model card and everything required to make its four
states real, reachable, and non-dead-ending — across ai-backend, backend-facade,
api-types, chat-surface, and both host binders.

**Out (must not regress):** the Bring-your-own-key card; Settings → Local models
(`LocalModelsPage`), `ModelPill`, `ModelsPage`, `DownloadLocalModelModal`,
`SettingsBinder`, `SettingsMount`, `RunRoute`, `destinationBinders` — all five
existing `ollama_running` readers keep working unchanged. Adopting the richer
state model in Settings is a tracked follow-up, not this change.

## 3. Decisions (locked)

- **D1 — No red terminal state.** Failures are classified server-side.
  `runtime_unreachable` → state ④. `transient` → stay in ③ and auto-resume with
  capped backoff (Ollama keeps partial blobs; nothing is lost). `terminal`
  (disk full, 404 repo, refused) → state ④'s amber shell with swapped copy and a
  `Resume download` action. The red `.dling.err` / danger `ProgressBar` /
  "Couldn't download …" branch is **deleted**.
- **D2 — Runtime control lives in ai-backend, gated.** One HTTP contract serves
  both hosts. A new setting `RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME` (default
  `false`) gates binary detection AND process spawn. Containerised self-host
  (`OLLAMA_BASE_URL` → `host.docker.internal`) leaves it off and therefore
  reports `runtime_state: "unknown"` rather than lying about a host filesystem it
  cannot see. Only `tools/desktop-runtime` + `apps/desktop` set it true.
- **D3 — Additive contract only.** `packages/api-types/CLAUDE.md` classifies a
  new required field as breaking. Every new field is optional; every consumer
  tolerates its absence and falls back to `ollama_running`.
- **D4 — Auto-start does not steal the stage.** A download the user did not ask
  for (auto-started on detection) keeps `stage === "choice"`, so the card stays
  mounted and renders ③/④, and the user keeps a live choice (BYOK is still right
  there). An explicit `Start download` click advances to the composer as today.
  The flow mock (`copilot-firstrun.jsx`) instead auto-advances ~1.4s after
  detection; we deliberately do not — moving the user with no gesture is the
  weaker behaviour and it reduces ③/④ to a one-second flash.
- **D4a — Two deliberate deviations follow from D4**, both recorded here so the
  design-parity report reads them as intent, not drift:
  1. State ③ on the gate gains a primary **"Continue →"** action. The design's ③
     foot has no button because the mock has already moved the user to the
     composer; staying on the gate means the user needs a way forward. It is the
     minimum affordance that makes D4 navigable.
  2. State ③'s note on the gate reads "… · downloading in the background", not
     the design's "… · type your first prompt while it lands". There is no
     composer on the gate, so the design's line would be a lie there. The
     design's wording is used verbatim once the user has advanced (composer/ack).
- **D5 — Header meta stays "4.3 GB".** The mock says 5.6 GB; the frozen value is
  the verified Qwen3-4B Q8_0 size (4,280,404,704 bytes, `data/localModels.ts:78`).
  Honesty beats parity — design-parity will flag it; the divergence is deliberate
  and recorded here.
- **D6 — Only the `.watch` dot is in the card.** The mock's `.ol-tag` dots
  (`.d.off/.on/.warn`) are catalog chrome labelling each comparison column, not
  part of the card. Do not build a card status dot.

## 4. Contract

### 4.1 ai-backend — `runtime_api/schemas/local_models.py`

```python
class LocalRuntimeState(StrEnum):
    UNKNOWN = "unknown"              # cannot determine (remote/containerised)
    NOT_INSTALLED = "not_installed"  # binary absent on this machine
    STOPPED = "stopped"              # binary present, daemon not answering
    RUNNING = "running"

class LocalModelErrorKind(StrEnum):
    RUNTIME_UNREACHABLE = "runtime_unreachable"  # daemon died / refused
    TRANSIENT = "transient"                      # network blip, stream break
    TERMINAL = "terminal"                        # 4xx, disk full, bad repo
```

`LocalModelsStatus` gains (both optional, defaulted):

```python
runtime_state: LocalRuntimeState = LocalRuntimeState.UNKNOWN
runtime_managed: bool = False     # this server may start/restart the runtime
```

`ollama_running` / `ollama_version` are **unchanged and still populated**.

`LocalModelPullEvent` gains `error_kind: LocalModelErrorKind | None = None`.

### 4.2 Derivation (single source of truth, server-side)

```
not enabled                      → UNKNOWN,       running=False
version is not None              → RUNNING
not manage_runtime               → UNKNOWN        # honest: cannot see host FS
binary found on this machine     → STOPPED
otherwise                        → NOT_INSTALLED
```

### 4.3 New route

`POST /v1/local-models/runtime/start` → `LocalModelsStatus`

- 404 when `enable_local_models` is false (existing `_require_enabled`).
- 404 when `manage_runtime` is false (new `_require_runtime_control`, same
  `CONFIGURATION_ERROR` shape — server-authoritative, never client-trust).
- Spawns the runtime detached, polls `running_version()` to a bounded timeout,
  returns the resulting status. Idempotent: already-running is a success.
- Emits an audit event (this is the first local-models route with a side effect
  on the host; the repo's compliance rules require it).

### 4.4 Client mirror — `packages/api-types/src/localModels.ts`

Mirror `LocalRuntimeState`, `LocalModelErrorKind`, the two optional
`LocalModelsStatus` fields, `error_kind`, and the new route in the header
comment. Add a local-models section to the package's own contract docs.

## 5. UI contract — `FirstRunLocalCard`

Header is unchanged in every state (icon / title / meta / body). Only the foot
varies. Class names follow the design (`.fr-dep`, `.acts`, `.watch`, `.ok`,
`.dling`, `.ol-prog`, `.spin`) namespaced into `onboarding.css`; colours resolve
to design-system tokens, never raw hex.

| State                                  | Foot                                                                                                                                                                                                 |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **feature off** (web/cloud)            | unchanged: "Local models run in the desktop app…" note                                                                                                                                               |
| **probing**                            | unchanged: disabled `Start download` + note                                                                                                                                                          |
| **① not_installed / unknown**          | `.fr-dep` → `.acts` → `gbtn gbtn--pri` **"Get Ollama ↗"** (host callback) + `.watch` (6px dot + "download starts once it's detected")                                                                |
| **① → detected**                       | `.fr-dep` → `.ok` (check glyph, success tone) **"Ollama detected — starting your download"**                                                                                                         |
| **② running, model absent**            | `gbtn gbtn--pri` **"Start download"** + `.note` "type your first prompt while it downloads"                                                                                                          |
| **② running, model already installed** | `.fr-dep` → `.ok` "on-device · ready" — no redundant pull                                                                                                                                            |
| **③ downloading**                      | `.fr-dep` → `.dling` (`.spin` + "Ollama detected — downloading now") → `.ol-prog` → `.note` "Qwen 3 4B · 2.4 / 4.3 GB · downloading in the background" → `.acts` → `gbtn gbtn--pri` **"Continue →"** |
| **③ reconnecting**                     | as ③, `.note` swapped to the reconnecting line; bar keeps last value                                                                                                                                 |
| **④ runtime stopped**                  | `.fr-dep` → `.dling.warn` (warn glyph + "Ollama stopped responding") → `.acts` → `gbtn gbtn--pri` **"Restart Ollama"** + `.watch` "download resumes on its own"                                      |
| **④ terminal error**                   | same shell; `.dling.warn` carries the safe server message; action is **"Resume download"**                                                                                                           |

`Restart Ollama` renders only when `runtime_managed` is true. When it is false
(web / containerised), state ④ degrades to the instructional foot — no button
that cannot work.

**Accessibility:** the foot is a `role="status"` `aria-live="polite"` region —
states ①→detected and ③→④ change with no user action and must be announced.
The spinner is gated by the package's existing reduced-motion pattern.

**Copy:** every new string enters `FIRST_RUN_COPY.local` in `firstRun.ts`. No
inline literals in the component.

## 6. Hook contract — `useFirstRunLocalModel`

The flat 5-value status is replaced by orthogonal axes (a runtime state and a
download phase are independent facts and must stop being one union):

```ts
readonly enabled: boolean;
readonly runtime: LocalRuntimeState;
readonly runtimeManaged: boolean;
readonly phase: "probing" | "idle" | "downloading" | "reconnecting" | "ready";
readonly modelInstalled: boolean;      // preset already present before any pull
readonly localModelPct: number | null;
readonly bytesCompleted: number | null;
readonly bytesTotal: number | null;
readonly blocked: { kind: LocalModelErrorKind; message: string } | null;
readonly modelName: string | null;
start(): void;  resume(): void;  restartRuntime(): void;  recheck(): void;
```

- **Polling.** While mounted and `runtime !== "running"`, re-probe on an
  interval, gated by the `PresenceSignal` port (never poll a hidden window).
  Cadence: 3s for the first 2 minutes, then 15s.
- **Auto-start on detection.** Implemented as an **effect keyed on the runtime
  edge**, never by calling `start()` from the probe's `.then` — `start` is a
  `useCallback` closing over `enabled`/`runtime` and a same-tick call reads the
  stale closure and silently no-ops (verified hazard).
- **Already-installed short-circuit.** The probe also calls `port.list()` and
  resolves the preset tag. If present: `modelInstalled = true`, `phase = "ready"`,
  `onReady(tag)` — no pull is issued.
- **Auto-resume.** `runtime_unreachable` → `runtime = "stopped"`, keep
  `localModelPct`, resume automatically once the runtime is running again.
  `transient` → `phase = "reconnecting"`, retry with capped exponential backoff
  (1s → 30s). `terminal` → `blocked` set, no auto-retry, manual `resume()`.
- **`localModelPct` is never frozen into a lie.** Any state that blocks progress
  must be visible to the launch lane (§7).

## 7. Downstream — killing the permanent "Queued" hang

- `useFirstRunLaunch` gains a `modelBlocked` input (derived from `blocked` /
  `runtime === "stopped"`). While queued and blocked, the phase exits to an
  actionable state instead of waiting forever.
- `launch()`'s `phaseRef.current !== "composing"` guard is relaxed so a user
  whose download stalled can re-submit.
- The acknowledgment line stops claiming "starts when the model lands" when the
  model demonstrably is not landing.
- Both host binders derive `modelReady` identically today
  (`FirstRunGate.tsx:264`, `FirstRunSurfaceMount.tsx:127`) — update both.

## 8. Hosts

- **Desktop.** `Get Ollama ↗` needs a main-brokered external open; the renderer
  cannot call `window.open` (denied at `main/index.ts:671`). Add a channel that
  takes **no URL argument** — the destination is a hardcoded constant — so the
  renderer can never ask main to open an arbitrary origin. `Restart Ollama` goes
  through the facade route; no new IPC.
- **Web.** Same card, same port. `runtime_managed` will be false, so no Restart
  button renders. `Get Ollama ↗` is an ordinary external link.

## 9. Verification

- Unit: every state and every transition, both directions, in
  `packages/chat-surface`; new ai-backend tests for derivation, the gate, the
  spawn path, and error classification; facade proxy test for the new route.
- Contract: a test asserting the Pydantic and TypeScript shapes agree (none
  exists today — a silent-drift hole).
- Design-parity: re-vendor the new mock into
  `tools/design-parity/surfaces/first-run/design/`, add per-state anchors, and
  drive the live side into each of the four states.
- Dev reachability: `RUNTIME_ENABLE_LOCAL_MODELS` must be settable in the
  documented local stacks, or no developer can exercise any of this outside a
  staged desktop boot.

## 10. Known limitations (stated, not hidden)

- **The runtime-start audit row is a structured log, not a durable record.**
  `_audit_runtime_start` writes a chained row only when
  `request.app.state.runtime_audit_appender` is set, and nothing in
  `services/ai-backend/src` ever sets it (only `rbac.py` reads it; only tests
  set it). This faithfully copies the existing `rbac.py` precedent, but the
  repo's compliance rules say **not** to call audit logging complete while the
  adapter is a no-op. Per §4.3 the intent is a durable row; today the record of
  account for "who started a host process" is the facade's structured log line.
  Tracked as a follow-up — do not mark this control implemented in a compliance
  review on the strength of §4.3 alone.
- **The ai-backend audit principal degrades to `unknown` on the direct dev
  path.** Through the facade, `service_headers` always sets
  `x-enterprise-org-id` / `x-enterprise-user-id`, so production is attributed.
  A direct dev-only call without those headers is logged as `unknown`.
