// Web `FirstRunLocalModelsPort` (P2) — backed by the typed `api/localModelsApi`
// module. The shared `createFirstRunLocalModelsPort(transport)` performs the
// same three facade calls over a `Transport`; the web host cannot import the
// Transport singleton inside `features/**` (eslint-banned), so it wraps the
// typed api client instead. Same endpoints:
//   status() → GET /v1/local-models/status
//   list()   → GET /v1/local-models
//   pull()   → GET /v1/local-models/pull (SSE)
//
// The one piece of real work here is bridging the callback-based SSE client
// (`streamLocalModelPull`) into the port's `AsyncIterable<LocalModelPullEvent>`
// — the same queue/drain shape the shared Transport adapter uses in its `pull`
// generator. A `done`/`error` frame, a stream error, or an aborted `signal`
// all end the iterator and `close()` the underlying subscription in `finally`.

import type {
  LocalModelPullEvent,
  LocalModelSummary,
  LocalModelsStatus,
} from "@0x-copilot/api-types";
import type {
  AvailableLocalModel,
  FirstRunLocalModelsPort,
} from "@0x-copilot/chat-surface";

import {
  getLocalModelsStatus,
  listLocalModels,
  startLocalModelRuntime,
  streamLocalModelPull,
} from "../../api/localModelsApi";

/**
 * Build the web `FirstRunLocalModelsPort` over `api/localModelsApi`. Consumed
 * by the shared `useFirstRunLocalModel` hook exactly as the desktop's
 * Transport-backed port is.
 */
export function createFirstRunLocalModelsPort(): FirstRunLocalModelsPort {
  return {
    status(): Promise<LocalModelsStatus> {
      return getLocalModelsStatus();
    },
    async list(): Promise<readonly LocalModelSummary[]> {
      const res = await listLocalModels();
      return res.models;
    },
    // PRD-P8 §4.3. On the web deployment this route 404s (the server may not
    // manage a host process), and `runtime_managed: false` means the card never
    // renders the button that would call it — but the port shape is shared with
    // desktop, so it is implemented rather than stubbed.
    startRuntime(): Promise<LocalModelsStatus> {
      return startLocalModelRuntime();
    },
    async *pull(
      preset: AvailableLocalModel,
      signal?: AbortSignal,
    ): AsyncIterable<LocalModelPullEvent> {
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

      const subscription = streamLocalModelPull({
        repo: preset.repo,
        quant: preset.quant,
        onEvent: (event) => {
          queue.push(event);
          notify();
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
            // Nullish, not just `null` — mirrors the shared Transport adapter.
            // `isLocalModelPullEvent` only requires sequence_no/status/done, so
            // a truncated frame can arrive with no `error` key; reading that as
            // a terminal error would close a healthy stream, which the hook
            // then classifies as a break and retries, forever.
            if (
              frame.done ||
              (frame.error !== null && frame.error !== undefined)
            )
              return;
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
