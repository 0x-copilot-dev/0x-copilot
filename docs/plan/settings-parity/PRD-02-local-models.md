# PRD-02 — Local models (Ollama)

## 1. Header

- **Title:** Converge web + desktop Settings → Local models onto the chat-surface SSOT page, and wire it to the real Ollama backend end-to-end.
- **One-line summary:** Retire the legacy web `LocalModels.tsx`, mount the design-spec `LocalModelsPage` on **both** hosts through a new `createLocalModelsPort(transport)`, un-stub desktop against the already-shipped `/v1/local-models/*` facade routes, and give "default local model" a real persistence home + round-trip.
- **Status:** Draft.
- **Nav slug:** `local-models` (Settings → Models & keys → Local models).
- **Related files:**
  - SSOT UI: `packages/chat-surface/src/settings/LocalModelsPage.tsx`, `packages/chat-surface/src/settings/DownloadLocalModelModal.tsx`, `packages/chat-surface/src/settings/localModelsFormat.ts`
  - **NEW** port + catalog: `packages/chat-surface/src/settings/data/localModels.ts` (mirrors `data/providerKeys.ts`)
  - Web host: `apps/frontend/src/features/settings/SettingsBinder.tsx` (case `'local-models'`); legacy to retire: `apps/frontend/src/features/settings/sections/LocalModels.tsx`, `apps/frontend/src/api/localModelsApi.ts`
  - Desktop host: `apps/desktop/renderer/SettingsMount.tsx` (`STUB_OLLAMA_STATUS`, `stubStartPull`, case `'local-models'`)
  - Facade: `services/backend-facade/src/backend_facade/local_models_routes.py`, `services/backend-facade/src/backend_facade/me_routes.py`
  - ai-backend: `services/ai-backend/src/runtime_api/http/local_models_routes.py`, `services/ai-backend/src/runtime_api/local_models/{service,ollama_client,hf_metadata}.py`, `services/ai-backend/src/runtime_api/schemas/local_models.py`
  - Default-local persistence: `services/backend/src/backend_app/routes/me_preferences.py`, `services/backend/src/backend_app/identity/me_store.py`
  - Contracts: `packages/api-types/src/localModels.ts`

---

## 2. Problem statement

**User pain.** A solo-desktop BYOK user who wants a fully-private, no-key, offline model expects to open **Settings → Local models**, see what's installed on their machine, download another, and pick a default — the same experience whether they're on the web build or the Electron app. Today that experience is broken on both surfaces in different ways, and it does not match the v3 design on either.

