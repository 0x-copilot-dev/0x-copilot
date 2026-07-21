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
