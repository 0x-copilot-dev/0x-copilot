import type { SaaSRendererAdapter } from "@enterprise-search/chat-surface";

import type {
  SmokeFailKind,
  SmokeMethod,
  SmokeRenderExecutor,
} from "./quality-gate";

// Main-process implementation of 6D's SmokeRenderExecutor. The 6A vm sandbox
// has already stripped privileged globals from the adapter's scope, so the
// adapter functions can be called directly on the main thread without
// reaching anything privileged. Preemptive termination of a misbehaving live
// render is the Tier2Loader Web Worker's responsibility (PRD D29); install-
// time smoke render uses a measured timeout because preempting synchronous
// JS on the main thread would require a worker_threads round-trip per call
// — overkill at install-time budget.

type TimeoutFn = (cb: () => void, ms: number) => unknown;
type ClearTimeoutFn = (handle: unknown) => void;

export interface SmokeRenderExecutorOptions {
  readonly setTimeout?: TimeoutFn;
  // Accepts `unknown` because the executor stores whatever the supplied
  // setTimeout returned and hands it back here — node's clearTimeout
  // overloads accept `number | NodeJS.Timeout | undefined`, both of which
  // pass through `unknown` without a runtime check.
  readonly clearTimeout?: ClearTimeoutFn;
}

interface ElementShape {
  readonly type?: unknown;
  readonly props?: unknown;
}

function looksLikeReactElement(value: unknown): value is ElementShape {
  if (value === null || typeof value !== "object") return false;
  const o = value as Record<string, unknown>;
  if (!("type" in o)) return false;
  if (!("props" in o) || o.props === null || typeof o.props !== "object") {
    return false;
  }
  return true;
}

export class MainProcessSmokeRenderExecutor implements SmokeRenderExecutor {
  readonly #setTimeout: TimeoutFn;
  readonly #clearTimeout: ClearTimeoutFn;

  constructor(options: SmokeRenderExecutorOptions = {}) {
    this.#setTimeout =
      options.setTimeout ?? ((cb, ms) => setTimeout(cb, ms) as unknown);
    this.#clearTimeout =
      options.clearTimeout ??
      ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  }

  async execute(
    adapter: SaaSRendererAdapter,
    payload: { method: SmokeMethod; input: unknown },
    budgetMs: number,
  ): Promise<{ ok: true } | { ok: false; kind: SmokeFailKind; error: Error }> {
    const call = adapter[payload.method] as (input: unknown) => unknown;
    let timeoutHandle: unknown = null;
    let timedOut = false;

    const timeoutPromise = new Promise<{
      ok: false;
      kind: SmokeFailKind;
      error: Error;
    }>((resolve) => {
      timeoutHandle = this.#setTimeout(() => {
        timedOut = true;
        resolve({
          ok: false,
          kind: "timeout",
          error: new Error(
            `smoke ${payload.method} exceeded ${budgetMs}ms budget`,
          ),
        });
      }, budgetMs);
    });

    const callPromise = Promise.resolve()
      .then(() => call(payload.input))
      .then(
        (
          result,
        ): { ok: true } | { ok: false; kind: SmokeFailKind; error: Error } => {
          if (timedOut) {
            return {
              ok: false,
              kind: "timeout",
              error: new Error(
                `smoke ${payload.method} exceeded ${budgetMs}ms budget`,
              ),
            };
          }
          if (!looksLikeReactElement(result)) {
            return {
              ok: false,
              kind: "not-element",
              error: new Error(
                `smoke ${payload.method} returned a non-element value`,
              ),
            };
          }
          return { ok: true };
        },
        (thrown: unknown): { ok: false; kind: SmokeFailKind; error: Error } => {
          const error =
            thrown instanceof Error
              ? thrown
              : new Error(typeof thrown === "string" ? thrown : String(thrown));
          return { ok: false, kind: "throw", error };
        },
      );

    const settled = await Promise.race([callPromise, timeoutPromise]);
    if (timeoutHandle !== null) this.#clearTimeout(timeoutHandle);
    return settled;
  }
}

export const SYNTHETIC_SMOKE_STATE = Object.freeze({
  id: "__smoke__",
  tier2_smoke: true,
});

export const SYNTHETIC_SMOKE_DIFF = Object.freeze({
  field_changes: [],
  resource: { id: "__smoke__" },
});