**Engineering reality (the "composer-web-desktop-mismatch" spine, this section's instance of it).** The design SSOT page — `LocalModelsPage` + `DownloadLocalModelModal` in `chat-surface` — is wired to a real backend by **neither** host:

- **Web** renders a _different, legacy_ component. `SettingsBinder.tsx` case `'local-models'` returns `<LocalModels />` from `apps/frontend/src/features/settings/sections/LocalModels.tsx` — a free-text "Hugging Face GGUF repo" + "Quantization" form with a raw `<progress>` bar and a "Remove" button. It **is** wired to the real facade via `apps/frontend/src/api/localModelsApi.ts` (`streamLocalModelPull` SSE), but it is not the design, has no "Installed"-card chrome, no jade chip logo, no "default local" chip, and no set-as-default flow.
- **Desktop** mounts the _correct_ design page but on **stubs**. `SettingsMount.tsx` passes `status={STUB_OLLAMA_STATUS}` (`ollama_running: false` → the page is frozen on the setup steps forever), `models={localModels}` where that array is `[]`, `defaultLocalModelName={null}`, `startPull={stubStartPull}` which immediately calls `onError("Local model downloads aren't wired in this build yet.")`, and `onDownloaded`/`onDelete` no-ops. So the design UI has **zero live coverage** — nobody can actually use it.

So the same nav slug renders two different UIs with two different wirings, and the one that matches the design is dead. On top of the mismatch there are three genuine backend/contract gaps: there is **no `createLocalModelsPort`** (the page takes raw props, so each host would hand-wire the transport differently — guaranteeing drift); there is **no persistence anywhere for "default local model"** (`onSetDefault`/`setAsDefault` results are discarded by every caller — no endpoint, no field, no column); and there is **no supplier for the download catalog** (`DownloadLocalModelModal.availableModels` has no source, so the design's "pick from available" step renders "No models available"). The backend routes themselves are fully built and reachable — `RunComposer.tsx` on desktop already fetches `GET /v1/local-models` through the same transport — so this is a wiring + small-contract program, not a from-scratch build.

---

## 3. Goals & non-goals

**Goals**

1. One UI, both hosts: `LocalModelsPage` + `DownloadLocalModelModal` are the only Local-models UI on **web and desktop**; legacy `sections/LocalModels.tsx` is deleted.
2. Real wiring on both hosts via a single **`createLocalModelsPort(transport)`** so web and desktop drive the identical status/list/size/pull-SSE/delete contract.
3. Live coverage on desktop: real Ollama status, installed list (with GPU/CPU placement), download (pull SSE with %/size/speed/ETA), and delete.
4. A real home + round-trip for **"default local model"**: the "default local" chip, "Set default" row action, and the modal's "Use as default local model" toggle all read/write the same persisted value on both hosts.
5. A supplier for the **available-to-download catalog** that satisfies the design's pick-from-available step, plus a power-user escape hatch so we don't regress the legacy free-text repo/quant capability.
6. Honest **graceful degradation** when Ollama is not installed/running and when the feature is disabled server-side, preserving the server-authoritative gate (`/status` always 200; every other route 404s when disabled) and the untrusted-local-daemon boundary.

**Non-goals**

- Bundling, supervising, or auto-starting Ollama from the desktop supervisor (it spawns Postgres + 3 Python services, **not** Ollama). Out of scope; see §11.
- Any change to inference/run execution. `"ollama"` is already a first-class keyless row in `OpenAICompatibleProviders` and unconditionally enabled in the model catalog; downloaded models already appear in the picker. We do not touch that.
- New Ollama capabilities beyond the five existing routes (no per-model config, no fine-tune, no multi-runtime abstraction — Ollama is the single runtime).
- Team/multi-tenant local-model sharing. Local models live on one machine; this is a solo-device feature.

---

## 4. Users & scenarios

**Personas**

- **Solo-desktop BYOK user (primary).** Runs 0xCopilot on their own laptop, privacy-motivated, may have a GPU. Wants offline models with no key. Deployment profile `single_user_desktop`.
- **Self-host web user (secondary).** Same feature over the web build against a self-hosted stack where `RUNTIME_ENABLE_LOCAL_MODELS` is on and Ollama runs on the same host.
- **Cloud/multi-tenant user (degraded).** Feature is disabled server-side; must never see install instructions or a broken card.
- **Team admin** — _not relevant here_: local models are per-device, not org policy. (Team admin appears only insofar as `local-models` stays a non-admin section under `Models & keys` for every profile.)

**Scenarios**

1. **Ollama not installed (desktop).** User opens Settings → Local models. The port probes `/status`; `ollama_running:false`. The page shows the "Install Ollama to get started" card with the three setup steps and a **Re-check** button. User installs from `ollama.com/download`, launches it, clicks Re-check → the installed list appears. _(Today: desktop is frozen here forever regardless of reality.)_
2. **First download (both hosts).** Ollama running, no models. User clicks **Get another model** → `DownloadLocalModelModal` opens on the **pick** step showing the curated catalog (DeepSeek-R1 32B, Mistral Small 3 24B, Phi-4 14B, Gemma 3 27B, …). User picks one → **progress** step streams % / size / speed / ETA off the pull SSE → **ready** step: "Ready to run locally." + a "Use as default local model" toggle (default on) → **Finish**. The list refreshes; the new model shows the sky **default local** chip.
3. **Set an existing model as default (both hosts).** User has two installed models; clicks **Set default** on the second row → the "default local" chip moves to it and persists; reload → the chip is still there; the desktop ⌘⇧M local-model picker and Model & behavior's "Local · your machine" lane resolve to it.
4. **Power-user side-load (both hosts).** A model not in the curated catalog: user opens the modal, expands **"Paste a Hugging Face repo"**, enters `bartowski/…-GGUF` + quant, and pulls — the same free-text capability the legacy web form had, preserved inside the design flow.
5. **Feature disabled (cloud).** `RUNTIME_ENABLE_LOCAL_MODELS` unset. `/status` returns `enabled:false`; the host drops the `local-models` nav item entirely (no install steps, no broken card).

---

## 5. Current state

| Capability                                                                                                                    | State                         | Evidence                                                                                                                                                                                                  |
| ----------------------------------------------------------------------------------------------------------------------------- | ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/v1/local-models/status` (capability probe, always 200)                                                                      | **Built & live**              | `services/ai-backend/src/runtime_api/http/local_models_routes.py:76`; facade `local_models_routes.py:28`                                                                                                  |
| `GET /v1/local-models` (installed list + GPU/CPU placement)                                                                   | **Built & live**              | `runtime_api/local_models/service.py:53`; facade `:80`; already consumed by desktop `apps/desktop/renderer/composer/RunComposer.tsx:209`                                                                  |
| `GET /v1/local-models/size?repo=&quant=`                                                                                      | **Built & live**              | routes `:87`; service `:72`                                                                                                                                                                               |
| `GET /v1/local-models/pull?repo=&quant=` (SSE `local_model_pull`)                                                             | **Built & live**              | routes `:98` (registered `methods=["GET"]`, `:63`); service `:75`; facade byte-for-byte SSE proxy `:42`                                                                                                   |
| `DELETE /v1/local-models/{name:path}`                                                                                         | **Built & live**              | routes `:112`; facade `:84`                                                                                                                                                                               |
| Server-authoritative gate (`/status` 200; others 404 when disabled)                                                           | **Built & live**              | `_require_enabled` → `RuntimeApiError(…404)` `routes.py:164`; gated on `settings.execution.enable_local_models`                                                                                           |
| `ollama` as keyless execution provider (downloads appear in picker)                                                           | **Built & live**              | `OpenAICompatibleProviders.get("ollama")` `routes.py:180`; `RunComposer.tsx` local list                                                                                                                   |
| api-types contract                                                                                                            | **Built & live**              | `packages/api-types/src/localModels.ts` (`LocalModelsStatus`, `LocalModelSummary`, `LocalModelsListResponse`, `LocalModelSize`, `LocalModelPullEvent`, `PullLocalModelRequest`, `LocalModelRunPlacement`) |
| SSOT design page + modal (presentational, 4 states, jade chip, default chip, set-default, privacy note, 3-step download flow) | **Built, unwired**            | `packages/chat-surface/src/settings/LocalModelsPage.tsx`, `DownloadLocalModelModal.tsx` — take raw props/callbacks                                                                                        |
| **Web** host wiring                                                                                                           | **Mismatch (legacy, live)**   | `SettingsBinder.tsx:207` renders `<LocalModels />` (`sections/LocalModels.tsx`) — free-text form, wired to real facade via `api/localModelsApi.ts`, but NOT the design                                    |
| **Desktop** host wiring                                                                                                       | **Stubbed (design, dead)**    | `SettingsMount.tsx:703` mounts `LocalModelsPage` with `STUB_OLLAMA_STATUS` (`:148`), `models=[]`, `stubStartPull` that throws (`:249`), no-op delete/downloaded, `defaultLocalModelName=null`             |
| `createLocalModelsPort(transport)`                                                                                            | **Missing**                   | no equivalent of `createProviderKeysPort` / `createModelsPort` for local models                                                                                                                           |
| "Default local model" persistence                                                                                             | **Missing entirely**          | no endpoint, no api-types field, no store column; `onSetDefault` / `setAsDefault` discarded by every host                                                                                                 |
| Available-to-download catalog supplier                                                                                        | **Missing**                   | `DownloadLocalModelModal.availableModels` has no source; empty → PickStep renders "No models available to download right now."                                                                            |
| Ollama lifecycle mgmt (bundle / supervise / auto-start)                                                                       | **Missing (by design today)** | supervisor spawns PG + 3 services only; Ollama assumed pre-installed; only a passive `/api/version` probe                                                                                                 |

**Two correctness deltas hiding in the SSOT page today** (see §6):

- The page keys its state machine only on `status.ollama_running`, never on `status.enabled`. When `enabled:false` (deployment disabled the feature) the service does not probe Ollama, so `ollama_running` is also `false` and the page tells a cloud user to "Install Ollama" — wrong. `enabled:false` must be a distinct, host-level "not available here → hide the section" state.
- `onDelete(name)` fires with no confirmation; deleting silently discards a multi-GB download.

---

## 6. Design & UX specification

The target is the v3 "0xCopilot App" design under the v2 "quiet" system. Both hosts render the identical `chat-surface` components, so **cross-host parity is automatic once both mount the SSOT page** — the work is (a) removing the legacy web divergence, (b) feeding both pages real data, and (c) closing the two correctness deltas above. Every color/size resolves to a `design-system` token (`--color-*`, `--font-*`, `--radius-*`, `--space-*`); no hard-coded hex or px font-sizes (the existing pages already comply — keep them compliant).

### 6.1 Section header

- **Title:** "Local models"
- **Description:** "Run a model entirely on this machine — no key, no network, nothing leaves your box."
  _(Wire via the settings section header slot; the legacy web copy — "Download an open model from Hugging Face…" — is retired with the component.)_

### 6.2 Page states (FR-5.13/5.14 already encoded in `LocalModelsPage`)

1. **Loading** — `status === null`: `data-testid="local-models-loading"` "Checking the local runtime…".
2. **Load-error** — `loadError` set (status/list probe failed): a danger `SetNote role="alert"` + secondary **Retry** (`data-testid="local-models-retry"` → `onRecheck`).
3. **Ollama not running** — `status.ollama_running === false` (**and** `status.enabled === true`, per the new gate below): the "Install Ollama to get started" `SetCard` with the three-step `<ol>` (install from `ollama.com/download`; launch it; re-check) + secondary **Re-check** (`onRecheck`).
4. **Ollama running** — the "Local models" card: `SecHead` "Installed", then either an empty `SetNote` ("No local models yet. Download one above.") or the `<ul data-testid="local-models-list">` of installed rows; a **Get another model** secondary action opens the download modal.

**NEW distinct state — feature disabled.** When `status.enabled === false`, the section must **not** render install steps. Preferred behavior: the **host drops the `local-models` nav slug** so the section is never reachable (§7 FR-3). Fallback in-page state (if the slug is shown): a neutral `SetNote` "Local models aren't available on this deployment." with no install steps and no Re-check. The privacy note is suppressed in this state.

### 6.3 Installed row (`.krow`) — parity spec

- **Logo:** jade `ChipLogo` (▦ glyph, `--color-success` on `--color-success-bg`, `--radius-sm`) — the one place a model logo is tinted, per design "jade chip logo".
- **Name:** `{model.name}`, followed by a **`Badge tone="success"`** reading **"default local"** (`data-testid="local-models-default-chip"`) **iff** `model.name === defaultLocalModelName`.
  _(Design copy says "default local" chip; the design mock also renders it "sky" while the code uses `tone="success"`/jade. Resolution: keep `tone="success"` — jade is the design system's live/default semantic and the chip means "this is the active local default." This is a deliberate, documented reconciliation, not a bug. See Open Decision D5.)_
- **Sub (JetBrains Mono metadata):** `·`-joined `parameter_size · {size} on disk · quantization · {placement}` where placement is `GPU` / `CPU — slower` / `GPU + CPU — slower` (`placementLabel` in `localModelsFormat.ts`).
- **Actions:** **Run** (secondary, `aria-label="Run {name}"`) · **Set default** (ghost, shown only when `onSetDefault` given and not already default, `aria-label="Set {name} as default local model"`) · **Delete** (danger/ember, `aria-label="Delete {name}"`).

### 6.4 "Get another model" (`.frow`) + download modal

- Card action **Get another model** (design label "Download a model"; the SSOT card uses "Get another model" as the card action and "Download a local model" as the modal title — keep these).
- **`DownloadLocalModelModal`** — title "Download a local model", subtitle "Runs on your machine via Ollama", 3 `StepDots`:
  1. **pick** — the curated catalog list; each row: name (`{name}`), mono sub `parameterSize · {size} · note`, a `↓` glyph; clicking begins the pull. **Empty-state copy today:** "No models available to download right now. Check your local runtime is reachable and try again." — this must not be the normal path (§7 FR-6 supplies a catalog).
  2. **progress** — `ProgressBar` (% from `bytes_completed/bytes_total`, falling back to the size hint), a status line (`humanStatus`) + `{size} · {speed}/s · {eta} left`. On error: danger `ProgressBar` + `SetNote role="alert"` "Couldn't download {name}: {error} Retry, or go back…", with **Back** and **Retry** footer actions.
  3. **ready** — `SetNote` ✓ "**Ready to run locally.** {name} is installed and appears in your model picker." + a `Frow` "Use as default local model" / hint "New runs that pick a local model will use this one." with a `Toggle` (default on) → **Finish**.
- **Design's Ready-step sub copy** ("picked when you choose 'Local' in Model & behavior") is captured by the existing hint "New runs that pick a local model will use this one." — keep the existing hint (it is clearer and already ties to Model & behavior's "Local · your machine" lane).

### 6.5 Privacy note (always, except the disabled state)

🔒 "Powered by your local runtime (Ollama). Inference uses your GPU/CPU — private and offline." (`data-testid="local-models-privacy-note"`).

### 6.6 Delete confirmation (NEW)

Delete removes a multi-GB artifact and requires re-download. **Add a lightweight confirm** before calling the port's delete: an ember-toned confirm ("Remove {name}? You'll need to download it again to use it locally." / **Remove** · **Cancel**). Implemented as a small confirm state inside `LocalModelsPage` (substrate-agnostic; no `window.confirm`). See Open Decision D6.

### 6.7 Microcopy, empty/loading/error, accessibility

- All strings above are the canonical copy. Do not invent per-host variants (both hosts render the same component).
- **Roles/focus:** load-error and pull-error notes use `role="alert"`; the modal is a focus-trapped `<Modal>` (existing); the pick rows and all actions are real `<button>`s with `aria-label`s already present; the setup steps are an `<ol>`; the confirm (§6.6) traps focus and returns it to the Delete trigger on close.
- **Reduce-motion:** the `ProgressBar` and any modal transitions respect `--duration-*`/`--ease-*` tokens, which the appearance layer already zeroes under `data-reduce-motion`. No new animation is introduced.
- **Live regions:** progress % updates should be announced sparingly — mark the progress status line `aria-live="polite"` (add to `ProgressStep`) so screen readers hear "Downloading… / Ready" without spamming every byte frame.

### 6.8 Enumerated parity deltas to fix

- **D-A (web mismatch):** web renders legacy free-text `sections/LocalModels.tsx` instead of the SSOT page. → Mount `LocalModelsPage`; delete the legacy file + its CSS classes (`.local-models-*` in `apps/frontend/src/styles.css`) + `api/localModelsApi.ts` (folded into the port).
- **D-B (desktop dead):** desktop mounts the SSOT page on stubs. → Replace `STUB_OLLAMA_STATUS`/`stubStartPull`/no-ops with the real port.
- **D-C (default chip never shows):** `defaultLocalModelName` is always `null`/absent on both hosts. → Wire real persistence (§7 FR-4).
- **D-D (catalog empty):** `availableModels` unsupplied → "No models available". → Supply a catalog (§7 FR-6).
- **D-E (enabled≠running conflation):** `enabled:false` shows install steps. → New disabled state / nav gate (§6.2, FR-3).
- **D-F (no delete confirm):** → §6.6.
- **D-G (free-text lost):** the design pick-only flow drops the legacy free-text repo/quant capability. → Preserve via a modal "Paste a Hugging Face repo" affordance (FR-7).

---

## 7. Functional requirements

> Layer legend: **CS** = `chat-surface` page/port · **WB** = web binder (`SettingsBinder.tsx`) · **DB** = desktop binder (`SettingsMount.tsx`) · **F** = facade · **BE-AI** = ai-backend · **BE** = backend · **AT** = api-types.

**FR-1 — Introduce `createLocalModelsPort(transport)` (CS, AT).**
Add `packages/chat-surface/src/settings/data/localModels.ts` exporting `createLocalModelsPort(transport: Transport): LocalModelsPort`, mirroring `data/providerKeys.ts`. The port method set:

- `status(): Promise<LocalModelsStatus>` → `transport.request({method:"GET", path:"/v1/local-models/status"})`
- `list(): Promise<LocalModelsListResponse>` → `GET /v1/local-models`
- `size(repo, quant): Promise<LocalModelSize>` → `GET /v1/local-models/size`
- `startPull(request, handlers): LocalModelPullHandle` → `transport.subscribeServerSentEvents({ path:"/v1/local-models/pull", query:{repo,quant}, eventName:"local_model_pull", onMessage, onError })`, parsing/validating each frame to `LocalModelPullEvent` (reuse the guard logic from `api/localModelsApi.ts` `isLocalModelPullEvent`) and mapping to the modal's `LocalModelPullHandlers`; the returned `close()` calls `subscription.close()`.
- `delete(name): Promise<void>` → `DELETE /v1/local-models/{encodeURIComponent(name)}`
- `getDefault(): Promise<string | null>` and `setDefault(name): Promise<void>` (FR-4).
  Export it from `packages/chat-surface/src/index.ts` and `settings/index.ts`. The port uses **only** the `Transport` port (`request` + `subscribeServerSentEvents` from `@0x-copilot/chat-transport`) — no bare `fetch`/`EventSource` — so it is substrate-legal and can live in the package (exactly as `createProviderKeysPort`/`createModelsPort` already do). The host's Transport implementation owns the raw SSE/EventSource.

**FR-2 — Mount `LocalModelsPage` on both hosts via the port; retire legacy web (CS, WB, DB).**

- WB: `SettingsBinder.tsx` case `'local-models'` renders `<LocalModelsPage …/>` bound to `createLocalModelsPort(transport)`; delete the `import { LocalModels }` and the `sections/LocalModels.tsx` file, the `api/localModelsApi.ts` file, and the `.local-models-*` CSS.
- DB: `SettingsMount.tsx` case `'local-models'` replaces `STUB_OLLAMA_STATUS`, `stubStartPull`, `models=[]`, and the no-op handlers with the port. Delete `STUB_OLLAMA_STATUS` and `stubStartPull`.
- Both binders wire the **same** callback set: `status`, `models`, `availableModels`, `defaultLocalModelName`, `loadError`, `onRecheck`, `onDownloaded`, `startPull`, `onDelete`, `onRun`, `onSetDefault`. Because `apps/* → apps/*` imports are banned, the two binders duplicate the projection intentionally and **must stay in lockstep** (NFR-2). A shared `chat-surface` unit test (`LocalModelsPage`/port) plus a parity test asserting both binders pass the identical prop set pins the lockstep.

**FR-3 — Server-authoritative gate → nav visibility (CS, WB, DB, BE-AI unchanged).**
Preserve the existing gate: `/status` always 200; all other routes 404 when `enable_local_models` is off (`local_models_routes.py:164`). At Settings open, each host fetches `/v1/local-models/status` once. If `status.enabled === false`, the host **omits** the `local-models` slug from the rendered Settings nav (via the section-visibility seam feeding `settingsNav`), so no install steps or broken card appear. If `enabled === true`, the section renders and the page's state machine runs on `ollama_running`. As a defense-in-depth in-page fallback, `LocalModelsPage` gains a `status.enabled === false` branch (neutral "not available on this deployment" note, no steps, no privacy note). No client trust: the host never renders live-list data unless the server said `enabled` and the list call succeeds.

**FR-4 — "Default local model" persistence + round-trip (AT, F, BE, CS, WB, DB).**
Add a real, per-user persisted value for the default local model and thread it through the chip + set-default + modal toggle:

- **Home (recommended): `/v1/me/preferences`** — the per-user namespaced JSONB KV in `backend` (`routes/me_preferences.py` + `identity/me_store.py`, `extra='forbid'`, depth-2 merge, deployment-default materialization). Add a top-level `local_models` block with `default_model_name: string | null` (one Pydantic field; JSONB, no migration — matches the file's "add a field, no migration" doctrine). See Open Decision D1 for why not `workspace_defaults`.
- **Contract (AT):** extend the preferences type in `api-types` with `local_models?: { default_model_name: string | null }`; keep `localModels.ts` as-is (the default is a _user preference_, not a local-models route payload).
- **Read path:** the port's `getDefault()` reads `GET /v1/me/preferences` and returns `local_models.default_model_name`; the host passes it as `defaultLocalModelName`. Chip renders when it matches an installed row.
- **Write path:** `onSetDefault(name)` and the modal's `onFinish({setAsDefault:true})` call the port's `setDefault(name)` → `PUT /v1/me/preferences` `{local_models:{default_model_name:name}}` (depth-2 merge). On success the host re-reads (or optimistically sets) `defaultLocalModelName` and toasts "{name} is now your default local model."
- **Consumers:** the desktop ⌘⇧M local-model picker and Model & behavior's "Local · your machine" lane resolve their default from the same preferences value (follow-up wiring; this PRD makes the value authoritative and read/written).
- **Integrity:** setting a default for a model the user later deletes must not dangle — on delete, if the deleted name equals the stored default, the host clears it (`PUT {local_models:{default_model_name:null}}`). The chip simply won't render for a missing name regardless.

**FR-5 — Live status / list / delete on both hosts (CS, WB, DB).**
On section open: `port.status()`. If `enabled && ollama_running`, `port.list()` populates `models`; failures set `loadError` (mapped from typed transport errors). **Re-check**/**Retry** re-run status(+list). **Delete** (after the §6.6 confirm) calls `port.delete(name)`, then re-lists and clears a dangling default (FR-4). All four page states (loading / load-error / not-running / running) are driven by real responses — no stubs.

**FR-6 — Available-to-download catalog supplier (CS, WB, DB).**
Supply `availableModels` to the modal. **Recommended:** a **static curated catalog as pure data in `chat-surface`** — `export const LOCAL_AVAILABLE_MODELS: readonly AvailableLocalModel[]` in `settings/data/localModels.ts`, imported by **both** binders (pure data, no substrate → one list, no lockstep duplication). Seed it from the design's illustrative set mapped to real HF GGUF repos + a default quant + a `sizeBytes` heads-up and a short `note` (e.g. DeepSeek-R1 32B, Mistral Small 3 24B, Phi-4 14B, Gemma 3 27B). The heads-up `sizeBytes` gives the progress bar a denominator before the first byte frame; the authoritative size still comes from `GET /size` at pull-start if we choose to pre-check. See Open Decision D2 for static-list vs backend-catalog.

**FR-7 — Preserve power-user free-text side-load (CS).**
Add an **"Paste a Hugging Face repo"** affordance to `DownloadLocalModelModal`'s pick step (a collapsible row/section below the catalog): two inputs `repo` (placeholder `vendor/repo-GGUF`) + `quant` (default `Q4_K_M`) + a **Download** button that calls the same `startPull({repo,quant})` path. This preserves the exact capability of the retired legacy web form inside the design flow, so no power-user regresses. Optionally pre-call `port.size(repo,quant)` to surface "Model not found." before streaming (legacy behavior). This lives entirely in the presentational component (repo/quant are plain props → `startPull`); no substrate concern.

**FR-8 — Pull SSE lifecycle correctness (CS, WB, DB).**
The modal already tears down the stream on unmount/close and on `done`/`error` (`DownloadLocalModelModal.tsx:214-231`). The port's `startPull` must return a `close()` that aborts the underlying `SseSubscription`, and the host must not hold a second reference. A pull in flight when the modal closes or the section unmounts is cancelled (no orphan gigabyte download attached to a dead component). On desktop this rides the IPC→facade SSE lane the RunComposer already uses.

**FR-9 — `onRun` behavior, defined identically on both hosts (CS, WB, DB).**
`onRun(name)` selects the model for the next run: the host sets it as the composer's active model and navigates to the Run destination with that model preselected. Both hosts implement the same effect (see Open Decision D4 for the exact seam; default: select + navigate). `onRun` remains optional in the component contract but **both** binders must supply it (lockstep).

**FR-10 — Untrusted-daemon boundary preserved (BE-AI, CS).**
Ollama is a local HTTP daemon whose `/api/tags`, `/api/ps`, `/api/pull` output is untrusted. It is already validated server-side into typed Pydantic schemas (`service.py`), and public errors are mapped to safe messages (`LocalModelError.public_message`, 502 `EXTERNAL_SERVICE_ERROR`). The client treats `name`, sizes, and status strings as display-only (already the case); model names are path-encoded on delete (`{name:path}` server-side; `encodeURIComponent` client-side) to allow `/` and `:` in `hf.co/...:quant` tags. No new trust is extended to the daemon.

---

## 8. Non-functional requirements

**NFR-1 — Security / BYOK-adjacent.** Local models are keyless; no secret travels here. The default-local write carries only a model _name_ (an Ollama tag), never a credential. Nothing local-model-related is logged with payloads; the untrusted-daemon boundary (NFR/FR-10) holds. No plaintext BYOK invariant is touched.

**NFR-2 — Architectural boundaries / SSOT / cross-host lockstep.**

- `chat-surface` stays substrate-agnostic: the new port uses only the `Transport` port; eslint `no-restricted-globals`/`no-restricted-imports` must still pass (no `window`/`fetch`/`EventSource`/`localStorage`, no `apps/*` import). Raw SSE lives in each host's Transport impl.
- The two host binders **cannot** share code and MUST pass the identical prop/callback set; changing the page contract updates **both** in the same PR. A test asserts both binders wire the same keys.
- Apps call the facade only (`:8200`); never `:8100`/`:8000` directly.

**NFR-3 — Performance.** `/status` is a cheap probe (already always-200, no Ollama call when disabled). List calls `/api/tags` + `/api/ps` once per open/recheck. The pull SSE streams incremental frames; the client updates a single progress reducer per frame and must not re-render the whole list during a pull. Catalog is static (no network).

**NFR-4 — Accessibility.** Meets §6.7: `role="alert"` on error notes, `aria-live="polite"` on progress status, focus-trapped modal + confirm, real buttons with `aria-label`s, reduce-motion honored via tokens.

**NFR-5 — Telemetry/audit.** No new audit rows required for a solo-device convenience, **except** the default-local write, which persists to `/v1/me/preferences` — reuse whatever preference-change audit that route already emits (`IdentityAuditEventRecord` is imported there); do not add a bespoke audit path. Pull/delete are local device actions; do not emit product audit for them.

**NFR-6 — Graceful degradation (fail-open vs fail-closed).**

- Feature-gate: **fail-closed** (server 404s; host hides the section). Never client-trust an enabled flag.
- Ollama-not-running: **fail-open to guidance** (setup steps + Re-check), never a hard error or a fake list.
- Pull/list/delete transport errors: **fail-visible** (typed error → `loadError`/modal error note + Retry), never a silent swallow. A dropped/malformed SSE frame is ignored without tearing the stream (mirror `api/localModelsApi.ts` behavior).

**NFR-7 — i18n / token discipline.** No hard-coded hex or px font-sizes in section/port code; all colors/spacing resolve to `design-system` tokens. Copy is centralized in the SSOT components (single translation site). The chip's jade semantic resolves to `--color-success*` tokens (D5). This PRD does not introduce the terse `--ink/--tx/--sky/--r` token names; it consumes the existing `--color-*` set (the token-name parity question is cross-cutting and owned by the program overview — this PRD's stance: **consume existing `--color-*` tokens, add none**).

**NFR-8 — Tests.** Unit: port method mapping (status/list/size/pull-frame-parse/delete/getDefault/setDefault) with a fake Transport; page state machine incl. the new `enabled:false` and delete-confirm states; modal free-text path. Cross-host: both binders pass identical props. Contract: api-types typecheck for the preferences extension. Backend: preferences round-trip for `local_models.default_model_name` (present/absent/null, depth-2 merge). No production secrets or a live Ollama required in CI (fake the daemon / fake the Transport).

---

## 9. Backend wiring & services required

Almost everything exists; the only **new** backend surface is the default-local preference field.

| Method + Path                                                     | Layer     | Contract (api-types)                                     | Status                 |
| ----------------------------------------------------------------- | --------- | -------------------------------------------------------- | ---------------------- |
| `GET /v1/local-models/status`                                     | F → BE-AI | `LocalModelsStatus`                                      | **EXISTING**           |
| `GET /v1/local-models`                                            | F → BE-AI | `LocalModelsListResponse`                                | **EXISTING**           |
| `GET /v1/local-models/size?repo=&quant=`                          | F → BE-AI | `LocalModelSize`                                         | **EXISTING**           |
| `GET /v1/local-models/pull?repo=&quant=` (SSE `local_model_pull`) | F → BE-AI | `LocalModelPullEvent` frames                             | **EXISTING**           |
| `DELETE /v1/local-models/{name:path}`                             | F → BE-AI | 204                                                      | **EXISTING**           |
| `GET /v1/me/preferences`                                          | F → BE    | preferences blob incl. `local_models.default_model_name` | **EXTEND** (add field) |
| `PUT /v1/me/preferences` (depth-2 merge)                          | F → BE    | `{local_models:{default_model_name}}`                    | **EXTEND** (add field) |

**New/changed backend work**

- `services/backend/src/backend_app/routes/me_preferences.py` — add a `local_models` Pydantic model (`default_model_name: str | None`, `extra='forbid'`) to the canonical preferences shape; materialize a default (`null`) when the row is absent, matching the file's hydration doctrine. Depth-2 merge already supports `{local_models:{default_model_name:…}}`.
- `services/backend/src/backend_app/identity/me_store.py` — `UserPreferencesRecord` gains the `local_models` block (JSONB; no schema migration).
- `packages/api-types` — extend the preferences response/request type with the optional `local_models` block (optional addition on a payload the server already tolerates → non-breaking).
- **No** changes to `local_models_routes.py`, `service.py`, `ollama_client.py`, `hf_metadata.py`, `schemas/local_models.py`, or the facade `local_models_routes.py` — the five routes and their SSE proxy already satisfy FR-1/FR-5/FR-8/FR-10.

**Enforcement points**

- Feature gate: `_require_enabled` (`local_models_routes.py:164`) — unchanged; hosts additionally hide the nav on `enabled:false`.
- RBAC: routes require `RUNTIME_USE`; preferences require `RUNTIME_USE` (already). Identity is bearer-derived at the facade; no identity query params.

---

## 10. Acceptance criteria

**Visual parity (BOTH hosts)**

- [ ] Web and desktop render the **same** `LocalModelsPage` + `DownloadLocalModelModal`; `apps/frontend/src/features/settings/sections/LocalModels.tsx` and `apps/frontend/src/api/localModelsApi.ts` are deleted; no `.local-models-*` CSS remains.
- [ ] Section header reads "Local models" / "Run a model entirely on this machine — no key, no network, nothing leaves your box."
- [ ] Installed rows show the jade chip logo, `name`, mono sub, and Run/Set-default/Delete; the sky/jade **"default local"** chip renders on exactly the default row.
- [ ] Privacy note renders in running & not-running states; absent in the disabled state.
- [ ] Download modal: pick → progress (%/size/speed/ETA) → ready ("Ready to run locally." + toggle) → Finish, 3 StepDots.

**Cross-host equivalence**

- [ ] With the same backend, web and desktop produce byte-identical page markup/copy/states (a snapshot/DOM test in each host, or a shared component test + a binder-prop parity test).
- [ ] Both binders supply the identical callback set (`status,models,availableModels,defaultLocalModelName,loadError,onRecheck,onDownloaded,startPull,onDelete,onRun,onSetDefault`).

**Live coverage & persistence round-trips**

- [ ] Desktop no longer uses `STUB_OLLAMA_STATUS`/`stubStartPull`; real status/list/pull/delete work against a running Ollama.
- [ ] Downloading a catalog model streams real progress and, on Finish with the toggle on, sets it as default; reload → the "default local" chip persists on that model (both hosts).
- [ ] "Set default" on another installed row moves the chip and persists (`GET /v1/me/preferences` reflects it).
- [ ] Deleting the current default clears the stored default (no dangling name).
- [ ] Free-text "Paste a Hugging Face repo" pulls an off-catalog GGUF successfully.

**Gating & degradation**

- [ ] `enable_local_models` off → `/status` returns `enabled:false`, every other route 404s, and the host hides the `local-models` nav slug (no install steps shown).
- [ ] Ollama not running (feature on) → setup steps + Re-check; launching Ollama + Re-check shows the list.
- [ ] Transport/list/pull errors surface a visible error + Retry; a malformed SSE frame does not tear the stream.

**Security & boundaries**

- [ ] `chat-surface` eslint passes (no `window`/`fetch`/`EventSource`/`localStorage`, no `apps/*` import); the port uses only the Transport port.
- [ ] No default write carries anything but a model name; no secret is introduced.
- [ ] Apps call only the facade.

**Tests required**

- [ ] chat-surface: port mapping (fake Transport), page states incl. `enabled:false` + delete-confirm, modal free-text path.
- [ ] both hosts: binder-prop parity test.
- [ ] backend: preferences `local_models.default_model_name` round-trip (present/absent/null/merge).
- [ ] api-types + frontend + desktop typecheck/build green.

---

## 11. Open decisions

**D1 — Home for "default local model."** Options: (a) `/v1/me/preferences` per-user JSONB KV; (b) `workspace_defaults.enabled_models`/a new field (ORG-scoped, ai-backend); (c) `KeyValueStore` port (device-local, unpersisted server-side).
**Recommended: (a) `/v1/me/preferences`.** The default local model is a **per-user, per-device opinion** — the models physically live on this machine and are not org policy. `workspace_defaults` is ORG-scoped and about cloud-catalog curation (`enabled_models`) + the overall `default_model`; stuffing a machine-specific Ollama tag there is a category error and would leak one device's default to a whole org. `/v1/me/preferences` already gives us `extra='forbid'`, depth-2 merge, deployment-default materialization, and "add one field, no migration." (c) is too weak — it wouldn't survive reinstall or sync to the picker/Model-&-behavior consumers server-side. Note the clean relationship to `workspace_defaults.default_model`: choosing a local model as the _overall_ default is a separate action in **Model & behavior** (provider `ollama`); the "default local" chip reflects the _local-lane_ default in preferences.

**D2 — Available-to-download catalog source.** Options: (a) static curated list as pure data in `chat-surface` (one list, both hosts); (b) a backend catalog endpoint (`GET /v1/local-models/catalog`); (c) keep free-text only (no curated list).
**Recommended: (a) static curated + (D7) free-text escape hatch.** The design wants pick-from-available; the backend already accepts arbitrary `repo+quant`, so the catalog is a _convenience layer_, not a constraint. A static list co-located as pure data avoids a new route/store/maintenance surface for a solo desktop and — being substrate-free — is imported by both binders without violating the `apps/*→apps/*` ban. Promote to (b) only if we need remote-updatable curation or per-deployment lists; that's a future PRD. (c) regresses the design.

**D3 — Ollama lifecycle stance.** Options: (a) keep "assumed pre-installed + setup steps + passive probe" (today); (b) auto-detect + offer a guided install; (c) bundle/supervise an Ollama binary in the desktop supervisor.
**Recommended: (a).** Bundling Ollama is a large, platform-specific, license-and-GPU-detection burden with model-storage implications, and the supervisor deliberately spawns only Postgres + the three Python services. The honest setup steps + Re-check are good UX and match the shipped backend. Revisit (c) as its own distribution PRD if telemetry shows install friction dominates drop-off.

**D4 — `onRun` semantics.** Options: (a) select the model as the composer's active model + navigate to Run; (b) set it as default local + toast only; (c) omit the Run action for now.
**Recommended: (a).** "Run" implies "use it now." Both hosts set the model on the composer seam and navigate to the Run destination. If the composer-active-model seam is not trivially reachable from Settings on one host, fall back to (b) uniformly (never diverge per host).

**D5 — "default local" chip color.** Design mock shows sky; SSOT code uses jade (`tone="success"`). **Recommended: keep jade.** Jade is the design system's live/default/success semantic ("this is your active local default"); sky is the single generic accent. Keeping `tone="success"` is a deliberate, documented reconciliation and avoids introducing a bespoke chip variant. (If the design owner insists on sky, it's a one-line `tone` change — cheap either way.)

