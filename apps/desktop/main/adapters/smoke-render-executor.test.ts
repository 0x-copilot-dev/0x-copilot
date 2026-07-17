// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

import {
  MainProcessSmokeRenderExecutor,
  SYNTHETIC_SMOKE_DIFF,
  SYNTHETIC_SMOKE_STATE,
} from "./smoke-render-executor";

function fakeReactElement(): unknown {
  return { type: "div", props: { children: [] }, key: null };
}

function adapterWith(
  renderCurrent: (state: unknown) => unknown,
  renderDiff: (diff: unknown) => unknown,
): SaaSRendererAdapter {
  return {
    scheme: "email",
    matches: (uri: string) => uri.startsWith("email://"),
    renderCurrent: renderCurrent as SaaSRendererAdapter["renderCurrent"],
    renderDiff: renderDiff as SaaSRendererAdapter["renderDiff"],
    metadata: { origin: "agent-generated", schemaVersion: 1 },
  };
}

describe("MainProcessSmokeRenderExecutor", () => {
  it("returns ok when the adapter call returns a plausible React element", async () => {
    const executor = new MainProcessSmokeRenderExecutor();
    const result = await executor.execute(
      adapterWith(
        () => fakeReactElement(),
        () => fakeReactElement(),
      ),
      { method: "renderCurrent", input: SYNTHETIC_SMOKE_STATE },
      100,
    );
    expect(result.ok).toBe(true);
  });

  it("returns kind=throw when the adapter throws", async () => {
    const executor = new MainProcessSmokeRenderExecutor();
    const result = await executor.execute(
      adapterWith(
        () => {
          throw new Error("boom");
        },
        () => fakeReactElement(),
      ),
      { method: "renderCurrent", input: SYNTHETIC_SMOKE_STATE },
      100,
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("throw");
      expect(result.error.message).toBe("boom");
    }
  });

  it("returns kind=not-element when the adapter returns a non-element", async () => {
    const executor = new MainProcessSmokeRenderExecutor();
    const result = await executor.execute(
      adapterWith(
        () => "just a string",
        () => fakeReactElement(),
      ),
      { method: "renderCurrent", input: SYNTHETIC_SMOKE_STATE },
      100,
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("not-element");
    }
  });

  it("returns kind=not-element when the adapter returns null", async () => {
    const executor = new MainProcessSmokeRenderExecutor();
    const result = await executor.execute(
      adapterWith(
        () => null,
        () => fakeReactElement(),
      ),
      { method: "renderCurrent", input: SYNTHETIC_SMOKE_STATE },
      100,
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("not-element");
    }
  });

  it("returns kind=timeout when the measured timer fires before resolution", async () => {
    let firedTimer: (() => void) | null = null;
    const setTimeoutSpy = vi.fn((cb: () => void) => {
      firedTimer = cb;
      return 1 as unknown;
    });
    const clearTimeoutSpy = vi.fn();
    const executor = new MainProcessSmokeRenderExecutor({
      setTimeout: setTimeoutSpy,
      clearTimeout: clearTimeoutSpy,
    });

    const resolveCallRef: { current: ((v: unknown) => void) | null } = {
      current: null,
    };
    const adapter = adapterWith(
      () =>
        new Promise((resolve) => {
          resolveCallRef.current = resolve;
        }),
      () => fakeReactElement(),
    );

    const execPromise = executor.execute(
      adapter,
      { method: "renderCurrent", input: SYNTHETIC_SMOKE_STATE },
      100,
    );

    expect(firedTimer).not.toBeNull();
    firedTimer!();
    const result = await execPromise;

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("timeout");
    }
    // Settle the pending promise to avoid an unhandled rejection.
    resolveCallRef.current?.(fakeReactElement());
  });

  it("forwards renderDiff payloads correctly", async () => {
    const seenInputs: unknown[] = [];
    const executor = new MainProcessSmokeRenderExecutor();
    await executor.execute(
      adapterWith(
        () => fakeReactElement(),
        (diff) => {
          seenInputs.push(diff);
          return fakeReactElement();
        },
      ),
      { method: "renderDiff", input: SYNTHETIC_SMOKE_DIFF },
      100,
    );
    expect(seenInputs).toEqual([SYNTHETIC_SMOKE_DIFF]);
  });

  it("clears the timer on success (no leak)", async () => {
    const clearTimeoutSpy = vi.fn();
    const executor = new MainProcessSmokeRenderExecutor({
      setTimeout: (cb: () => void, ms: number) => setTimeout(cb, ms) as unknown,
      clearTimeout: clearTimeoutSpy,
    });
    await executor.execute(
      adapterWith(
        () => fakeReactElement(),
        () => fakeReactElement(),
      ),
      { method: "renderCurrent", input: SYNTHETIC_SMOKE_STATE },
      100,
    );
    expect(clearTimeoutSpy).toHaveBeenCalledTimes(1);
  });
});
