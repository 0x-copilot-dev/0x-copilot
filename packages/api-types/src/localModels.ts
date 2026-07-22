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
//   POST   /v1/local-models/runtime/start       → LocalModelsStatus
//   DELETE /v1/local-models/{name}              → 204
//
// `pull` is a GET so it rides the browser EventSource / SSE transport lane
// (query-only, no request body).
//
// `runtime/start` starts (or restarts) the local runtime on the host and
// returns the resulting status; it is idempotent, so an already-running
// runtime is a success. It 404s unless the deployment both enabled local
// models AND permits this server to manage the runtime process — the same
// server-authoritative gate reported by `LocalModelsStatus.runtime_managed`.

/** Where a loaded model actually runs (Ollama `/api/ps`); `null` = not loaded. */
export type LocalModelRunPlacement = "gpu" | "cpu" | "partial";

/**
 * Whether the local runtime binary exists on this machine and is answering.
 * Derived server-side — clients never infer it.
 *
 * `"unknown"` is the honest answer when the server cannot see the host
 * filesystem (web / containerised self-host, where the runtime lives behind
 * `OLLAMA_BASE_URL`); it is not a synonym for `"not_installed"`.
 */
export type LocalRuntimeState =
  | "unknown"
  | "not_installed"
  | "stopped"
  | "running";

/**
 * Server-side classification of a failed pull, so clients know whether to
 * recover automatically or stop and wait for the user.
 *
 * - `runtime_unreachable` — the daemon died or refused; resume once it is back.
 * - `transient` — network blip / stream break; retry with capped backoff.
 * - `terminal` — 4xx, bad repo, disk full; no auto-retry, needs a user action.
 */
export type LocalModelErrorKind =
  | "runtime_unreachable"
  | "transient"
  | "terminal";

/** Capability probe: is the feature enabled and is Ollama reachable. */
export interface LocalModelsStatus {
  readonly enabled: boolean;
  readonly ollama_running: boolean;
  /** Ollama version string when running, else `null`. */
  readonly ollama_version: string | null;
  /**
   * Richer runtime state, added after the three fields above. Optional
   * because older servers omit it: when it is absent consumers MUST fall
   * back to `ollama_running` (`true` → `"running"`, `false` → `"unknown"`)
   * rather than assuming the runtime is missing.
   */
  readonly runtime_state?: LocalRuntimeState;
  /**
   * Whether this server may start/restart the runtime — i.e. whether
   * `POST /v1/local-models/runtime/start` can do anything but 404. Optional;
   * treat an absent value as `false` and render no restart affordance.
   */
  readonly runtime_managed?: boolean;
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

/**
 * The `repo` + `quant` pair that identifies one HF GGUF to pull. Not a JSON
 * body: both `pull` and `size` are GETs and carry these as query params (see
 * the route list above). Clients keep the pair as one value.
 */
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
  /**
   * Classification of an `error` frame. Optional and nullable: the server
   * sends `null` on every non-error frame, and older servers omit the field
   * entirely. When it is absent on an error frame, consumers MUST degrade to
   * `"terminal"` (stop, surface the error) rather than guessing that a retry
   * is safe.
   */
  readonly error_kind?: LocalModelErrorKind | null;
}
