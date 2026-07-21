// FTUE local-models port (P2) — the substrate seam the download hook depends on.
//
// The presentational surface performs NO I/O: `status` / `list` / `pull` all
// flow through this host-injected port. `createFirstRunLocalModelsPort` is the
// default `Transport`-backed adapter (mirrors `createModelsPort` /
// `createProviderKeysPort`), so BOTH hosts get the same facade projection over
// their own `Transport` (web fetch+SSE, desktop IPC→facade) with no
// `apps/* → apps/*` duplication.
//
// Substrate-agnostic: only the injected `Transport` port is touched — no bare
// `fetch` / `EventSource` / `window` (eslint-banned in this package).

import type {
  LocalModelPullEvent,
  LocalModelSummary,
  LocalModelsListResponse,
  LocalModelsStatus,
} from "@0x-copilot/api-types";

import type { Transport } from "../ports/Transport";
import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";

/** The SSE frame name the facade tags each pull-progress line with. */
const PULL_EVENT_NAME = "local_model_pull";

/**
 * Host seam for the first-run local model download.
 *
 *  - `status()` — `GET /v1/local-models/status` (always 200; the gate reads
 *    `enabled` / `ollama_running` to decide the card's sub-state).
 *  - `list()`   — `GET /v1/local-models`; used post-pull to resolve the
 *    installed Ollama tag name.
 *  - `pull()`   — `GET /v1/local-models/pull` (SSE); an async stream of
 *    `LocalModelPullEvent` frames the hook reduces into `localModelPct`.
 */
export interface FirstRunLocalModelsPort {
  status(signal?: AbortSignal): Promise<LocalModelsStatus>;
  list(signal?: AbortSignal): Promise<readonly LocalModelSummary[]>;
  pull(
    preset: AvailableLocalModel,
    signal?: AbortSignal,
  ): AsyncIterable<LocalModelPullEvent>;
}

function isLocalModelPullEvent(value: unknown): value is LocalModelPullEvent {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.sequence_no === "number" &&
    typeof record.status === "string" &&
    typeof record.done === "boolean"
  );
}

/**
 * Default `FirstRunLocalModelsPort` backed by the injected `Transport`.
 *
 * The `pull` generator bridges the callback-based SSE subscription
 * (`transport.subscribeServerSentEvents`) into an `AsyncIterable`: frames are
 * buffered in a queue and drained by the consumer; a `done`/`error` frame, a
 * transport error, or an aborted `signal` all end the stream and `close()` the
 * underlying subscription in the generator's `finally`.
 */
export function createFirstRunLocalModelsPort(
  transport: Transport,
): FirstRunLocalModelsPort {
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
    async *pull(preset, signal) {
      const queue: LocalModelPullEvent[] = [];
      let streamError: Error | null = null;
      let closed = false;
      let wake: (() => void) | null = null;

      const notify = (): void => {
        if (wake) {
          const resume = wake;
          wake = null;
          resume();
        }
      };

      const subscription = transport.subscribeServerSentEvents({
        path: "/v1/local-models/pull",
        query: { repo: preset.repo, quant: preset.quant },
        eventName: PULL_EVENT_NAME,
        onMessage: (raw) => {
          let parsed: unknown;
          try {
            parsed = JSON.parse(raw) as unknown;
          } catch {
            return; // drop malformed frames without tearing the stream down
          }
          if (isLocalModelPullEvent(parsed)) {
            queue.push(parsed);
            notify();
          }
        },
        onError: (err) => {
          streamError = err;
          closed = true;
          notify();
        },
      });

      const onAbort = (): void => {
        closed = true;
        notify();
      };
      signal?.addEventListener("abort", onAbort);

      try {
        while (true) {
          while (queue.length > 0) {
            const frame = queue.shift() as LocalModelPullEvent;
            yield frame;
            if (frame.done || frame.error !== null) return;
          }
          if (closed) {
            if (streamError) throw streamError;
            return;
          }
          if (signal?.aborted) return;
          await new Promise<void>((resolve) => {
            wake = resolve;
          });
        }
      } finally {
        subscription.close();
        signal?.removeEventListener("abort", onAbort);
      }
    },
  };
}
