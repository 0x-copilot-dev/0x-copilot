// useFirstRunLaunch — run-create + queued-deferral + ~1.5s handoff (PRD-P3 §6.4).

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  FirstRunLaunchResult,
  FirstRunRunsPort,
} from "./ports/FirstRunRunsPort";
import { useFirstRunLaunch } from "./useFirstRunLaunch";

const RESULT: FirstRunLaunchResult = { conversationId: "c1", runId: "r1" };
const PAYLOAD = {
  text: "watch my wallet",
  attachments: [] as const,
  webSearchEnabled: true,
};

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
      webSearchEnabled: true,
      connectorScopes: undefined,
    });
    expect(result.current.phase).toBe("handoff");
    expect(onComplete).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith(RESULT);
  });

  it("threads webSearchEnabled=false + connectorScopes into createFirstRun (P4)", async () => {
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
      result.current.launch({
        text: "no web",
        attachments: [],
        webSearchEnabled: false,
        connectorScopes: { "seed:sheets": [] },
      });
    });

    expect(createFirstRun).toHaveBeenCalledWith({
      userInput: "no web",
      model: null,
      attachments: [],
      webSearchEnabled: false,
      connectorScopes: { "seed:sheets": [] },
    });
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

  it("a second launch after a create succeeded is a no-op (double-send guard)", async () => {
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

  // --- PRD-P8 §7 — killing the permanent "Queued" hang -------------------

  it("a fast double-Enter with a create in flight still creates exactly ONE run", () => {
    // The guard's original purpose, pinned: `starting` owns an in-flight create,
    // so the second Enter is swallowed even though the guard is now narrower.
    const createFirstRun = vi.fn(
      () => new Promise<FirstRunLaunchResult>(() => undefined), // never settles
    );
    const { result } = renderHook(() =>
      useFirstRunLaunch({
        runs: runsPort(createFirstRun),
        modelReady: true,
        model: null,
        onComplete: vi.fn(),
      }),
    );

    act(() => {
      result.current.launch(PAYLOAD);
      result.current.launch(PAYLOAD);
    });

    expect(createFirstRun).toHaveBeenCalledTimes(1);
    expect(result.current.phase).toBe("starting");
  });

  it("a fast double-Enter while queued still creates exactly ONE run when the model lands", async () => {
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result, rerender } = renderHook(
      ({ modelReady }: { modelReady: boolean }) =>
        useFirstRunLaunch({
          runs: runsPort(createFirstRun),
          modelReady,
          model: null,
          onComplete: vi.fn(),
        }),
      { initialProps: { modelReady: false } },
    );

    act(() => {
      result.current.launch(PAYLOAD);
      result.current.launch(PAYLOAD);
    });
    expect(result.current.phase).toBe("queued");

    await act(async () => {
      rerender({ modelReady: true });
    });
    expect(createFirstRun).toHaveBeenCalledTimes(1);
  });

  it("queued + modelBlocked → exits to 'blocked' instead of waiting forever", () => {
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result, rerender } = renderHook(
      ({
        modelReady,
        modelBlocked,
      }: {
        modelReady: boolean;
        modelBlocked: boolean;
      }) =>
        useFirstRunLaunch({
          runs: runsPort(createFirstRun),
          modelReady,
          modelBlocked,
          model: null,
          onComplete: vi.fn(),
        }),
      { initialProps: { modelReady: false, modelBlocked: false } },
    );

    act(() => {
      result.current.launch(PAYLOAD);
    });
    expect(result.current.phase).toBe("queued");

    // The runtime died / the pull failed terminally — the model is not coming.
    act(() => {
      rerender({ modelReady: false, modelBlocked: true });
    });
    expect(result.current.phase).toBe("blocked");
    expect(createFirstRun).not.toHaveBeenCalled();
  });

  it("blocked → the model lands anyway → the held payload still fires once", async () => {
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result, rerender } = renderHook(
      ({
        modelReady,
        modelBlocked,
      }: {
        modelReady: boolean;
        modelBlocked: boolean;
      }) =>
        useFirstRunLaunch({
          runs: runsPort(createFirstRun),
          modelReady,
          modelBlocked,
          model: null,
          onComplete: vi.fn(),
        }),
      { initialProps: { modelReady: false, modelBlocked: false } },
    );

    act(() => {
      result.current.launch(PAYLOAD);
    });
    act(() => {
      rerender({ modelReady: false, modelBlocked: true });
    });
    expect(result.current.phase).toBe("blocked");

    // Ollama restarted → the pull resumed → the model landed.
    await act(async () => {
      rerender({ modelReady: true, modelBlocked: false });
    });
    expect(createFirstRun).toHaveBeenCalledTimes(1);
    expect(result.current.phase).toBe("handoff");
  });

  it("a stalled user can re-submit from 'blocked' (the guard no longer swallows it)", async () => {
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result, rerender } = renderHook(
      ({
        modelReady,
        modelBlocked,
      }: {
        modelReady: boolean;
        modelBlocked: boolean;
      }) =>
        useFirstRunLaunch({
          runs: runsPort(createFirstRun),
          modelReady,
          modelBlocked,
          model: null,
          onComplete: vi.fn(),
        }),
      { initialProps: { modelReady: false, modelBlocked: true } },
    );

    act(() => {
      result.current.launch(PAYLOAD);
    });
    expect(result.current.phase).toBe("blocked");

    // Re-submitting while still stalled is accepted and re-arms the deferral
    // with the NEW text (pre-P8 this was silently swallowed forever).
    act(() => {
      result.current.launch({ ...PAYLOAD, text: "second try" });
    });
    expect(result.current.phase).toBe("blocked");

    await act(async () => {
      rerender({ modelReady: true, modelBlocked: false });
    });
    expect(createFirstRun).toHaveBeenCalledTimes(1);
    expect(createFirstRun).toHaveBeenCalledWith(
      expect.objectContaining({ userInput: "second try" }),
    );
  });

  it("re-submits straight from 'error' without a reset()", async () => {
    const createFirstRun = vi
      .fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue(RESULT);
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
    expect(result.current.phase).toBe("error");

    await act(async () => {
      result.current.launch(PAYLOAD);
    });
    expect(createFirstRun).toHaveBeenCalledTimes(2);
    expect(result.current.phase).toBe("handoff");
  });

  it("modelBlocked is optional — omitting it keeps the pre-P8 queued hold", () => {
    const createFirstRun = vi.fn().mockResolvedValue(RESULT);
    const { result } = renderHook(() =>
      useFirstRunLaunch({
        runs: runsPort(createFirstRun),
        modelReady: false,
        model: null,
        onComplete: vi.fn(),
      }),
    );

    act(() => {
      result.current.launch(PAYLOAD);
    });
    expect(result.current.phase).toBe("queued");
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
