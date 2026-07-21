// Local-models data seam (DESIGN-SPEC §4/§5 · Settings → Local models).
//
// Desktop / self-host only. Download a Hugging Face GGUF and run it locally via
// a user-installed Ollama. The feature is gated server-side: `status` always
// answers, but every other route 404s unless the deployment enabled it — so the
// page degrades to its setup steps, never a crash.
//
// The page depends on the PORT, not on `Transport` directly, so the runtime
// (Ollama reachability, the HF pull, the installed list, delete) is a host
// concern the substrate injects. Both hosts wire the default Transport adapter
// below; tests / alternative substrates pass their own `LocalModelsPort`.
//
// Substrate-agnostic (chat-surface boundary): no bare `fetch` / `EventSource` /
// `window`. The adapter only builds `TypedRequest`s + `SseSubscribeOptions` and
// calls the injected `Transport`. Ollama output is never parsed client-side —
// the server projects each raw pull line into a typed `LocalModelPullEvent`
// frame; we only JSON-parse those frames and drop anything malformed.
//
// Facade routes (user bearer, RBAC scope RUNTIME_USE):
//
//   GET    /v1/local-models/status              → LocalModelsStatus
//   GET    /v1/local-models                     → LocalModelsListResponse
//   GET    /v1/local-models/size?repo=&quant=   → LocalModelSize
//   GET    /v1/local-models/pull?repo=&quant=   → SSE `local_model_pull` frames
//   DELETE /v1/local-models/{name:path}         → 204

import type {
  LocalModelPullEvent,
  LocalModelSize,
  LocalModelsListResponse,
  LocalModelsStatus,
  LocalModelSummary,
} from "@0x-copilot/api-types";

import type { Transport } from "../../ports/Transport";
import type {
  AvailableLocalModel,
  LocalModelPullHandle,
  LocalModelPullHandlers,
} from "../DownloadLocalModelModal";

/**
 * SSE event name for the pull-progress stream. The server emits each frame
 * under this event; mirrors the ai-backend `local_models_routes` writer and the
 * legacy web client (`apps/frontend/src/api/localModelsApi.ts`).
 */
export const LOCAL_MODEL_PULL_EVENT = "local_model_pull";

/** Default GGUF quant offered by the curated catalog + the custom-repo path. */
export const DEFAULT_LOCAL_MODEL_QUANT = "Q4_K_M";

/**
 * The Ollama tag a pulled Hugging Face GGUF installs under —
 * ``hf.co/{repo}:{quant}`` (backend `LocalModelsService.pull_events`). This is
 * the value that appears as `LocalModelSummary.name` in the installed list, so
 * both host binders build the default-local model name from a download result
 * (`{repo, quant}`) through this ONE helper — keeping the "download → set as
 * default" round-trip byte-identical across web + desktop (they can't share a
 * binder, only this package).
 */
export function localModelInstalledTag(repo: string, quant: string): string {
  return `hf.co/${repo}:${quant}`;
}

// ---------------------------------------------------------------------------
// Curated catalog (DESIGN-SPEC §5 "pick from available", decision D-4).
//
// A small set of popular open models the modal offers as one-click picks, so
// the converged flow no longer forces the web legacy's free-text-only path.
// The power-user free-text path still lives in the modal (custom repo / quant).
//
// `repo` is the Hugging Face GGUF repo Ollama pulls via `hf.co/{repo}:{quant}`
// (backend `LocalModelsService.pull_events`); `quant` is a GGUF quant token
// matched case-insensitively against the repo's filenames. `sizeBytes` is only a
// pre-download heads-up (the progress bar's denominator before the first byte
// frame) — the modal never probes size for a catalog pick, so an estimate is
// fine; the real byte totals arrive from the pull stream. Notes call out the
// hardware envelope honestly so a laptop user doesn't pick a 70B by mistake.
// ---------------------------------------------------------------------------

