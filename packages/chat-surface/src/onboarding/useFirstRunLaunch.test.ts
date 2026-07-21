// useFirstRunLaunch — run-create + queued-deferral + ~1.5s handoff (PRD-P3 §6.4).

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  FirstRunLaunchResult,
  FirstRunRunsPort,
} from "./ports/FirstRunRunsPort";
import { useFirstRunLaunch } from "./useFirstRunLaunch";

const RESULT: FirstRunLaunchResult = { conversationId: "c1", runId: "r1" };
const PAYLOAD = { text: "watch my wallet", attachments: [] as const };

function runsPort(
  createFirstRun: FirstRunRunsPort["createFirstRun"],
): FirstRunRunsPort {
  return { createFirstRun };
}

describe("useFirstRunLaunch", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("ready → create once → handoff → onComplete(result) after the delay", async () => {
    const onComplete = vi.fn();
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result } = renderHook(() =>
      useFirstRunLaunch({
        runs: runsPort(createFirstRun),
        modelReady: true,
        model: null,
        onComplete,
        handoffDelayMs: 1500,
      }),
    );

    await act(async () => {
      result.current.launch(PAYLOAD);
    });

    expect(createFirstRun).toHaveBeenCalledTimes(1);
    expect(createFirstRun).toHaveBeenCalledWith({
      userInput: "watch my wallet",
      model: null,
      attachments: [],
    });
    expect(result.current.phase).toBe("handoff");
    expect(onComplete).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith(RESULT);
  });

  it("not ready → queued (no create) → modelReady flips → create fires → handoff", async () => {
    const onComplete = vi.fn();
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result, rerender } = renderHook(
      ({ modelReady }: { modelReady: boolean }) =>
        useFirstRunLaunch({
          runs: runsPort(createFirstRun),
          modelReady,
          model: null,
          onComplete,
        }),
      { initialProps: { modelReady: false } },
    );

    act(() => {
      result.current.launch(PAYLOAD);
    });
    expect(result.current.phase).toBe("queued");
    expect(createFirstRun).not.toHaveBeenCalled();

    await act(async () => {
      rerender({ modelReady: true });
    });
    expect(createFirstRun).toHaveBeenCalledTimes(1);
    expect(result.current.phase).toBe("handoff");
  });

  it("create rejection → phase 'error' + parsed StartRunError", async () => {
    const onComplete = vi.fn();
    const createFirstRun = vi.fn().mockRejectedValue(
      new Error(
        JSON.stringify({
          detail: {
            code: "configuration_error",
            safe_message:
              "Missing API key for model provider 'openai'. Add one in Settings -> Provider keys.",
          },
        }),
      ),
    );
    const { result } = renderHook(() =>
      useFirstRunLaunch({
        runs: runsPort(createFirstRun),
        modelReady: true,
        model: null,
        onComplete,
      }),
    );

    await act(async () => {
      result.current.launch(PAYLOAD);
    });

    expect(result.current.phase).toBe("error");
    expect(result.current.error?.code).toBe("configuration_error");
    expect(result.current.error?.message).toContain(
      "Missing API key for model provider 'openai'",
    );
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("a second launch while non-composing is a no-op (double-send guard)", async () => {
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result } = renderHook(() =>
      useFirstRunLaunch({
        runs: runsPort(createFirstRun),
        modelReady: true,
        model: null,
        onComplete: vi.fn(),
      }),
    );

    await act(async () => {
      result.current.launch(PAYLOAD);
    });
    // phase is now "handoff" — a second launch must not spawn a second create.
    act(() => {
      result.current.launch(PAYLOAD);
    });
    expect(createFirstRun).toHaveBeenCalledTimes(1);
  });

  it("reset() cancels the pending handoff timer", async () => {
    const onComplete = vi.fn();
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result } = renderHook(() =>
      useFirstRunLaunch({
        runs: runsPort(createFirstRun),
        modelReady: true,
        model: null,
        onComplete,
        handoffDelayMs: 1500,
      }),
    );

    await act(async () => {
      result.current.launch(PAYLOAD);
    });
    expect(result.current.phase).toBe("handoff");

    act(() => {
      result.current.reset();
    });
    expect(result.current.phase).toBe("composing");

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onComplete).not.toHaveBeenCalled();
  });
});
