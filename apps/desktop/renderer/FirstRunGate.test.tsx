// FirstRunGate + FirstRunSurfaceMount (PRD-P1 §6.5). The gate reads the
// per-workspace first-run flag over IPC: complete → children (the shell);
// not-complete → the onboarding surface; skip/complete persist the flag then
// reveal children. The mount binds the shared FirstRunSurface to facade ports.

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** Every render's `useFirstRunLaunch` input + resulting phase, in order. */
const launchTrace = vi.hoisted(
  () =>
    [] as Array<{
      readonly modelReady: boolean;
      readonly modelBlocked: boolean | undefined;
      readonly phase: string;
    }>,
);

// Keep every real chat-surface export — the surface, the card and the hooks are
// exactly what these tests drive — but tap `useFirstRunLaunch` so the binder's
// inputs (and the phase they produce) are observable: the DOM shows the ack the
// phase produced, not the phase itself, so only the trace can prove the queued
// hold actually EXITED rather than being re-rendered. Mirrors the web host.
vi.mock("@0x-copilot/chat-surface", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@0x-copilot/chat-surface")>();
  return {
    ...actual,
    useFirstRunLaunch: (
      options: Parameters<typeof actual.useFirstRunLaunch>[0],
    ): ReturnType<typeof actual.useFirstRunLaunch> => {
      const result = actual.useFirstRunLaunch(options);
      launchTrace.push({
        modelReady: options.modelReady,
        modelBlocked: options.modelBlocked,
        phase: result.phase,
      });
      return result;
    },
  };
});

import { CHANNELS } from "@0x-copilot/chat-transport";
import { HashRouter } from "@0x-copilot/chat-surface";

import type { WindowBridge } from "../preload/window-bridge-types";
import { FIRST_RUN_CHANNELS } from "../main/services/first-run-channels";
import { OLLAMA_DOWNLOAD_URL } from "../main/services/ollama-download";
import { FirstRunGate, FirstRunSurfaceMount } from "./FirstRunGate";

interface Call {
  readonly channel: string;
  readonly payload: unknown;
}

function makeBridge(
  handlers: Record<string, (payload: unknown) => Promise<unknown>> = {},
): WindowBridge & { calls: Call[] } {
  const calls: Call[] = [];
  return {
    calls,
    ipc: {
      invoke<T = unknown>(channel: string, payload: unknown): Promise<T> {
        calls.push({ channel, payload });
        const handler = handlers[channel];
        if (!handler) return Promise.resolve(null as T);
        return handler(payload) as Promise<T>;
      },
      on: () => () => undefined,
    },
  };
}

function stubWindowBridge(bridge: WindowBridge): void {
  Object.defineProperty(window, "bridge", {
    value: bridge,
    configurable: true,
    writable: true,
  });
}

afterEach(() => {
  // Desktop vitest runs with `globals: false`, so testing-library's automatic
  // afterEach cleanup does not self-register — do it explicitly (else prior
  // renders accumulate in document.body and by-testid queries match multiples).
  cleanup();
  // Drop the stubbed bridge between tests.
  Reflect.deleteProperty(
    window as unknown as Record<string, unknown>,
    "bridge",
  );
});

