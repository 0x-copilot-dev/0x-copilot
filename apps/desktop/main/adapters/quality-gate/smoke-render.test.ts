// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { SaaSRendererAdapter } from "@enterprise-search/chat-surface";

import {
  DEFAULT_SMOKE_BUDGET_MS,
  runSmokeRender,
  type SmokeFailKind,
  type SmokeMethod,
  type SmokeRenderExecutor,
} from "./smoke-render";

function makeAdapter(): SaaSRendererAdapter {
  return {
    scheme: "email",
    matches: (uri: string) => uri.startsWith("email://"),
    renderCurrent: () =>
      ({ type: "div", props: {}, key: null }) as unknown as ReturnType<
        SaaSRendererAdapter["renderCurrent"]
      >,
    renderDiff: () =>
      ({ type: "div", props: {}, key: null }) as unknown as ReturnType<
        SaaSRendererAdapter["renderDiff"]
      >,
    metadata: { origin: "agent-generated", schemaVersion: 1 },
  };
}

// Real adapter execution lives inside 6A's Worker. The tests verify the GATE
// — that when an executor reports throw / timeout / not-element, the result
// surfaces correctly with the right method name.
function fakeExecutor(
  outcomes: Record<
    SmokeMethod,
    | { ok: true }
    | { ok: false; kind: SmokeFailKind; error: Error }
    | { ok: false; kind: SmokeFailKind; error: Error; delayMs: number }
  >,
  observed?: { calls: Array<{ method: SmokeMethod; budgetMs: number }> },
): SmokeRenderExecutor {
  return {
    async execute(_adapter, payload, budgetMs) {
      observed?.calls.push({ method: payload.method, budgetMs });
      const o = outcomes[payload.method];
      if ("delayMs" in o) {
        await new Promise((r) => setTimeout(r, o.delayMs));
      }
      return o;
    },
  };
}

describe("Q4 — runSmokeRender", () => {
  it("returns ok when both methods render successfully", async () => {
    const result = await runSmokeRender(
      makeAdapter(),
      { id: 1 },
      { changes: [] },
      {
        executor: fakeExecutor({
          renderCurrent: { ok: true },
          renderDiff: { ok: true },
        }),
      },
    );
    expect(result.ok).toBe(true);
  });

  it("fails when renderCurrent throws", async () => {
    const err = new Error("boom in renderCurrent");
    const result = await runSmokeRender(
      makeAdapter(),
      { id: 1 },
      { changes: [] },
      {
        executor: fakeExecutor({
          renderCurrent: { ok: false, kind: "throw", error: err },
          renderDiff: { ok: true },
        }),
      },
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("throw");
      expect(result.method).toBe("renderCurrent");
      expect(result.error).toBe(err);
    }
  });

  it("fails when renderDiff throws", async () => {
    const err = new Error("boom in renderDiff");
    const result = await runSmokeRender(
      makeAdapter(),
      { id: 1 },
      { changes: [] },
      {
        executor: fakeExecutor({
          renderCurrent: { ok: true },
          renderDiff: { ok: false, kind: "throw", error: err },
        }),
      },
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("throw");
      expect(result.method).toBe("renderDiff");
    }
  });

  it("fails with kind 'timeout' when an executor reports the budget was blown", async () => {
    const result = await runSmokeRender(
      makeAdapter(),
      { id: 1 },
      { changes: [] },
      {
        executor: fakeExecutor({
          renderCurrent: {
            ok: false,
            kind: "timeout",
            error: new Error("100ms budget exceeded"),
          },
          renderDiff: { ok: true },
        }),
      },
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("timeout");
      expect(result.method).toBe("renderCurrent");
    }
  });

  it("fails with kind 'not-element' when the worker returned a non-element value", async () => {
    const result = await runSmokeRender(
      makeAdapter(),
      { id: 1 },
      { changes: [] },
      {
        executor: fakeExecutor({
          renderCurrent: {
            ok: false,
            kind: "not-element",
            error: new Error("returned undefined, not a ReactElement"),
          },
          renderDiff: { ok: true },
        }),
      },
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("not-element");
    }
  });

  it("short-circuits on renderCurrent failure (renderDiff is not called)", async () => {
    const observed = {
      calls: [] as Array<{ method: SmokeMethod; budgetMs: number }>,
    };
    await runSmokeRender(
      makeAdapter(),
      { id: 1 },
      { changes: [] },
      {
        executor: fakeExecutor(
          {
            renderCurrent: {
              ok: false,
              kind: "throw",
              error: new Error("nope"),
            },
            renderDiff: { ok: true },
          },
          observed,
        ),
      },
    );
    expect(observed.calls.map((c) => c.method)).toEqual(["renderCurrent"]);
  });

  it("forwards the budget to the executor (default = 100 ms)", async () => {
    const observed = {
      calls: [] as Array<{ method: SmokeMethod; budgetMs: number }>,
    };
    await runSmokeRender(
      makeAdapter(),
      { id: 1 },
      { changes: [] },
      {
        executor: fakeExecutor(
          {
            renderCurrent: { ok: true },
            renderDiff: { ok: true },
          },
          observed,
        ),
      },
    );
    expect(observed.calls[0].budgetMs).toBe(DEFAULT_SMOKE_BUDGET_MS);
    expect(observed.calls[1].budgetMs).toBe(DEFAULT_SMOKE_BUDGET_MS);
  });

  it("forwards a custom budget", async () => {
    const observed = {
      calls: [] as Array<{ method: SmokeMethod; budgetMs: number }>,
    };
    await runSmokeRender(
      makeAdapter(),
      { id: 1 },
      { changes: [] },
      {
        executor: fakeExecutor(
          {
            renderCurrent: { ok: true },
            renderDiff: { ok: true },
          },
          observed,
        ),
        budgetMs: 50,
      },
    );
    expect(observed.calls.every((c) => c.budgetMs === 50)).toBe(true);
  });

  it("default executor fails-closed when 6A's Tier2Loader is not wired", async () => {
    // No executor injected → falls through to StubSmokeRenderExecutor which
    // refuses every render. Same fail-closed posture as the allowlist gate.
    const result = await runSmokeRender(makeAdapter(), {}, {});
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("throw");
      expect(result.error.message).toMatch(/not wired/);
    }
  });
});
