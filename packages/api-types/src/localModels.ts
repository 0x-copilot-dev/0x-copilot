// Local models (Round 2 — download an HF GGUF + run it locally via Ollama).
//
// Desktop / self-host only. The feature is gated server-side: `status`
// always answers, but every other route 404s unless the deployment enabled
// it. Mirrors the ai-backend schemas in
// `services/ai-backend/src/runtime_api/schemas/local_models.py`.
//
// Public facade routes (user bearer, RBAC scope RUNTIME_USE):
//
//   GET    /v1/local-models/status              → LocalModelsStatus
//   GET    /v1/local-models                     → LocalModelsListResponse
//   GET    /v1/local-models/size?repo=&quant=   → LocalModelSize
//   GET    /v1/local-models/pull?repo=&quant=   → SSE stream of LocalModelPullEvent
//   DELETE /v1/local-models/{name}              → 204
//
// `pull` is a GET so it rides the browser EventSource / SSE transport lane
// (query-only, no request body).

/** Where a loaded model actually runs (Ollama `/api/ps`); `null` = not loaded. */
export type LocalModelRunPlacement = "gpu" | "cpu" | "partial";

/** Capability probe: is the feature enabled and is Ollama reachable. */
export interface LocalModelsStatus {
  readonly enabled: boolean;
  readonly ollama_running: boolean;
  /** Ollama version string when running, else `null`. */
  readonly ollama_version: string | null;
}

/** One installed local model (an Ollama tag), with live GPU/CPU placement. */
export interface LocalModelSummary {
  readonly name: string;
  readonly size_bytes: number;
  readonly quantization: string | null;
  readonly parameter_size: string | null;
  readonly run_placement: LocalModelRunPlacement | null;
}

/** `GET /v1/local-models` response. */
export interface LocalModelsListResponse {
  readonly models: readonly LocalModelSummary[];
}

/** Pre-download size heads-up for one HF GGUF (repo + quant). */
export interface LocalModelSize {
  readonly repo: string;
  readonly quant: string;
  readonly filename: string;
  readonly size_bytes: number;
}

/** Body for `POST /v1/local-models/pull`. */
export interface PullLocalModelRequest {
  readonly repo: string;
  readonly quant: string;
}

/**
 * One SSE frame of pull progress. `bytes_*` are present only on download
 * lines; `speed_bps` / `eta_seconds` are computed server-side. The stream
 * ends with `done: true`, or a terminal `status: "error"` frame carrying
 * `error`.
 */
export interface LocalModelPullEvent {
  readonly sequence_no: number;
  readonly status: string;
  readonly bytes_total: number | null;
  readonly bytes_completed: number | null;
  readonly speed_bps: number | null;
  readonly eta_seconds: number | null;
  readonly done: boolean;
  readonly error: string | null;
}