describe("FirstRunGate", () => {
  it("renders children when the flag is already complete (returning user)", async () => {
    const bridge = makeBridge({
      [FIRST_RUN_CHANNELS.get]: async () => ({ completed: true }),
    });
    render(
      <FirstRunGate
        bridge={bridge}
        workspaceId="org_acme"
        router={new HashRouter()}
        renderFirstRun={() => <div data-testid="onboarding">onboarding</div>}
      >
        <div data-testid="shell">workspace shell</div>
      </FirstRunGate>,
    );

    await waitFor(() => expect(screen.queryByTestId("shell")).not.toBeNull());
    expect(screen.queryByTestId("onboarding")).toBeNull();
  });

  it("renders the onboarding surface when the flag is not complete", async () => {
    const bridge = makeBridge({
      [FIRST_RUN_CHANNELS.get]: async () => ({ completed: false }),
    });
    render(
      <FirstRunGate
        bridge={bridge}
        workspaceId="org_acme"
        router={new HashRouter()}
        renderFirstRun={() => <div data-testid="onboarding">onboarding</div>}
      >
        <div data-testid="shell">workspace shell</div>
      </FirstRunGate>,
    );

    await waitFor(() =>
      expect(screen.queryByTestId("onboarding")).not.toBeNull(),
    );
    expect(screen.queryByTestId("shell")).toBeNull();
  });

  it("persists the flag and reveals children on complete/skip", async () => {
    const bridge = makeBridge({
      [FIRST_RUN_CHANNELS.get]: async () => ({ completed: false }),
      [FIRST_RUN_CHANNELS.set]: async () => undefined,
    });
    render(
      <FirstRunGate
        bridge={bridge}
        workspaceId="org_acme"
        router={new HashRouter()}
        renderFirstRun={(onComplete) => (
          <button
            type="button"
            data-testid="finish"
            onClick={() => onComplete()}
          >
            finish
          </button>
        )}
      >
        <div data-testid="shell">workspace shell</div>
      </FirstRunGate>,
    );

    await waitFor(() => expect(screen.queryByTestId("finish")).not.toBeNull());
    act(() => {
      screen.getByTestId("finish").click();
    });

    await waitFor(() => expect(screen.queryByTestId("shell")).not.toBeNull());
    const setCall = bridge.calls.find(
      (c) => c.channel === FIRST_RUN_CHANNELS.set,
    );
    expect(setCall?.payload).toEqual({
      workspaceId: "org_acme",
      completed: true,
    });
  });

  it("binds the router to the created conversation before revealing the shell (Bug B)", async () => {
    // The gate navigates the App-owned router to the handed-off conversation
    // BEFORE flipping to the shell, so `conversationIdFromRoute` seeds the
    // cockpit onto the first run instead of `null` (empty standby).
    const bridge = makeBridge({
      [FIRST_RUN_CHANNELS.get]: async () => ({ completed: false }),
      [FIRST_RUN_CHANNELS.set]: async () => undefined,
    });
    const router = new HashRouter();
    render(
      <FirstRunGate
        bridge={bridge}
        workspaceId="org_acme"
        router={router}
        renderFirstRun={(onComplete) => (
          <button
            type="button"
            data-testid="finish"
            onClick={() =>
              onComplete({ conversationId: "conv_x", runId: "run_x" })
            }
          >
            finish
          </button>
        )}
      >
        <div data-testid="shell">workspace shell</div>
      </FirstRunGate>,
    );

    await waitFor(() => expect(screen.queryByTestId("finish")).not.toBeNull());
    act(() => {
      screen.getByTestId("finish").click();
    });

    await waitFor(() => expect(screen.queryByTestId("shell")).not.toBeNull());
    // The router now points at the created conversation — the seed the shell
    // reads on mount (a resultless finish would leave it null → standby).
    expect(router.current()).toEqual({
      kind: "conversation",
      conversationId: "conv_x",
    });
    router.dispose();
  });
});

