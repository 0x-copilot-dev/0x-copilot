_Read-only design PRD. Branch `claude/0xcopilot-first-run-onboarding-d7eb30`. All paths relative to ROOT `/Users/parthpahwa/Documents/work/enterprise-search/.claude/worktrees/0xcopilot-first-run-onboarding-d7eb30`. Grounded in the P2-relevant files; no files were edited._

# P2 — Local-model card + curated "Qwen 3 4B" preset

## 0. Context established from the code (so the plan is grounded, not hand-waved)

- **The pull pipeline is fully built and gated.** `runtime_api/local_models/service.py:75` (`pull_events`) drives `f"hf.co/{repo}:{quant}"` through `OllamaClient.pull` (`ollama_client.py:119`), computing `speed_bps`/`eta_seconds` server-side and emitting typed `LocalModelPullEvent` frames (`schemas/local_models.py:60`). The HTTP route `http/local_models_routes.py:98` streams them as SSE (`event: local_model_pull`), gated by `enable_local_models` (`:159-172`, every route but `/status` 404s when off). The facade byte-for-byte proxies the stream (`backend-facade/.../local_models_routes.py:42-78`). The web client `apps/frontend/src/api/localModelsApi.ts:60` (`streamLocalModelPull`) already parses this exact frame over the shared SSE transport lane. **P2 adds no backend routes and no api-types shapes** — `packages/api-types/src/localModels.ts` already carries everything.
- **The curated catalog is the only missing piece.** `packages/chat-surface/src/settings/DownloadLocalModelModal.tsx:46` defines `AvailableLocalModel` and `LocalModelsPage` defaults `availableModels = []` (`LocalModelsPage.tsx:99`). Both hosts pass nothing today: desktop `apps/desktop/renderer/SettingsMount.tsx:705-713` omits `availableModels` and stubs status/pull; web `apps/frontend/src/features/settings/sections/LocalModels.tsx` is a free-text repo field. So no curated "Qwen 3 4B" card exists anywhere — this is the net-new the mock's local card needs.
- **A local run resolves as `provider:"ollama"` + `model_name:<ollama tag>`.** `execution/models.py:60` aliases `ollama` → keyless (`:100-105`), so the credential gate never blocks it (`openai_compat.py:122` = the keyless registry row). The installed tag name after an HF pull is `hf.co/{repo}:{quant}`; the web composer already maps installed tags 1:1 (`apps/frontend/src/features/chat/ChatScreen.tsx:270-278`; desktop `renderer/composer/desktopModelCatalog.ts:64-74` → `{id, provider:"ollama", model_name:name}`).
- **The FTUE `enable_local_models` decision has a real gap.** ai-backend default is `False` (`agent_runtime/settings.py:141`, env `RUNTIME_ENABLE_LOCAL_MODELS` `:55`, loaded `:371`). The **staging** supervisor sets it true (`tools/desktop-runtime/run-local.mjs:713`) but the **packaged** supervisor's ai-backend env builder (`apps/desktop/main/services/service-env.ts:226-283`) does **not** — so a shipped desktop build reports `enabled:false` and the gate's local card is dead. That is the headline edit below.
- **Real HF sizes (verified via `huggingface.co/api/models/Qwen/Qwen3-4B-GGUF/tree/main`):** `Q4_K_M`=2,497,280,256 B (~2.5 GB), `Q5_K_M`~2.9 GB, `Q6_K`~3.3 GB, `Q8_0`~4.3 GB. **No standard Qwen3-4B quant is 5.6 GB** — the mock number is a placeholder.

---

## 1. Goal + scope

**Goal.** Give the FTUE gate (State A) a working "Download the local model" card that pulls a **real, curated Qwen 3 4B GGUF** over the already-shipped SSE pipeline, shows live in-gate progress that maps to the composer model pill `Qwen 3 4B · N%`, defines the "type while it downloads → queued run" state contract, and makes local models actually enabled in the packaged desktop.

**In scope (P2):**

