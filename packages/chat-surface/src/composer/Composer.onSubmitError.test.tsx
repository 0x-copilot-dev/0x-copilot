// The composer onSubmit error channel (SSOT fix).
//
// `onSubmit` may be async (the host's `POST /v1/agent/runs`). Before this
// channel existed, the Composer called `onSubmit(...)` and dropped the
// returned promise on the floor, so a rejected dispatch (missing provider
// key, network error) became an UNHANDLED rejection — no error surfaced, no
// UI feedback. Hosts each had to wrap their own `try/catch` (see #158). These
// tests lock the fix at the SSOT: every onSubmit call site now captures the
// result and, if it is a promise, routes a rejection to `onSubmitError` (opt
// in) while always catching it so it can never be an unhandled rejection.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { Composer } from "./Composer";

// The package is substrate-agnostic (no @types/node), but this test asserts
// the Node-level "no unhandled rejection" guarantee. Declare the minimal
// `process` surface we use so tsc is happy without pulling in node types.
declare const process: {
  on(event: "unhandledRejection", listener: (reason: unknown) => void): void;
  off(event: "unhandledRejection", listener: (reason: unknown) => void): void;
};

function textarea(): HTMLTextAreaElement {
  return screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
}

function typeAndSend(text: string): void {
  fireEvent.change(textarea(), { target: { value: text } });
  fireEvent.click(screen.getByTestId("composer-send"));
}

describe("Composer onSubmit error channel", () => {
  // A hard guard that our .catch actually fires: if any promise rejection
  // escapes unhandled, fail the test loudly instead of letting it leak into
  // an unrelated test.
  let unhandled: unknown[];
  const onUnhandled = (reason: unknown): void => {
    unhandled.push(reason);
  };
  beforeEach(() => {
    unhandled = [];
    process.on("unhandledRejection", onUnhandled);
  });
  afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
  });

  it("routes a rejected async onSubmit to onSubmitError with the error", async () => {
    const boom = new Error("run-create failed");
    const onSubmit = vi.fn(() => Promise.reject(boom));
    const onSubmitError = vi.fn();
    render(<Composer onSubmit={onSubmit} onSubmitError={onSubmitError} />);

    typeAndSend("draft the launch note");

    await waitFor(() => expect(onSubmitError).toHaveBeenCalledTimes(1));
    expect(onSubmitError).toHaveBeenCalledWith(boom);
    // The composer still cleared synchronously on send (existing UX): error
    // handling is out-of-band and never blocks the clear.
    expect(textarea().value).toBe("");
  });

  it("does not call onSubmitError for a resolving async onSubmit", async () => {
    const onSubmit = vi.fn(() => Promise.resolve());
    const onSubmitError = vi.fn();
    render(<Composer onSubmit={onSubmit} onSubmitError={onSubmitError} />);

    typeAndSend("hello");

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    // Give the (resolved) promise a tick to settle before asserting.
    await Promise.resolve();
    expect(onSubmitError).not.toHaveBeenCalled();
  });

  it("catches a rejected onSubmit even when onSubmitError is absent (no unhandled rejection)", async () => {
    const onSubmit = vi.fn(() =>
      Promise.reject(new Error("swallowed but caught")),
    );
    // No onSubmitError wired — the pre-existing behaviour is preserved EXCEPT
    // the rejection is still caught so it never becomes an unhandled rejection.
    render(<Composer onSubmit={onSubmit} />);

    expect(() => typeAndSend("no channel")).not.toThrow();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    // Let the microtask + any macrotask that would surface an unhandled
    // rejection run.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(unhandled).toEqual([]);
  });

  it("leaves a synchronous (void) onSubmit untouched — no promise plumbing", async () => {
    const onSubmit = vi.fn((): void => undefined);
    const onSubmitError = vi.fn();
    render(<Composer onSubmit={onSubmit} onSubmitError={onSubmitError} />);

    typeAndSend("sync send");

    expect(onSubmit).toHaveBeenCalledTimes(1);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onSubmitError).not.toHaveBeenCalled();
    expect(unhandled).toEqual([]);
  });
});