describe("FirstRunSurfaceMount", () => {
  it("mounts the shared FirstRunSurface and skip calls onComplete", async () => {
    stubWindowBridge(makeBridge());
    const onComplete = vi.fn();
    render(
      <FirstRunSurfaceMount workspaceId="org_acme" onComplete={onComplete} />,
    );

    // The shared surface rendered (brand + skip control).
    expect(screen.getByTestId("first-run-surface")).not.toBeNull();
    expect(screen.getByTestId("first-run-brand")).not.toBeNull();

    act(() => {
      screen.getByTestId("first-run-skip").click();
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("wires the P2 local-model card into the gate (renderLocalCard slot)", async () => {
    // Bridge with no local-models handler → the status probe degrades; the card
    // still renders in the choice-stage gate (never a blank slot).
    stubWindowBridge(makeBridge());
    render(
      <FirstRunSurfaceMount workspaceId="org_acme" onComplete={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.queryByTestId("first-run-local-card")).not.toBeNull(),
    );
  });

  it("mounts the P3 OnboardingComposer in the composer stage (not the placeholder)", () => {
    // Empty bridge → catalog probes degrade to an empty catalog; the real
    // composer still renders (never the P1 placeholder).
    stubWindowBridge(makeBridge());
    render(
      <FirstRunSurfaceMount
        workspaceId="org_acme"
        onComplete={vi.fn()}
        initialStage="ready"
      />,
    );
    expect(screen.getByTestId("first-run-composer")).not.toBeNull();
    expect(screen.queryByTestId("first-run-composer-placeholder")).toBeNull();
  });

  it("flips to the acknowledgment on send (create → onSent → ack)", async () => {
    // Route the two-step create so the launch reaches `handoff`; the phase
    // effect then flips the surface to the real Acknowledgment.
    stubWindowBridge(
      makeBridge({
        [CHANNELS.transportRequest]: async (payload) => {
          const path = (payload as { path?: string }).path;
          if (path === "/v1/agent/conversations")
            return { conversation_id: "conv_x" };
          if (path === "/v1/agent/runs") return { run_id: "run_x" };
          return {}; // provider-keys / local-models catalog probes
        },
      }),
    );
    render(
      <FirstRunSurfaceMount
        workspaceId="org_acme"
        onComplete={vi.fn()}
        initialStage="ready"
      />,
    );

    fireEvent.change(screen.getByTestId("composer-textarea"), {
      target: { value: "watch my wallet" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send message/i }));

    await waitFor(() =>
      expect(screen.queryByTestId("first-run-ack")).not.toBeNull(),
    );
    expect(screen.queryByTestId("first-run-ack-placeholder")).toBeNull();
  });

  it("hands the created conversation identity to onComplete at the run handoff", async () => {
    // Bug B regression: the FTUE created the run (conv_x / run_x) but discarded
    // the identity at handoff, so the shell mounted unbound (empty standby) and
    // the first message was lost. `onComplete` must receive the created
    // `{ conversationId, runId }` so the host can bind the shell to that run.
    vi.useFakeTimers();
    try {
      stubWindowBridge(
        makeBridge({
          [CHANNELS.transportRequest]: async (payload) => {
            const path = (payload as { path?: string }).path;
            if (path === "/v1/agent/conversations")
              return { conversation_id: "conv_x" };
            if (path === "/v1/agent/runs") return { run_id: "run_x" };
            return {}; // provider-keys / local-models catalog probes
          },
        }),
      );
      const onComplete = vi.fn();
      render(
        <FirstRunSurfaceMount
          workspaceId="org_acme"
          onComplete={onComplete}
          initialStage="ready"
        />,
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      fireEvent.change(screen.getByTestId("composer-textarea"), {
        target: { value: "watch my wallet" },
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /Send message/i }));
        // Flush the two create POSTs (conversation → run) into the handoff hold.
        await vi.advanceTimersByTimeAsync(0);
      });

      // The ~1.5s handoff hold has NOT elapsed → identity not yet handed off.
      expect(onComplete).not.toHaveBeenCalled();

      // Advance past the handoff hold → the hook fires onComplete ONCE with the
      // created identity (never a resultless call that would strand the shell).
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(onComplete).toHaveBeenCalledTimes(1);
      expect(onComplete).toHaveBeenCalledWith({
        conversationId: "conv_x",
        runId: "run_x",
      });
    } finally {
      vi.useRealTimers();
    }
  });
});

// --- PRD-P8 §8: the two card seams this host must supply --------------------
//
// `FirstRunLocalCard` renders a control ONLY when its callback prop is present
// ("omitted means no button" — the card never ships a control that cannot
// work). So an unwired seam here is not a degraded state, it is a DEAD END:
// state ① with no "Get Ollama ↗" and state ③ with no way off the gate. These
// tests assert both controls actually exist in the desktop host, and that the
// external open is the argument-free channel rather than a URL the renderer
// hands over.

type StatusPatch = Record<string, unknown>;

function localModelsBridge(statuses: readonly StatusPatch[]) {
  let call = 0;
  return makeBridge({
    [CHANNELS.transportRequest]: async (payload) => {
      const { path } = payload as { path?: string };
      if (path === "/v1/local-models/status") {
        // Successive probes walk the list, then hold on the last entry.
        const status = statuses[Math.min(call, statuses.length - 1)];
        call += 1;
        return status;
      }
      if (path === "/v1/local-models") return { models: [] };
      return {};
    },
    // The pull opens an SSE subscription that never yields a frame — enough to
    // park the hook in `phase: "downloading"`, which is state ③.
    [CHANNELS.transportSubscribe]: async () => ({ ok: true }),
    [CHANNELS.transportUnsubscribe]: async () => ({ removed: true }),
  });
}

const NOT_INSTALLED: StatusPatch = {
  enabled: true,
  ollama_running: false,
  ollama_version: null,
  runtime_state: "not_installed",
  runtime_managed: true,
};

const RUNNING: StatusPatch = {
  enabled: true,
  ollama_running: true,
  ollama_version: "0.5.0",
  runtime_state: "running",
  runtime_managed: true,
};

describe("FirstRunSurfaceMount — P8 local-card seams", () => {
  it("renders state ①'s Get Ollama button and opens it over the argument-free channel", async () => {
    const bridge = localModelsBridge([NOT_INSTALLED]);
    stubWindowBridge(bridge);
    render(
      <FirstRunSurfaceMount workspaceId="org_acme" onComplete={vi.fn()} />,
    );

    // ① — runtime absent: the watch line plus the host-brokered action.
    await waitFor(() =>
      expect(screen.queryByTestId("first-run-local-get-ollama")).not.toBeNull(),
    );

    act(() => {
      screen.getByTestId("first-run-local-get-ollama").click();
    });

    const opens = bridge.calls.filter(
      (c) => c.channel === FIRST_RUN_CHANNELS.openOllamaDownload,
    );
    expect(opens).toHaveLength(1);
    // The whole security property: the renderer names an INTENT, never a URL.
    // Main owns `OLLAMA_DOWNLOAD_URL`; nothing addressable crosses the bridge.
    expect(opens[0]?.payload).toEqual({});
    expect(JSON.stringify(opens[0]?.payload)).not.toContain(
      OLLAMA_DOWNLOAD_URL,
    );
    expect(JSON.stringify(bridge.calls)).not.toContain("http");
  });

  it("renders state ③'s Continue → and advances to the composer without unmounting the gate first", async () => {
    // ① then ② on the poll: the hook arms auto-start on the not-installed
    // probe and fires it on the runtime edge (D4), so the pull begins with NO
    // click — which is exactly the case where the gate stays mounted and the
    // user needs `Continue →` to move on.
    vi.useFakeTimers();
    try {
      stubWindowBridge(localModelsBridge([NOT_INSTALLED, RUNNING]));
      render(
        <FirstRunSurfaceMount workspaceId="org_acme" onComplete={vi.fn()} />,
      );

      // Flush the mount probe (a promise chain, not a timer).
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.queryByTestId("first-run-local-watch")).not.toBeNull();

      // The 3s fast poll re-probes, sees the runtime, and auto-starts the pull.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_500);
      });

      expect(screen.queryByTestId("first-run-local-progress")).not.toBeNull();
      const cont = screen.getByTestId("first-run-local-continue");

      act(() => {
        cont.click();
      });

      // D4a-1: advancing to the composer, with the download still in flight.
      expect(screen.queryByTestId("first-run-composer")).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

// --- PRD-P8 §7: killing the permanent "Queued" hang on this host ------------
//
// The desktop binder must thread the hook's "the model is NOT landing" fact
// (`blocked !== null || runtime === "stopped"`) into BOTH `useFirstRunLaunch`
// (so the queued hold can exit) and `FirstRunSurface`'s `localModelBlocked`
// (so the composer/ack ctx stops claiming a download is in flight). Web derives
// it identically (`FirstRunSurfaceMount.tsx`). Without it the gap is SILENT:
// `modelBlocked` is optional on the hook, so desktop compiles and the ack keeps
// echoing "· downloading 40%" for a download that died.

const STOPPED: StatusPatch = {
  enabled: true,
  ollama_running: false,
  ollama_version: null,
  runtime_state: "stopped",
  runtime_managed: true,
};

/**
 * Like `localModelsBridge`, but the SSE subscription is drivable: it captures
 * the `subscriptionId` the IpcTransport registers and the `stream-event`
 * listener it attaches, so a test can push real pull frames through the real
 * transport + port + hook rather than stubbing any of them. Also answers the
 * two run-create POSTs so a send can reach the acknowledgment.
 */
function streamingLocalModelsBridge(statuses: readonly StatusPatch[]): {
  readonly bridge: WindowBridge & { calls: Call[] };
  emit(frame: Record<string, unknown>): Promise<void>;
} {
  let probe = 0;
  let subscriptionId: string | null = null;
  const listeners = new Map<string, Array<(payload: unknown) => void>>();
  const calls: Call[] = [];

  const bridge: WindowBridge & { calls: Call[] } = {
    calls,
    ipc: {
      invoke<T = unknown>(channel: string, payload: unknown): Promise<T> {
        calls.push({ channel, payload });
        if (channel === CHANNELS.transportRequest) {
          const { path } = payload as { path?: string };
          if (path === "/v1/local-models/status") {
            const status = statuses[Math.min(probe, statuses.length - 1)];
            probe += 1;
            return Promise.resolve(status as T);
          }
          if (path === "/v1/local-models") {
            return Promise.resolve({ models: [] } as T);
          }
          if (path === "/v1/agent/conversations") {
            return Promise.resolve({ conversation_id: "conv_1" } as T);
          }
          if (path === "/v1/agent/runs") {
            return Promise.resolve({ run_id: "run_1" } as T);
          }
          return Promise.resolve({} as T);
        }
        if (channel === CHANNELS.transportSubscribe) {
          subscriptionId =
            (payload as { subscriptionId?: string }).subscriptionId ?? null;
        }
        return Promise.resolve({} as T);
      },
      on(channel: string, handler: (payload: unknown) => void): () => void {
        const bucket = listeners.get(channel) ?? [];
        bucket.push(handler);
        listeners.set(channel, bucket);
        return () => {
          listeners.set(
            channel,
            (listeners.get(channel) ?? []).filter((h) => h !== handler),
          );
        };
      },
    },
  };

  return {
    bridge,
    async emit(frame: Record<string, unknown>): Promise<void> {
      if (subscriptionId === null) {
        throw new Error("no pull subscription is open");
      }
      const event = {
        subscriptionId,
        kind: "message",
        message: JSON.stringify(frame),
      };
      await act(async () => {
        for (const handler of listeners.get(CHANNELS.streamEvent) ?? []) {
          handler(event);
        }
        await vi.advanceTimersByTimeAsync(0);
      });
    },
  };
}

function pullFrame(
  over: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    sequence_no: 1,
    status: "pulling",
    bytes_total: 1000,
    bytes_completed: 400,
    speed_bps: null,
    eta_seconds: null,
    done: false,
    error: null,
    error_kind: null,
    ...over,
  };
}

describe("FirstRunSurfaceMount — P8 §7 modelBlocked threading", () => {
  it("stops the acknowledgment claiming a download once the runtime dies mid-pull", async () => {
    vi.useFakeTimers();
    launchTrace.length = 0;
    try {
      // ② on the first probe (runtime up, model absent), then a dead daemon on
      // every probe after the failure.
      const { bridge, emit } = streamingLocalModelsBridge([RUNNING, STOPPED]);
      stubWindowBridge(bridge);
      render(
        <FirstRunSurfaceMount workspaceId="org_acme" onComplete={vi.fn()} />,
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      // Explicit "Start download" → advances to the composer AND pulls.
      await act(async () => {
        screen.getByTestId("first-run-start-download").click();
        await vi.advanceTimersByTimeAsync(0);
      });
      await emit(pullFrame());

      // Nothing is wrong yet: the download is live and the model can still land.
      expect(launchTrace.at(-1)).toMatchObject({
        modelReady: false,
        modelBlocked: false,
      });

      // Send the first prompt while the model is still downloading → queued.
      fireEvent.change(screen.getByTestId("composer-textarea"), {
        target: { value: "Watch my wallet" },
      });
      await act(async () => {
        fireEvent.click(screen.getByLabelText("Send message"));
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(launchTrace.at(-1)?.phase).toBe("queued");
      expect(screen.getByTestId("first-run-ack")).toHaveTextContent(
        "Qwen 3 4B · downloading 40%",
      );

      // The daemon dies. Pre-P8 this was the permanent hang: the pct freezes,
      // `modelReady` never flips, and the queued phase had no exit.
      await emit(
        pullFrame({
          sequence_no: 2,
          status: "error",
          error: "connection refused",
          error_kind: "runtime_unreachable",
        }),
      );

      // The queued hold EXITED (this is the hang fix itself) …
      expect(launchTrace.at(-1)?.modelBlocked).toBe(true);
      expect(launchTrace.at(-1)?.phase).toBe("blocked");
      // … and the acknowledgment stops echoing a download that is not happening.
      const ack = screen.getByTestId("first-run-ack");
      expect(ack).toHaveTextContent("Qwen 3 4B · download paused at 40%");
      // The TITLE has to stop lying too, or the ack argues with itself:
      // "Queued — starts when the model lands" directly above "· paused".
      expect(ack.getAttribute("data-variant")).toBe("stalled");
      expect(screen.getByTestId("first-run-ack-title").textContent).toBe(
        "Held — the model isn't downloading",
      );
      // …and it is not a dead end: the action returns the composer, which the
      // narrowed double-launch guard then accepts a re-submit from.
      await act(async () => {
        screen.getByTestId("first-run-ack-back").click();
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.queryByTestId("first-run-ack")).toBeNull();
      expect(screen.queryByTestId("composer-textarea")).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  // `firstRunAckLines` interpolates the pct VERBATIM, and `pullPercent` is
  // got/total*100 — a real byte pct is fractional. The binder must round it
  // (web does the same, by floor) or the ack prints the full float.
  it("prints a whole-number download pct, never the raw float", async () => {
    vi.useFakeTimers();
    launchTrace.length = 0;
    try {
      const { bridge, emit } = streamingLocalModelsBridge([RUNNING]);
      stubWindowBridge(bridge);
      render(
        <FirstRunSurfaceMount workspaceId="org_acme" onComplete={vi.fn()} />,
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      await act(async () => {
        screen.getByTestId("first-run-start-download").click();
        await vi.advanceTimersByTimeAsync(0);
      });
      // 40.7% — fractional, and floored (not rounded) so the ack can never
      // over-claim progress the launch lane has not accepted as ready.
      await emit(pullFrame({ bytes_completed: 407 }));

      fireEvent.change(screen.getByTestId("composer-textarea"), {
        target: { value: "Watch my wallet" },
      });
      await act(async () => {
        fireEvent.click(screen.getByLabelText("Send message"));
        await vi.advanceTimersByTimeAsync(0);
      });

      const ack = screen.getByTestId("first-run-ack");
      expect(ack).toHaveTextContent("Qwen 3 4B · downloading 40%");
      expect(ack.textContent).not.toContain("40.7");
    } finally {
      vi.useRealTimers();
    }
  });
});