1. A shared curated-preset constant (`QWEN3_4B_PRESET`) — the SSOT for the FTUE gate card **and** the Settings `LocalModelsPage availableModels`.
2. A `FirstRunLocalModelsPort` (status / list / startPull) reusing the existing `StartLocalModelPull` seam.
3. A `useFirstRunLocalModel` orchestration hook: status probe → `Start download` → live `pct` → `done`/`ready` → installed-tag resolution → `onReady`.
4. The `GateLocalCard` presentational component (verbatim copy, token-only, inline progress sub-state).
5. `firstRunModelPillLabel(engine)` + the `FirstRunEngine` state contract that P3's composer/ack consume.
6. **Enable local models in the packaged desktop** (`service-env.ts` edit); keep the ai-backend default off (cloud fail-safe); handle web/cloud `enabled:false` degrade.

**Out of scope (owned elsewhere):** the composer/model-pill render and the two-step conversation→run POST (**P3** — P2 only defines the state seam and fires `onReady`); the BYOK card/key form (**P1**); the `FirstRunSurface` scaffold + `stage` reducer + host onboarding mount (**P1** — P2 extends its state with `engine`/`pct` and slots `GateLocalCard`). The hosted-trial lane and the "Haiku starter" model row are **SHELVED** — not designed here (README §1, SPEC.md v1 note).

---

## 2. Exact files to CREATE and EDIT

### CREATE

