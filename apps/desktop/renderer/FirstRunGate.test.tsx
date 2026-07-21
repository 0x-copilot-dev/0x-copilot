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

import { CHANNELS } from "@0x-copilot/chat-transport";

import type { WindowBridge } from "../preload/window-bridge-types";
import { FIRST_RUN_CHANNELS } from "../main/services/first-run-channels";
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
        renderFirstRun={(onComplete) => (
          <button type="button" data-testid="finish" onClick={onComplete}>
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
});
