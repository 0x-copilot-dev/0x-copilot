import type { SaaSRendererAdapter } from "@enterprise-search/chat-surface";

// Q4 (PRD §9.5.1). Run once before activation. Render the adapter against a
// synthetic minimal state + diff and refuse to install if it throws, exceeds
// the budget, or returns a non-element. Production wires `executor` to 6A's
// `Tier2Loader` Worker so the budget is preemptively enforceable (D29). Tests
// inject a synchronous fake executor.

export type SmokeMethod = "renderCurrent" | "renderDiff";

export type SmokeFailKind = "throw" | "timeout" | "not-element";

export interface SmokeRenderOk {
  readonly ok: true;
}

export interface SmokeRenderFail {
  readonly ok: false;
  readonly kind: SmokeFailKind;
  readonly error: Error;
  readonly method: SmokeMethod;
}

export interface SmokeRenderExecutor {
  execute(
    adapter: SaaSRendererAdapter,
    payload: { method: SmokeMethod; input: unknown },
    budgetMs: number,
  ): Promise<{ ok: true } | { ok: false; kind: SmokeFailKind; error: Error }>;
}

class StubSmokeRenderExecutor implements SmokeRenderExecutor {
  async execute(): Promise<
    { ok: true } | { ok: false; kind: SmokeFailKind; error: Error }
  > {
    return {
      ok: false,
      kind: "throw",
      error: new Error(
        "smoke-render executor is not wired (6A Tier2Loader pending). Refusing install fails-closed (D29).",
      ),
    };
  }
}

let defaultExecutor: SmokeRenderExecutor = new StubSmokeRenderExecutor();

export function setDefaultSmokeRenderExecutor(
  executor: SmokeRenderExecutor,
): void {
  defaultExecutor = executor;
}

export const DEFAULT_SMOKE_BUDGET_MS = 100;

export async function runSmokeRender(
  adapter: SaaSRendererAdapter,
  synthState: unknown,
  synthDiff: unknown,
  opts?: { executor?: SmokeRenderExecutor; budgetMs?: number },
): Promise<SmokeRenderOk | SmokeRenderFail> {
  const executor = opts?.executor ?? defaultExecutor;
  const budgetMs = opts?.budgetMs ?? DEFAULT_SMOKE_BUDGET_MS;

  const current = await executor.execute(
    adapter,
    { method: "renderCurrent", input: synthState },
    budgetMs,
  );
  if (!current.ok) {
    return {
      ok: false,
      kind: current.kind,
      error: current.error,
      method: "renderCurrent",
    };
  }

  const diff = await executor.execute(
    adapter,
    { method: "renderDiff", input: synthDiff },
    budgetMs,
  );
  if (!diff.ok) {
    return {
      ok: false,
      kind: diff.kind,
      error: diff.error,
      method: "renderDiff",
    };
  }

  return { ok: true };
}
