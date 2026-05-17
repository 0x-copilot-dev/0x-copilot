// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { SaaSRendererAdapter } from "@enterprise-search/chat-surface";

import { wrapWithBoundary, type BoundaryError } from "./error-boundary";

function okAdapter(): SaaSRendererAdapter {
  return {
    scheme: "email",
    matches: (uri: string) => uri.startsWith("email://"),
    renderCurrent: () =>
      ({ type: "div", props: { children: "current" }, key: null }) as never,
    renderDiff: () =>
      ({ type: "div", props: { children: "diff" }, key: null }) as never,
    metadata: { origin: "agent-generated", schemaVersion: 3 },
  };
}

function throwingAdapter(
  method: "renderCurrent" | "renderDiff",
  err: Error,
): SaaSRendererAdapter {
  const base = okAdapter();
  return {
    ...base,
    renderCurrent:
      method === "renderCurrent"
        ? () => {
            throw err;
          }
        : base.renderCurrent,
    renderDiff:
      method === "renderDiff"
        ? () => {
            throw err;
          }
        : base.renderDiff,
  };
}

describe("Q3/Q5 — wrapWithBoundary", () => {
  it("passes through a successful renderCurrent and renderDiff", () => {
    const onError = vi.fn();
    const wrapped = wrapWithBoundary(okAdapter(), onError);
    const a = wrapped.renderCurrent({});
    const b = wrapped.renderDiff({});
    expect(a).toBeTruthy();
    expect(b).toBeTruthy();
    expect(onError).not.toHaveBeenCalled();
  });

  it("fires onError with full context when renderCurrent throws", () => {
    const err = new Error("render-current boom");
    const onError = vi.fn<(info: BoundaryError) => void>();
    const wrapped = wrapWithBoundary(
      throwingAdapter("renderCurrent", err),
      onError,
    );
    expect(() => wrapped.renderCurrent({})).toThrow(err);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatchObject({
      scheme: "email",
      version: 3,
      method: "renderCurrent",
      error: err,
    });
  });

  it("fires onError with full context when renderDiff throws", () => {
    const err = new Error("render-diff boom");
    const onError = vi.fn<(info: BoundaryError) => void>();
    const wrapped = wrapWithBoundary(
      throwingAdapter("renderDiff", err),
      onError,
    );
    expect(() => wrapped.renderDiff({})).toThrow(err);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].method).toBe("renderDiff");
    expect(onError.mock.calls[0][0].error).toBe(err);
  });

  it("rethrows so the host's React error boundary still catches", () => {
    const err = new Error("rethrow me");
    const wrapped = wrapWithBoundary(
      throwingAdapter("renderCurrent", err),
      () => {},
    );
    expect(() => wrapped.renderCurrent({})).toThrowError("rethrow me");
  });

  it("does not let the onError listener throwing prevent the rethrow", () => {
    const err = new Error("adapter error");
    const wrapped = wrapWithBoundary(
      throwingAdapter("renderCurrent", err),
      () => {
        throw new Error("logger blew up");
      },
    );
    // The original adapter error must still bubble — listener faults are
    // swallowed so audit failures don't mask renderer faults.
    expect(() => wrapped.renderCurrent({})).toThrowError("adapter error");
  });

  it("preserves the adapter's identity (scheme, matches, metadata)", () => {
    const original = okAdapter();
    const wrapped = wrapWithBoundary(original, () => {});
    expect(wrapped.scheme).toBe(original.scheme);
    expect(wrapped.metadata).toEqual(original.metadata);
    expect(wrapped.matches("email://x")).toBe(true);
    expect(wrapped.matches("slack://x")).toBe(false);
  });

  it("normalizes non-Error throws to Error instances in the listener payload", () => {
    const base = okAdapter();
    const stringThrower: SaaSRendererAdapter = {
      ...base,
      renderCurrent: () => {
        throw "plain string failure";
      },
    };
    const onError = vi.fn<(info: BoundaryError) => void>();
    const wrapped = wrapWithBoundary(stringThrower, onError);
    expect(() => wrapped.renderCurrent({})).toThrow();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].error).toBeInstanceOf(Error);
    expect(onError.mock.calls[0][0].error.message).toBe("plain string failure");
  });
});
