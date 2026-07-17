// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mountApp } from "./bootstrap";

type Handler = (payload: unknown) => void;

interface FakeBridgeControls {
  emit(channel: string, payload: unknown): void;
}

function installFakeBridge(
  invoke: (channel: string) => Promise<unknown> = () => Promise.resolve(null),
): FakeBridgeControls {
  const handlers = new Map<string, Handler[]>();
  (window as unknown as { bridge: unknown }).bridge = {
    ipc: {
      invoke,
      on: (channel: string, handler: Handler) => {
        const arr = handlers.get(channel) ?? [];
        arr.push(handler);
        handlers.set(channel, arr);
        return () => {
          const current = handlers.get(channel) ?? [];
          const idx = current.indexOf(handler);
          if (idx >= 0) current.splice(idx, 1);
        };
      },
    },
  };
  return {
    emit: (channel, payload) => {
      for (const handler of [...(handlers.get(channel) ?? [])]) {
        handler(payload);
      }
    },
  };
}

const BOOT_READY = { phase: "ready", message: "Ready", percent: 100 };

describe("renderer bootstrap", () => {
  let container: HTMLElement | null = null;
  let unmount: (() => void) | null = null;

  beforeEach(() => {
    installFakeBridge();
  });

  afterEach(() => {
    if (unmount !== null) {
      const u = unmount;
      act(() => {
        u();
      });
    }
    unmount = null;
    container?.remove();
    container = null;
    delete (window as unknown as { bridge?: unknown }).bridge;
  });

  it("shows the boot screen until main pushes boot.status ready", async () => {
    const controls = installFakeBridge();
    container = document.createElement("div");
    container.id = "root";
    document.body.appendChild(container);

    await act(async () => {
      unmount = mountApp(container as HTMLElement);
    });

    // No boot status yet -> boot gate, no sign-in.
    expect(container.querySelector("[data-testid='boot-gate']")).not.toBeNull();
    expect(container.querySelector("[data-testid='sign-in-gate']")).toBeNull();

    await act(async () => {
      controls.emit("boot.status", BOOT_READY);
    });
    expect(container.querySelector("[data-testid='boot-gate']")).toBeNull();
    expect(
      container.querySelector("[data-testid='sign-in-gate']"),
    ).not.toBeNull();
  });

  it("mounts the sign-in gate while no session is present", async () => {
    const controls = installFakeBridge();
    container = document.createElement("div");
    container.id = "root";
    document.body.appendChild(container);

    await act(async () => {
      unmount = mountApp(container as HTMLElement);
    });
    await act(async () => {
      controls.emit("boot.status", BOOT_READY);
    });

    // jsdom's microtasks have run by now; the initial getSession resolved
    // to null, so we should be on the anon screen.
    await act(async () => {
      await Promise.resolve();
    });

    const gate = container.querySelector("[data-testid='sign-in-gate']");
    expect(gate).not.toBeNull();
    const button = container.querySelector(
      "[data-testid='sign-in-button']",
    ) as HTMLButtonElement | null;
    expect(button).not.toBeNull();
  });

  it("mounts ChatShell with the desktop placeholder once a session is present", async () => {
    const controls = installFakeBridge((channel: string) => {
      if (channel === "auth.get-session") {
        return Promise.resolve({
          workspaceId: "org_acme",
          expiresAt: Date.now() + 60_000,
          displayName: "Sarah",
          email: "sarah@acme.test",
        });
      }
      return Promise.resolve(null);
    });

    container = document.createElement("div");
    container.id = "root";
    document.body.appendChild(container);

    await act(async () => {
      unmount = mountApp(container as HTMLElement);
    });
    await act(async () => {
      controls.emit("boot.status", BOOT_READY);
    });

    await act(async () => {
      await Promise.resolve();
    });

    const placeholder = container.querySelector(
      "[data-testid='desktop-placeholder']",
    );
    expect(placeholder).not.toBeNull();
    expect(placeholder?.textContent).toContain("Atlas desktop");
  });

  it("shows the fatal boot screen when the supervisor reports a fatal status", async () => {
    const controls = installFakeBridge();
    container = document.createElement("div");
    container.id = "root";
    document.body.appendChild(container);

    await act(async () => {
      unmount = mountApp(container as HTMLElement);
    });
    await act(async () => {
      controls.emit("boot.status", {
        phase: "services",
        message: "backend crashed 5 times within 300s — giving up",
        percent: 60,
        fatal: true,
      });
    });

    expect(
      container.querySelector("[data-testid='boot-fatal']"),
    ).not.toBeNull();
    expect(container.querySelector("[data-testid='sign-in-gate']")).toBeNull();
  });
});
