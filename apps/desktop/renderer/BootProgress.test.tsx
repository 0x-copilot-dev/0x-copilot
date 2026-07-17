// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { WindowBridge } from "@0x-copilot/chat-transport";

import { BootGate } from "./BootProgress";

type Emit = (payload: unknown) => void;

function makeBridge(): { bridge: WindowBridge; emit: Emit } {
  const handlers: Array<(payload: unknown) => void> = [];
  const bridge: WindowBridge = {
    ipc: {
      invoke: () => Promise.resolve(null as never),
      on: (channel, handler) => {
        if (channel === "boot.status") handlers.push(handler);
        return () => {
          const idx = handlers.indexOf(handler);
          if (idx >= 0) handlers.splice(idx, 1);
        };
      },
    },
  };
  return {
    bridge,
    emit: (payload) => {
      for (const handler of [...handlers]) handler(payload);
    },
  };
}

describe("BootGate", () => {
  let container: HTMLElement;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    if (root !== null) {
      const r = root;
      act(() => {
        r.unmount();
      });
      root = null;
    }
    container.remove();
  });

  function mount(bridge: WindowBridge): void {
    act(() => {
      root = createRoot(container);
      root.render(
        <BootGate bridge={bridge}>
          <div data-testid="app-content">app</div>
        </BootGate>,
      );
    });
  }

  it("shows the boot screen (not the app) before any status arrives", () => {
    const { bridge } = makeBridge();
    mount(bridge);
    expect(container.querySelector("[data-testid='boot-gate']")).not.toBeNull();
    expect(container.querySelector("[data-testid='app-content']")).toBeNull();
    expect(
      container.querySelector("[data-testid='boot-message']")?.textContent,
    ).toContain("Starting 0xCopilot");
  });

  it("renders progress updates as they stream in", () => {
    const { bridge, emit } = makeBridge();
    mount(bridge);
    act(() => {
      emit({
        phase: "postgres",
        message: "Starting local database…",
        percent: 25,
      });
    });
    expect(
      container.querySelector("[data-testid='boot-message']")?.textContent,
    ).toBe("Starting local database…");
    const bar = container.querySelector("[data-testid='boot-progress']");
    expect(bar?.getAttribute("aria-valuenow")).toBe("25");
    expect(container.querySelector("[data-testid='app-content']")).toBeNull();
  });

  it("mounts the app children once ready arrives", () => {
    const { bridge, emit } = makeBridge();
    mount(bridge);
    act(() => {
      emit({ phase: "ready", message: "Ready", percent: 100 });
    });
    expect(
      container.querySelector("[data-testid='app-content']"),
    ).not.toBeNull();
    expect(container.querySelector("[data-testid='boot-gate']")).toBeNull();
  });

  it("shows the fatal screen with the message and never recovers to progress", () => {
    const { bridge, emit } = makeBridge();
    mount(bridge);
    act(() => {
      emit({
        phase: "migrations",
        message: "migrations for backend exited with code 2",
        percent: 40,
        fatal: true,
      });
    });
    expect(
      container.querySelector("[data-testid='boot-fatal']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='boot-fatal-message']")
        ?.textContent,
    ).toContain("exited with code 2");
    // A later (stale) non-fatal push must not clear the fatal screen.
    act(() => {
      emit({ phase: "ready", message: "Ready", percent: 100 });
    });
    expect(
      container.querySelector("[data-testid='boot-fatal']"),
    ).not.toBeNull();
    expect(container.querySelector("[data-testid='app-content']")).toBeNull();
  });

  it("ignores malformed payloads", () => {
    const { bridge, emit } = makeBridge();
    mount(bridge);
    act(() => {
      emit({ phase: "nonsense", message: 42 });
      emit(null);
      emit("ready");
    });
    expect(container.querySelector("[data-testid='boot-gate']")).not.toBeNull();
    expect(container.querySelector("[data-testid='app-content']")).toBeNull();
  });
});