export const LOCAL_MODEL_CATALOG: readonly AvailableLocalModel[] = [
  {
    repo: "bartowski/Llama-3.2-3B-Instruct-GGUF",
    quant: DEFAULT_LOCAL_MODEL_QUANT,
    name: "Llama 3.2 3B",
    parameterSize: "3.2B",
    sizeBytes: 2_019_000_000,
    note: "small · fast · runs on most laptops",
  },
  {
    repo: "bartowski/phi-4-GGUF",
    quant: DEFAULT_LOCAL_MODEL_QUANT,
    name: "Phi-4 14B",
    parameterSize: "14B",
    sizeBytes: 9_050_000_000,
    note: "strong reasoning · ~9 GB",
  },
  {
    repo: "bartowski/Mistral-Small-24B-Instruct-2501-GGUF",
    quant: DEFAULT_LOCAL_MODEL_QUANT,
    name: "Mistral Small 3 24B",
    parameterSize: "24B",
    sizeBytes: 14_330_000_000,
    note: "capable all-rounder · needs a big GPU",
  },
  {
    repo: "bartowski/google_gemma-3-27b-it-GGUF",
    quant: DEFAULT_LOCAL_MODEL_QUANT,
    name: "Gemma 3 27B",
    parameterSize: "27B",
    sizeBytes: 16_500_000_000,
    note: "Google Gemma 3 · large GPU",
  },
  {
    repo: "bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF",
    quant: DEFAULT_LOCAL_MODEL_QUANT,
    name: "DeepSeek-R1 32B",
    parameterSize: "32B",
    sizeBytes: 19_850_000_000,
    note: "reasoning distill · large GPU",
  },
  {
    repo: "bartowski/Qwen2.5-Coder-32B-Instruct-GGUF",
    quant: DEFAULT_LOCAL_MODEL_QUANT,
    name: "Qwen 2.5 Coder 32B",
    parameterSize: "32B",
    sizeBytes: 19_850_000_000,
    note: "coding-tuned · large GPU",
  },
  {
    repo: "bartowski/Llama-3.3-70B-Instruct-GGUF",
    quant: DEFAULT_LOCAL_MODEL_QUANT,
    name: "Llama 3.3 70B",
    parameterSize: "70B",
    sizeBytes: 42_520_000_000,
    note: "top quality · very large GPU",
  },
];

// ---------------------------------------------------------------------------
// Port — the host-callback seam the page depends on.
// ---------------------------------------------------------------------------

export interface LocalModelsPort {
  /** `GET /v1/local-models/status` — capability probe (always answers). */
  status(signal?: AbortSignal): Promise<LocalModelsStatus>;
  /** `GET /v1/local-models` — installed models (unwrapped from `.models`). */
  list(signal?: AbortSignal): Promise<readonly LocalModelSummary[]>;
  /** `GET /v1/local-models/size?repo=&quant=` — pre-download size heads-up. */
  size(
    repo: string,
    quant: string,
    signal?: AbortSignal,
  ): Promise<LocalModelSize>;
  /**
   * `DELETE /v1/local-models/{name}` — remove an installed model. The name is
   * an Ollama tag like `hf.co/{repo}:{quant}`, so it carries `/` and `:`;
   * `encodeURIComponent` keeps them intact for the backend `{name:path}` route.
   */
  remove(name: string, signal?: AbortSignal): Promise<void>;
  /**
   * Open the pull-progress SSE stream for one HF GGUF (`repo` + `quant`). Each
   * `local_model_pull` frame is JSON-parsed and forwarded to `handlers.onEvent`;
   * malformed frames are dropped without tearing the stream down (mirrors the
   * legacy web client). The stream ends with a `done: true` frame or a terminal
   * `status: "error"` frame carrying `error`; `handle.close()` aborts it. This
   * is the value the page's `StartLocalModelPull` seam wraps.
   */
  pull(
    repo: string,
    quant: string,
    handlers: LocalModelPullHandlers,
  ): LocalModelPullHandle;
}

/**
 * Default `LocalModelsPort` backed by the injected `Transport`. Builds typed
 * facade requests; no bare `fetch`/`EventSource` — the pull stream rides the
 * shared SSE transport lane (bearer attached by the host transport, not a query
 * param).
 */
export function createLocalModelsPort(transport: Transport): LocalModelsPort {
  return {
    status(signal) {
      return transport.request<LocalModelsStatus>({
        method: "GET",
        path: "/v1/local-models/status",
        signal,
      });
    },
    async list(signal) {
      const res = await transport.request<LocalModelsListResponse>({
        method: "GET",
        path: "/v1/local-models",
        signal,
      });
      return res.models;
    },
    size(repo, quant, signal) {
      return transport.request<LocalModelSize>({
        method: "GET",
        path: "/v1/local-models/size",
        query: { repo, quant },
        signal,
      });
    },
    async remove(name, signal) {
      await transport.request<void>({
        method: "DELETE",
        path: `/v1/local-models/${encodeURIComponent(name)}`,
        signal,
      });
    },
    pull(repo, quant, handlers) {
      return transport.subscribeServerSentEvents({
        path: "/v1/local-models/pull",
        query: { repo, quant },
        eventName: LOCAL_MODEL_PULL_EVENT,
        onError: (err) => handlers.onError(err),
        onMessage: (raw) => {
          let parsed: unknown;
          try {
            parsed = JSON.parse(raw) as unknown;
          } catch {
            return; // drop a malformed frame; keep the stream alive
          }
          if (isLocalModelPullEvent(parsed)) {
            handlers.onEvent(parsed);
          }
        },
      });
    },
  };
}

/** Structural guard for a pull frame (mirrors the legacy web client). */
function isLocalModelPullEvent(value: unknown): value is LocalModelPullEvent {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.sequence_no === "number" &&
    typeof record.status === "string" &&
    typeof record.done === "boolean"
  );
}
