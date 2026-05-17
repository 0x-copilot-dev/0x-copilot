import type { AdapterGeneratedPayload } from "@enterprise-search/api-types";

import { appendLifecycleEvent } from "./lifecycle-events";
import {
  installAdapter,
  markBrokenFromBoundary,
  type RegistryHostDeps,
} from "./registry-host";

// Phase 6C orchestrator. Listens to adapter_generated run events and Q6
// boundary errors; drives the install pipeline through registry-host;
// enforces the 3-attempt × ~5 s retry budget (PRD §9.5.4). The counter
// resets on a successful install — a freshly-broken adapter starts with a
// full budget for its next regen.

export interface LifecycleBoundaryEvent {
  readonly scheme: string;
  readonly version: number;
  readonly method: "renderCurrent" | "renderDiff";
  readonly reason: string;
}

export interface LifecycleEventSource {
  onAdapterGenerated(
    handler: (payload: AdapterGeneratedPayload) => void,
  ): () => void;
  onBoundaryError(handler: (info: LifecycleBoundaryEvent) => void): () => void;
}

type TimeoutFn = (cb: () => void, ms: number) => unknown;
type ClearTimeoutFn = (handle: unknown) => void;

export interface Tier2LifecycleDeps {
  readonly source: LifecycleEventSource;
  readonly host: RegistryHostDeps;
  readonly retryBudget?: number;
  readonly attemptTimeoutMs?: number;
  readonly setTimeout?: TimeoutFn;
  readonly clearTimeout?: ClearTimeoutFn;
  readonly onExhausted?: (scheme: string) => void;
  readonly onError?: (err: Error) => void;
}

export interface Tier2LifecycleHandle {
  stop(): void;
  attempts(scheme: string): number;
}

export const DEFAULT_RETRY_BUDGET = 3;
export const DEFAULT_ATTEMPT_TIMEOUT_MS = 5000;

interface AttemptTimer {
  fired: boolean;
}

type DeadlineSettled<T> =
  | { readonly outcome: "value"; readonly value: T }
  | { readonly outcome: "error"; readonly error: Error }
  | { readonly outcome: "timeout" };

function raceWithDeadline<T>(
  promise: Promise<T>,
  ms: number,
  schedule: TimeoutFn,
  cancel: ClearTimeoutFn,
): Promise<DeadlineSettled<T>> {
  return new Promise((resolve) => {
    const timer: AttemptTimer = { fired: false };
    const handle = schedule(() => {
      timer.fired = true;
      resolve({ outcome: "timeout" });
    }, ms);
    promise
      .then((value) => {
        if (timer.fired) return;
        cancel(handle);
        resolve({ outcome: "value", value });
      })
      .catch((err: unknown) => {
        if (timer.fired) return;
        cancel(handle);
        const error = err instanceof Error ? err : new Error(String(err));
        resolve({ outcome: "error", error });
      });
  });
}

export function startTier2Lifecycle(
  deps: Tier2LifecycleDeps,
): Tier2LifecycleHandle {
  const retryBudget = deps.retryBudget ?? DEFAULT_RETRY_BUDGET;
  const attemptTimeoutMs = deps.attemptTimeoutMs ?? DEFAULT_ATTEMPT_TIMEOUT_MS;
  const schedule: TimeoutFn =
    deps.setTimeout ?? ((cb, ms) => setTimeout(cb, ms) as unknown);
  const cancel: ClearTimeoutFn =
    deps.clearTimeout ??
    ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));

  // Per-scheme failed-attempt counter. Reset only on successful install
  // (PRD §9.5.4: a freshly-broken adapter starts with a full budget).
  const attempts = new Map<string, number>();

  const recordError = (err: unknown): void => {
    if (deps.onError) {
      deps.onError(err instanceof Error ? err : new Error(String(err)));
    }
  };

  const handleGenerated = (payload: AdapterGeneratedPayload): void => {
    void (async () => {
      try {
        await appendLifecycleEvent(
          {
            ts: deps.host.clock(),
            kind: "generated",
            scheme: payload.scheme,
            version: payload.schema_version,
            detail: `model=${payload.generator_model}; layout=${payload.layout}`,
          },
          deps.host.audit,
        );

        const used = attempts.get(payload.scheme) ?? 0;
        if (used >= retryBudget) {
          await appendLifecycleEvent(
            {
              ts: deps.host.clock(),
              kind: "lifecycle-exhausted",
              scheme: payload.scheme,
              version: payload.schema_version,
              detail: `attempts=${used}/${retryBudget}`,
            },
            deps.host.audit,
          );
          deps.onExhausted?.(payload.scheme);
          return;
        }

        const settled = await raceWithDeadline(
          installAdapter(
            {
              scheme: payload.scheme,
              version: payload.schema_version,
              source: payload.adapter_source,
              generatedAt: payload.generated_at,
              generatorModel: payload.generator_model,
            },
            deps.host,
          ),
          attemptTimeoutMs,
          schedule,
          cancel,
        );

        if (settled.outcome === "value" && settled.value.ok) {
          attempts.delete(payload.scheme);
          return;
        }

        // Either timeout, install-pipeline rejection, or a gate failure.
        // All three burn one slot from the retry budget.
        attempts.set(payload.scheme, used + 1);
        let failDetail: string;
        if (settled.outcome === "timeout") {
          failDetail = `attempt-timeout after ${attemptTimeoutMs}ms`;
        } else if (settled.outcome === "error") {
          failDetail = `pipeline-error: ${settled.error.message}`;
        } else if (!settled.value.ok) {
          failDetail = `${settled.value.gate}: ${settled.value.detail}`;
        } else {
          // Unreachable — the prior block returned on settled.value.ok.
          failDetail = "unknown";
        }
        await appendLifecycleEvent(
          {
            ts: deps.host.clock(),
            kind: "regen-queued",
            scheme: payload.scheme,
            version: payload.schema_version,
            detail: failDetail,
          },
          deps.host.audit,
        );
        if (used + 1 >= retryBudget) {
          await appendLifecycleEvent(
            {
              ts: deps.host.clock(),
              kind: "lifecycle-exhausted",
              scheme: payload.scheme,
              version: payload.schema_version,
              detail: `attempts=${used + 1}/${retryBudget}`,
            },
            deps.host.audit,
          );
          deps.onExhausted?.(payload.scheme);
        }
      } catch (err) {
        recordError(err);
      }
    })();
  };

  const handleBoundaryError = (info: LifecycleBoundaryEvent): void => {
    void (async () => {
      try {
        await markBrokenFromBoundary(info, deps.host);
        const used = attempts.get(info.scheme) ?? 0;
        attempts.set(info.scheme, used + 1);
      } catch (err) {
        recordError(err);
      }
    })();
  };

  const unsubGenerated = deps.source.onAdapterGenerated(handleGenerated);
  const unsubBoundary = deps.source.onBoundaryError(handleBoundaryError);

  return {
    stop(): void {
      unsubGenerated();
      unsubBoundary();
    },
    attempts(scheme: string): number {
      return attempts.get(scheme) ?? 0;
    },
  };
}