**D6 — Delete confirmation.** Options: (a) lightweight in-page ember confirm; (b) delete immediately (legacy behavior); (c) full modal.
**Recommended: (a).** Deleting discards a multi-GB download requiring re-download; a one-tap confirm is warranted, but a full modal is heavy. Implement as a small confirm state inside `LocalModelsPage` (no `window.confirm` — substrate rule).

**D7 — Where the free-text escape hatch lives.** Options: (a) inside `DownloadLocalModelModal` as a collapsible "Paste a Hugging Face repo" section; (b) a separate advanced page; (c) drop it.
**Recommended: (a).** Keeps one flow, preserves the exact legacy capability, and stays presentational (repo/quant → `startPull`). (c) regresses power users who relied on the free-text form.

---

## 12. Rollout & sequencing

Small, independently shippable slices; each is green on its own.

- **PR-1 (CS, AT): the port + catalog.** Add `settings/data/localModels.ts` (`createLocalModelsPort`, `LOCAL_AVAILABLE_MODELS`), barrel exports, and unit tests (fake Transport: status/list/size/pull-frame-parse/delete). No host change yet. _Risk: none (dead code until mounted)._
- **PR-2 (WB): converge web onto the SSOT page.** Swap `SettingsBinder` case to `LocalModelsPage` bound to the port; delete `sections/LocalModels.tsx`, `api/localModelsApi.ts`, `.local-models-*` CSS. Web goes from legacy→design, wired to the same live facade it already used. _Risk: web parity regression — pin with a DOM/snapshot test; the facade contract is unchanged._
- **PR-3 (DB): un-stub desktop.** Replace `STUB_OLLAMA_STATUS`/`stubStartPull`/no-ops with the port; delete the stubs. Desktop goes from dead→live. Add the binder-prop parity test (PR-2 + PR-3 assert identical wiring). _Risk: desktop IPC→facade SSE — validate against the RunComposer lane that already works._
- **PR-4 (BE, AT, F): default-local persistence.** Add `local_models.default_model_name` to `/v1/me/preferences` (backend model + store + materialization), extend api-types, backend round-trip tests. _Risk: preferences shape churn — `extra='forbid'` + depth-2 merge already tested; add cases._
- **PR-5 (CS, WB, DB): wire the default round-trip + chip + set-default toggle + delete confirm.** `getDefault`/`setDefault` on the port; both binders read/write; the "default local" chip, "Set default" action, modal toggle, and delete-clears-dangling all go live; add the delete confirm and `aria-live` progress. _Risk: consumer wiring (⌘⇧M picker / Model & behavior lane) is a tracked follow-up, not a blocker — the value is authoritative after this PR._
- **PR-6 (CS): free-text escape hatch.** Add "Paste a Hugging Face repo" to the modal + tests. _Risk: minimal; presentational only._

**Test strategy.** Unit-level fakes for the Transport and the Ollama daemon (no live Ollama in CI); shared chat-surface component tests carry the visual/behavioral contract for both hosts; a binder-prop parity test enforces lockstep; backend preference round-trip tests cover FR-4. Manual smoke: one machine with Ollama installed exercises download → default → delete on both the web and desktop builds against the same facade.

**Risk notes.** (1) The web/desktop lockstep is the standing hazard — every page-contract change must touch both binders in one PR; the parity test is the guardrail. (2) The `enabled` vs `ollama_running` conflation is a real correctness bug today (cloud users told to install Ollama) — FR-3 must land no later than PR-2/PR-3 so the converged page never shows install steps on a disabled deployment. (3) Catalog repos/quants can drift out of date; keeping the list small and pairing it with the free-text hatch bounds the blast radius. (4) Ollama remains an untrusted local daemon; all trust stays server-side (FR-10) — do not move any daemon parsing into the client.