| Path                                                                 | Purpose                                                                                                                                |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `packages/chat-surface/src/settings/localModelPresets.ts`            | The curated `QWEN3_4B_PRESET: AvailableLocalModel` + `LOCAL_MODEL_PRESETS` array — SSOT for the FTUE gate and Settings download modal. |
| `packages/chat-surface/src/settings/localModelPresets.test.ts`       | Pin the preset repo/quant/name/param and that `sizeBytes` matches the real Q4_K_M byte count.                                          |
| `packages/chat-surface/src/onboarding/localModelEngine.ts`           | `FirstRunEngine` discriminated union + `firstRunModelPillLabel()` + `resolveInstalledTag()` pure helpers (the P2↔P3 state contract).   |
| `packages/chat-surface/src/onboarding/localModelEngine.test.ts`      | Unit-test the pill label ("Qwen 3 4B · 41%" / "Qwen 3 4B") and tag resolution.                                                         |
| `packages/chat-surface/src/onboarding/ports.ts` (or extend P1's)     | `FirstRunLocalModelsPort` interface (re-exports the `StartLocalModelPull` handler/handle types).                                       |
| `packages/chat-surface/src/onboarding/useFirstRunLocalModel.ts`      | The download-orchestration hook: probe → start → live pct → ready → `onReady`.                                                         |
| `packages/chat-surface/src/onboarding/useFirstRunLocalModel.test.ts` | Drive the hook with a fake port (status branches, pct math, done→ready→onReady, error→retry, unmount close).                           |
| `packages/chat-surface/src/onboarding/GateLocalCard.tsx`             | The State-A left card (verbatim copy) + inline downloading/error sub-states over the hook.                                             |
| `packages/chat-surface/src/onboarding/GateLocalCard.test.tsx`        | Copy verbatim, enabled/disabled/downloading/error render, Start wiring.                                                                |
| `apps/frontend/src/features/onboarding/localModelsPort.ts`           | Web binder: `FirstRunLocalModelsPort` backed by `localModelsApi` (status/list/`streamLocalModelPull`).                                 |
| `apps/desktop/renderer/onboarding/localModelsPort.ts`                | Desktop binder: same port backed by the shell `Transport` (IPC → facade), mirroring the web projection (no `apps/*→apps/*` import).    |

### EDIT

| Path (anchor)                                                                                              | Change                                                                                                                                                                                     |
| ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `apps/desktop/main/services/service-env.ts:263` (ai-backend case, after `RUNTIME_START_IN_PROCESS_WORKER`) | **Add `env.RUNTIME_ENABLE_LOCAL_MODELS = "true";`** — the single fix that makes the packaged FTUE local card reachable. (`run-local.mjs:713` already does the staging equivalent.)         |
| `apps/desktop/main/services/service-env.test.ts` (if present)                                              | Assert `buildServiceEnv("ai-backend", …).RUNTIME_ENABLE_LOCAL_MODELS === "true"` for both file and postgres backends.                                                                      |
| `packages/chat-surface/src/index.ts` (new `// === P2 onboarding local-model ===` block)                    | Barrel-export `GateLocalCard`, `useFirstRunLocalModel`, `FirstRunLocalModelsPort`, `FirstRunEngine`, `firstRunModelPillLabel`, `QWEN3_4B_PRESET`, `LOCAL_MODEL_PRESETS`.                   |
| `packages/chat-surface/src/settings/index.ts:138-145`                                                      | Export `QWEN3_4B_PRESET`/`LOCAL_MODEL_PRESETS` alongside `LocalModelsPage`.                                                                                                                |
| `packages/chat-surface/src/onboarding/FirstRunSurface.tsx` (P1)                                            | Add `engine`/`pct` to the surface state; render `GateLocalCard` in the gate's left slot; wire `Start download` → hook; expose `engine` to the (P3) composer/ack.                           |
| `apps/desktop/renderer/SettingsMount.tsx:705-713`                                                          | Pass `availableModels={LOCAL_MODEL_PRESETS}` to `LocalModelsPage` (unify the settings download modal with the FTUE preset). _Optional-but-recommended; not required for the gate to work._ |
| `apps/frontend/src/features/onboarding/*` + `apps/desktop/renderer/bootstrap.tsx` (P1 mounts)              | Inject the new port + `QWEN3_4B_PRESET` into `FirstRunSurface`.                                                                                                                            |

**No change to** `packages/api-types` (localModels shapes already complete) or `agent_runtime/settings.py` (keep the `False` default).

---

## 3. New component / port / type signatures

### 3.1 Curated preset (`settings/localModelPresets.ts`)

```ts
import type { AvailableLocalModel } from "./DownloadLocalModelModal";

/**
 * The FTUE gate's "Download the local model" card + the Settings download
 * modal's catalog. One real entry today. `sizeBytes` is the verified HF
 * Q4_K_M byte size (heads-up denominator); the live GET /v1/local-models/size
 * refines it if the host asks for it.
 */
export const QWEN3_4B_PRESET: AvailableLocalModel = {
  repo: "Qwen/Qwen3-4B-GGUF", // official Qwen GGUF repo (Ollama: hf.co/Qwen/Qwen3-4B-GGUF:Q4_K_M)
  quant: "Q4_K_M", // matched case-insensitively by HfGgufResolver.size
  name: "Qwen 3 4B",
  parameterSize: "4B",
  sizeBytes: 2_497_280_256, // ≈ 2.5 GB — verified; NOT the mock's placeholder 5.6 GB
  note: "runs on this machine · free forever",
};

export const LOCAL_MODEL_PRESETS: readonly AvailableLocalModel[] = [
  QWEN3_4B_PRESET,
];
```

### 3.2 Engine state contract + helpers (`onboarding/localModelEngine.ts`)

```ts
import type { LocalModelSummary } from "@0x-copilot/api-types";

/** What the first run will execute against. Owned by P2; read by P3 composer/ack. */
export type FirstRunEngine =
  | { readonly kind: "none" }
  | {
      readonly kind: "local";
      readonly status: "downloading" | "ready" | "error";
      readonly pct: number; // 0..100
      readonly modelName: string | null; // resolved installed Ollama tag once ready
      readonly error: string | null;
    }
  | {
      readonly kind: "key";
      readonly provider: string;
      readonly modelId: string;
    }; // P1

/** Composer model-pill text. Downloading → "Qwen 3 4B · 41%", ready → "Qwen 3 4B". */
export function firstRunModelPillLabel(
  engine: FirstRunEngine,
  presetName: string,
): string {
  if (engine.kind !== "local") return ""; // BYOK label owned by P1/P3
  if (engine.status === "downloading")
    return `${presetName} · ${Math.round(engine.pct)}%`;
  if (engine.status === "error") return `${presetName} · failed`;
  return presetName; // ready / on-device
}

/** % from a live pull frame, falling back to the preset size hint as denominator. */
export function pullPercent(
  bytesCompleted: number | null,
  bytesTotal: number | null,
  sizeHint: number | null | undefined,
  done: boolean,
): number {
  const total = bytesTotal ?? sizeHint ?? 0;
  const got = bytesCompleted ?? 0;
  if (total > 0) return Math.min(100, (got / total) * 100);
  return done ? 100 : 0;
}

/**
 * Resolve the installed Ollama tag to send as `model_name`. Ollama's casing on
 * `hf.co/{repo}:{quant}` pulls is unreliable, so match the freshly-listed tags
 * by case-insensitive substring on the repo; fall back to the literal.
 */
export function resolveInstalledTag(
  models: readonly LocalModelSummary[],
  repo: string,
  quant: string,
): string {
  const needle = repo.toLowerCase();
  const hit = models.find((m) => m.name.toLowerCase().includes(needle));
  return hit?.name ?? `hf.co/${repo}:${quant}`;
}
```

### 3.3 The port (`onboarding/ports.ts`) — reuses the settings seam types (DRY)

```ts
import type {
  LocalModelsStatus,
  LocalModelSummary,
} from "@0x-copilot/api-types";
import type {
  StartLocalModelPull, // (request, handlers) => LocalModelPullHandle  — already exported
} from "../settings/DownloadLocalModelModal";

export interface FirstRunLocalModelsPort {
  /** GET /v1/local-models/status (always 200; gate reads `enabled`/`ollama_running`). */
  getStatus(signal?: AbortSignal): Promise<LocalModelsStatus>;
  /** GET /v1/local-models — used post-pull to resolve the installed tag name. */
  listModels(signal?: AbortSignal): Promise<readonly LocalModelSummary[]>;
  /** GET /v1/local-models/pull (SSE) — identical seam as the Settings modal. */
  startPull: StartLocalModelPull;
}
```

### 3.4 The hook (`onboarding/useFirstRunLocalModel.ts`)

```ts
export interface FirstRunLocalModelState {
  readonly engine: FirstRunEngine; // {kind:"none"} until Start; then {kind:"local",…}
  readonly enabled: boolean; // status.enabled — false on web/cloud
  readonly ollamaRunning: boolean; // false → GateLocalCard shows honest setup steps
  readonly probing: boolean;
  readonly start: () => void; // "Start download" handler (no-op if already downloading)
  readonly retry: () => void;
}

export function useFirstRunLocalModel(args: {
  readonly port: FirstRunLocalModelsPort;
  readonly preset: AvailableLocalModel; // QWEN3_4B_PRESET
  readonly onReady?: (modelName: string) => void; // fires once on done → P3 queued-run trigger
}): FirstRunLocalModelState;
```

### 3.5 The card (`onboarding/GateLocalCard.tsx`)

```ts
export interface GateLocalCardProps {
  readonly state: FirstRunLocalModelState; // from useFirstRunLocalModel
  readonly preset: AvailableLocalModel;
  /** Deep-link to Settings → local-models when Ollama isn't running (optional). */
  readonly onOpenLocalModelSettings?: () => void;
}
```

### 3.6 Host binder shape (web `localModelsPort.ts`; desktop mirrors via `Transport`)

```ts
import {
  getLocalModelsStatus,
  listLocalModels,
  streamLocalModelPull,
} from "../../api/localModelsApi";
import type { FirstRunLocalModelsPort } from "@0x-copilot/chat-surface";

export const webFirstRunLocalModelsPort: FirstRunLocalModelsPort = {
  getStatus: () => getLocalModelsStatus(),
  listModels: () => listLocalModels().then((r) => r.models),
  startPull: (request, handlers) => {
    const stream = streamLocalModelPull({
      repo: request.repo,
      quant: request.quant,
      onEvent: handlers.onEvent,
      onError: (err) => handlers.onError(err),
    });
    return { close: () => stream.close() };
  },
};
```

_Desktop: the same three methods over `transport.request({method:"GET", path:"/v1/local-models/status" | "/v1/local-models"})` and `transport.subscribeServerSentEvents({path:"/v1/local-models/pull", query:{repo,quant}, eventName:"local_model_pull", onMessage:…})` — the projection is duplicated by design (the `apps/*→apps/*` boundary), mirroring `destinationBinders.tsx:598-607`._

---

## 4. Precise wiring steps into the real code

1. **Enable the capability in the packaged build.** In `service-env.ts` ai-backend case, immediately after `env.RUNTIME_START_IN_PROCESS_WORKER = "true";` (`:263`), add `env.RUNTIME_ENABLE_LOCAL_MODELS = "true";`. This is read once at boot by `settings.py:371` and gates every route in `local_models_routes.py:159`. (Leave `settings.py:141` = `False` so web/self-host-cloud stay off.) The dark-capability gate (`tools/check_dark_capabilities.py:75`) stays satisfied — the flag remains referenced (now in two boot paths).

2. **Seed the preset.** `localModelPresets.ts` exports `QWEN3_4B_PRESET`. Wire it as `availableModels` at the two injection points the mock implies: the FTUE gate card (via the hook + `GateLocalCard`) and — recommended — `SettingsMount.tsx:705` `<LocalModelsPage availableModels={LOCAL_MODEL_PRESETS} …/>` (today omitted → `[]` → the "pick from available" list is empty, `LocalModelsPage.tsx:99`).

3. **The download orchestration hook.**
   - On mount: `port.getStatus()` → set `enabled`/`ollamaRunning`/`probing=false`. If `!enabled` or `!ollamaRunning`, `start()` is inert and `GateLocalCard` renders the disabled/setup sub-state (never a broken button).
   - `start()`: guard against re-entry (mirror `DownloadLocalModelModal.tsx:233`); set `engine = {kind:"local", status:"downloading", pct: 2, modelName:null, error:null}` (the mock seeds `pct=2`, SPEC §state machine); call `port.startPull({repo: preset.repo, quant: preset.quant}, {onEvent, onError})` and hold the handle in a ref.
   - `onEvent(frame)`: recompute `pct = pullPercent(frame.bytes_completed, frame.bytes_total, preset.sizeBytes, frame.done)` (same reducer as `DownloadLocalModelModal.tsx:242-258`, but the FTUE keeps only `pct`). On `frame.error` → `status:"error"`, close stream. On `frame.done` → `port.listModels()` → `resolveInstalledTag(models, repo, quant)` → `engine = {status:"ready", pct:100, modelName}` → `onReady(modelName)`.
   - `onError` / stream teardown on unmount: `handle.close()` (mirror `DownloadLocalModelModal.tsx:228-231`).

4. **In-gate progress → model pill.** `GateLocalCard`, while `engine.status==="downloading"`, renders the reused `ProgressBar` (`settings/controls.tsx:317`) + the mono status/size/ETA line (reuse `formatBytes`/`formatEta`/`humanStatus` from `settings/localModelsFormat.ts`) + the SPEC note "type your first prompt while it downloads". The **composer** pill text (P3) is `firstRunModelPillLabel(engine, preset.name)` — P3 reads the same `engine` off the surface state, so the pill shows `Qwen 3 4B · N%` with zero duplicated download logic.

5. **"Type while it downloads" → queued run (state contract here; POST in P3).** No backend queue exists — it's a client-side deferral, exactly as JOURNEYS J1 steps 4-6 describe:
   - The `FirstRunSurface` holds `draft`/`atts`/`sent`. Sending while `engine.status==="downloading"` sets `sent=true` and renders the Ack **"Queued — starts when the model lands"** (SPEC copy). It does **not** POST.
   - A single P3 effect keyed on `(sent, engine.status, engine.modelName)`: when `sent && engine.kind==="local" && engine.status==="ready"`, POST `/v1/agent/conversations` then `/v1/agent/runs` with `{ conversation_id, user_input, model: { provider:"ollama", model_name: engine.modelName } }` (the `ModelSelectionRequest` shape, `runtime_api/schemas/runs.py:41-45`; the exact wire the composer already uses, `desktopModelCatalog.ts:124-142`), open the SSE stream, `firstRunStore.set(complete)`, navigate. If the user sends **after** ready, the same effect fires immediately.
   - P2's deliverable for this is `FirstRunEngine` + the `onReady` seam + the Ack copy switch; P3 owns the POST.

6. **Barrel + hosts.** Add the P2 exports to `packages/chat-surface/src/index.ts` (new delimited block per the barrel discipline in `packages/chat-surface/CLAUDE.md`). P1's onboarding mounts inject the host port + `QWEN3_4B_PRESET`.

---

## 5. Parity notes (design classes → design-system tokens/primitives, per SPEC.md §2/§4)

| Mock (`copilot-firstrun.css`)                                                                                                 | Implementation (design-system SSOT — never hardcode hex)                                                                                                                                                                                          |
| ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.fr-gcard` radius **12**                                                                                                     | `border-radius: var(--radius-lg)` (`styles.css:92` = 12px).                                                                                                                                                                                       |
| `.fr-gcard` bg `--panel` / border `--line`                                                                                    | `background: var(--color-surface)` / `border: 1px solid var(--color-border)`.                                                                                                                                                                     |
| `.fr-gcard .ic` (accent glyph)                                                                                                | `color: var(--color-accent)` (sky `#5fb2ec`). One-accent discipline holds.                                                                                                                                                                        |
| `.gbtn--pri` "Start download" (accent bg, `#0b0a0e` text)                                                                     | `<Button variant="primary">` — already accent bg + `var(--color-accent-contrast)` (`#08131d`, `styles.css:164`).                                                                                                                                  |
| `.gbtn` secondary / setup "Re-check"                                                                                          | `<Button variant="secondary">` / `variant="ghost"`.                                                                                                                                                                                               |
| `.meta` mono **9.5** (`Qwen 3 4B · <size> · free forever`) · `.note` mono **9** ("type your first prompt while it downloads") | `font-family: var(--font-mono)`, `font-size: var(--font-size-2xs)` (`styles.css:62` = 11.2px). **Intentional upsize** — the "quiet v2" scale bottoms at 2xs; the mock's 9-9.5px sub-pixel mono is not a token. Note for the `ui-design-reviewer`. |
| Download progress bar / `.spin`                                                                                               | Reuse `ProgressBar` (`settings/controls.tsx:317`, token-only) — do **not** re-author.                                                                                                                                                             |
| Byte/ETA/status line                                                                                                          | Reuse `formatBytes`/`formatEta`/`humanStatus` (`settings/localModelsFormat.ts`).                                                                                                                                                                  |
| Model-pill `Qwen 3 4B · N%`                                                                                                   | `firstRunModelPillLabel(engine, "Qwen 3 4B")` fed into the P3 composer pill.                                                                                                                                                                      |
| Ollama-not-running (honest, not fake)                                                                                         | Reuse the `OllamaSetup` pattern (`LocalModelsPage.tsx:178`) linking `ollama.com/download`.                                                                                                                                                        |

Substrate rules (`packages/chat-surface/CLAUDE.md`): the surface touches **no** `fetch`/`window`/`localStorage`/`EventSource` — all I/O flows through `FirstRunLocalModelsPort` (eslint `no-restricted-globals` enforces this). Colors resolve **only** to `--color-*` tokens; per-provider dot colors are not in this card.

---

## 6. Test list

**Unit — chat-surface (`npx vitest run --root packages/chat-surface`):**

- `localModelPresets.test.ts`: `QWEN3_4B_PRESET.repo === "Qwen/Qwen3-4B-GGUF"`, `quant === "Q4_K_M"`, `name === "Qwen 3 4B"`, `sizeBytes === 2_497_280_256`; `LOCAL_MODEL_PRESETS` contains it.
- `localModelEngine.test.ts`: `firstRunModelPillLabel({kind:"local",status:"downloading",pct:41,…})` → `"Qwen 3 4B · 41%"`; ready → `"Qwen 3 4B"`; error → `"Qwen 3 4B · failed"`. `pullPercent` uses `bytes_total`, falls back to `sizeHint`, returns 100 on `done`. `resolveInstalledTag` matches case-insensitively and falls back to the literal.
- `useFirstRunLocalModel.test.ts` (fake port): `enabled:false` → `start()` inert; `enabled+ollamaRunning:false` → `ollamaRunning:false`; `start()` seeds downloading `pct:2` and calls `startPull` with `{repo,quant}`; byte frames advance `pct`; a `done` frame triggers `listModels` → `status:"ready"` + `onReady(tag)`; an `error` frame → `status:"error"`, `retry()` re-pulls; unmount calls `handle.close()`.
- `GateLocalCard.test.tsx`: renders **verbatim** copy ("Download the local model", "Qwen 3 4B · … · free forever", "Runs on this machine. Nothing you send ever leaves it.", "Start download", "type your first prompt while it downloads"); `enabled:false` → disabled/"desktop app" state, Start does not pull; downloading → `ProgressBar` + `Qwen 3 4B · N%` + note; error → `role="alert"` + Retry.

**Unit — hosts:**

- Web `localModelsPort.test.ts`: methods delegate to `localModelsApi` (status/list/`streamLocalModelPull`); `close()` tears down the stream.
- Desktop `service-env.test.ts`: `buildServiceEnv("ai-backend", …).RUNTIME_ENABLE_LOCAL_MODELS === "true"` for both `file` and `postgres` backends (guards the headline fix from regressing).

**Existing coverage to lean on (no change):** `services/ai-backend/tests/unit/runtime_api/test_local_models_routes.py` (gate + SSE frames), `services/backend-facade/tests/test_local_models_proxy.py` (byte-for-byte SSE proxy).

**Live-stack (P7-adjacent; opt-in, not CI):** on the supervised desktop boot (`tools/desktop-runtime/run-local.mjs`), assert `GET /v1/local-models/status` → `{enabled:true, ollama_running:<host>}`. A real pull of the ~2.5 GB GGUF is network- and time-heavy → **manual/opt-in only**, never in hermetic CI (mirrors the fake-model boot at `run-local.mjs:703`).

---

## 7. Acceptance criteria

1. `QWEN3_4B_PRESET` exists with the **real** `Qwen/Qwen3-4B-GGUF:Q4_K_M` and is the single source consumed by the FTUE gate card **and** the Settings `LocalModelsPage availableModels`.
2. Packaged desktop `GET /v1/local-models/status` → `enabled:true` (proves the `service-env.ts` edit); web/cloud → `enabled:false` and the gate local card degrades gracefully (no broken Start).
3. The gate local card renders verbatim SPEC copy and, on **Start download**, opens the **real** facade SSE pull for the preset (not a fake ticker).
4. In-gate progress shows a token-only progress bar + `Qwen 3 4B · N%` computed from live `bytes_completed/bytes_total`; `firstRunModelPillLabel(engine)` returns the same string for the P3 composer pill.
5. Ollama-not-running → honest setup steps (link to `ollama.com/download`), never a fake list/progress.
6. On `done`: the installed Ollama tag is resolved by re-listing (not a hardcoded literal); `engine.status="ready"`; `onReady(modelName)` fires exactly once.
7. "Type while it downloads": sending during download shows the Ack **"Queued — starts when the model lands"**; the deferred run-create (P3) fires on ready with `{provider:"ollama", model_name:<resolved tag>}`.
8. chat-surface stays substrate-clean (no bare `fetch`/`window`; all I/O via the port — eslint green); colors are token-only; `npm run typecheck` + vitest green.

---

## 8. Risks / edge-cases

- **Mock "5.6 GB" is not real** for any standard Qwen3-4B quant (verified: Q4_K_M 2.5 GB … Q8_0 4.3 GB). Shipping "5.6 GB" would be a lie. **Recommendation: Q4_K_M** (~2.5 GB, broadest hardware compatibility — best fits "Runs on this machine") and correct the card copy to the real size (or show the live `/v1/local-models/size` value). This is the one deliberate copy deviation from the mock — see Open Questions.
- **Packaged-build dead card (headline).** Without the `service-env.ts` edit, a shipped desktop reports `enabled:false` and the gate's primary CTA is inert — the exact class of bug the MEMORY "no-run-executor = red light, not a silent hang" (#157) warns against. The `service-env.test.ts` assertion is the guardrail.
- **Ollama tag casing.** HF pulls land as `hf.co/{repo}:{quant}` but Ollama's stored casing is unreliable; always resolve via `resolveInstalledTag` (substring), never a hardcoded literal, or the queued run 404s on model resolution.
- **Web/cloud degrade.** `enabled:false` → `GateLocalCard` must show a disabled "available in the desktop app" state and route the user to the BYOK card; never surface a Start button that 404s the pull.
- **Long-lived SSE over the desktop IPC bridge.** A multi-minute, multi-GB pull streams through `apps/desktop/main/transport-bridge.ts:70` — verify no idle/inactivity timeout clips a slow-progress pull (Ollama emits sparse frames between layers). The facade already sets `X-Accel-Buffering: no` (`local_models_routes.py:77`).
- **No resume.** The pull has no client resume; navigating away aborts the client subscription but Ollama keeps pulling server-side. On return, `listModels()` shows the model once complete. Acceptable for FTUE; document it.
- **HF/network dependency.** `/size` and the pull reach `huggingface.co`; offline → typed error surfaced via the error frame (`local_models_routes.py:137`). The preset `sizeBytes` still gives the progress bar a denominator if `/size` is skipped.
- **Double-start.** Guard `start()` against re-entry while downloading (mirror `DownloadLocalModelModal.tsx:233` `closeStream()` first).
- **dark-capability gate.** Adding `RUNTIME_ENABLE_LOCAL_MODELS` to `service-env.ts` keeps the flag referenced (satisfies `tools/check_dark_capabilities.py`); no waiver needed.

---

## 9. Open questions (need a human decision)

1. **Shipped quant + copy correction (blocks pixel parity).** Confirm **Q4_K_M (~2.5 GB, recommended)** vs **Q8_0 (~4.3 GB, near-lossless)**, and approve correcting the mock's "5.6 GB · free forever" to the real size (or a live size). Owner: product/design. (README §8 + STATUS "P2: Qwen 3 4B vs a lighter shipped preset" is exactly this.)
2. **`enable_local_models` default.** Confirm the recommendation: keep ai-backend default `False`; enable via the desktop **supervisor env** (packaged `service-env.ts` + staging `run-local.mjs`), not the Pydantic default. (STATUS "P2: desktop default for `enable_local_models`".)
3. **Web/cloud gate local card:** hide entirely, or show disabled with "available in the desktop app"? (Recommend: show disabled — the mock's gate is two-card and hiding one breaks the 2-col grid.)
4. **Unify Settings catalog now?** Seed `LocalModelsPage availableModels` with `LOCAL_MODEL_PRESETS` (recommended — one curated SSOT) vs keep the web Settings free-text repo field. (Low risk; touches `SettingsMount.tsx:705` only.)
5. **Ollama bootstrapping.** The FTUE promises a one-click "Start download", but Ollama may be absent. v1 falls back to setup steps. Is a bundled/auto-installed Ollama in scope later? Product decision (out of P2).

---

## Open questions

- Shipped quant + copy: confirm Q4_K_M (~2.5 GB, recommended for broad hardware) vs Q8_0 (~4.3 GB, near-lossless), AND approve correcting the mock's '5.6 GB' label to the real size — no standard Qwen3-4B quant is 5.6 GB, so shipping it verbatim is inaccurate. Owner: product/design.
- enable_local_models default: confirm keeping the ai-backend Pydantic default False (cloud fail-safe) and enabling only via the desktop supervisor env — which requires the net-new one-line edit to the PACKAGED supervisor apps/desktop/main/services/service-env.ts (the staging tool already sets it; the packaged build does not, so the FTUE local card is currently dead in real builds).
- Web/cloud (enabled:false): should the gate's local card be hidden entirely or shown disabled with 'available in the desktop app'? (Hiding one card breaks the mock's 2-column gate grid — recommend disabled.)
- Unify the Settings LocalModelsPage availableModels (currently []) with the same QWEN3_4B_PRESET now, or keep the web Settings free-text repo field separate? (Recommend unify to one curated SSOT.)
- Ollama bootstrapping: the FTUE promises one-click 'Start download' but Ollama may be absent (v1 falls back to setup steps). Is a bundled/auto-installed Ollama in scope for a later phase?
