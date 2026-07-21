// Web FirstRunGate — gating behavior (parity with the desktop FirstRunGate
// test). A returning user (flag set) renders children; a first-timer renders the
// onboarding surface until `onComplete` fires, which persists + swaps to the
// shell.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WebFirstRunStore } from "./firstRunStore";
import { FirstRunGate } from "./FirstRunGate";

function makeStore(overrides: Partial<WebFirstRunStore> = {}): WebFirstRunStore {
  return {
    isComplete: () => false,
    markComplete: vi.fn(),
    ...overrides,
  };
}

describe("FirstRunGate", () => {
  it("renders the shell (children) when onboarding is already complete", () => {
    const store = makeStore({ isComplete: () => true });
    render(
      <FirstRunGate
        store={store}
        renderFirstRun={() => <div data-testid="onboarding">onboarding</div>}
      >
        <div data-testid="shell">shell</div>
      </FirstRunGate>,
    );
    expect(screen.getByTestId("shell")).toBeInTheDocument();
    expect(screen.queryByTestId("onboarding")).not.toBeInTheDocument();
  });

  it("renders the onboarding surface when onboarding is incomplete", () => {
    const store = makeStore({ isComplete: () => false });
    render(
      <FirstRunGate
        store={store}
        renderFirstRun={() => <div data-testid="onboarding">onboarding</div>}
      >
        <div data-testid="shell">shell</div>
      </FirstRunGate>,
    );
    expect(screen.getByTestId("onboarding")).toBeInTheDocument();
    expect(screen.queryByTestId("shell")).not.toBeInTheDocument();
  });

  it("persists completion and swaps to the shell when onComplete fires", () => {
    const markComplete = vi.fn();
    const store = makeStore({ isComplete: () => false, markComplete });
    render(
      <FirstRunGate
        store={store}
        renderFirstRun={(onComplete) => (
          <button type="button" data-testid="finish" onClick={onComplete}>
            finish
          </button>
        )}
      >
        <div data-testid="shell">shell</div>
      </FirstRunGate>,
    );
    // First-run surface is shown.
    expect(screen.getByTestId("finish")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("finish"));

    // Completion persisted, and the shell replaces the surface without a reload.
    expect(markComplete).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("shell")).toBeInTheDocument();
    expect(screen.queryByTestId("finish")).not.toBeInTheDocument();
  });

  it("does not trap the user when the persist write throws", () => {
    const store = makeStore({
      isComplete: () => false,
      markComplete: vi.fn(() => {
        throw new Error("localStorage unavailable");
      }),
    });
    render(
      <FirstRunGate
        store={store}
        renderFirstRun={(onComplete) => (
          <button type="button" data-testid="finish" onClick={onComplete}>
            finish
          </button>
        )}
      >
        <div data-testid="shell">shell</div>
      </FirstRunGate>,
    );
    // A throwing write must not prevent the swap to the shell.
    expect(() => fireEvent.click(screen.getByTestId("finish"))).not.toThrow();
    expect(screen.getByTestId("shell")).toBeInTheDocument();
  });
});
