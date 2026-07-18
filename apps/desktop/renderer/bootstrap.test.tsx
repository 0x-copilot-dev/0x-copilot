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

  // Drive the renderer past boot + sign-in into the mounted shell. Returns
  // once the profile-gated shell is on screen (default destination = Run).
  async function mountSignedInShell(): Promise<HTMLElement> {
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
    return container as HTMLElement;
  }

  it("mounts the profile-gated 6-destination shell on Run, without the placeholder", async () => {
    const root = await mountSignedInShell();

    // The static "phase 1" placeholder is no longer mounted (PR-2.6).
    expect(
      root.querySelector("[data-testid='desktop-placeholder']"),
    ).toBeNull();

    // Solo profile → exactly 6 rail destinations, in order.
    const railButtons = Array.from(
      root.querySelectorAll("[data-component='app-rail'] [data-destination]"),
    );
    expect(railButtons.map((b) => b.getAttribute("data-destination"))).toEqual([
      "run",
      "chats",
      "projects",
      "activity",
      "connectors",
      "tools",
    ]);

    // Landing destination is Run.
    expect(
      root.querySelector("[data-destination='run'][aria-current='page']"),
    ).not.toBeNull();

    // The destination outlet renders the Run surface (honest placeholder).
    const outlet = root.querySelector("[data-testid='destination-outlet']");
    expect(outlet).not.toBeNull();
    expect(outlet?.getAttribute("data-destination")).toBe("run");
    expect(
      root.querySelector("[data-testid='destination-placeholder-title']")
        ?.textContent,
    ).toBe("Run");
  });

  it("shows the rail-foot Settings + avatar and opens the Settings surface", async () => {
    const root = await mountSignedInShell();

    const settingsButton = root.querySelector(
      "[data-rail-action='settings']",
    ) as HTMLButtonElement | null;
    expect(settingsButton).not.toBeNull();
    expect(root.querySelector("[data-rail-me]")).not.toBeNull();
    // Settings surface is not mounted until the gear is clicked.
    expect(root.querySelector("[data-testid='settings-surface']")).toBeNull();

    await act(async () => {
      settingsButton?.click();
    });

    // Gear opens the (stub) Settings surface without a destination-outlet.
    expect(
      root.querySelector("[data-testid='settings-surface']"),
    ).not.toBeNull();
    expect(root.querySelector("[data-testid='destination-outlet']")).toBeNull();
  });

  it("swaps the outlet when a rail destination is clicked and leaves Settings", async () => {
    const root = await mountSignedInShell();

    // Open Settings first, then navigate away via the rail.
    await act(async () => {
      (
        root.querySelector("[data-rail-action='settings']") as HTMLButtonElement
      )?.click();
    });
    expect(
      root.querySelector("[data-testid='settings-surface']"),
    ).not.toBeNull();

    await act(async () => {
      (
        root.querySelector(
          "[data-component='app-rail'] [data-destination='activity']",
        ) as HTMLButtonElement
      )?.click();
    });

    // Settings closed; the outlet now shows the Activity surface.
    expect(root.querySelector("[data-testid='settings-surface']")).toBeNull();
    const outlet = root.querySelector("[data-testid='destination-outlet']");
    expect(outlet?.getAttribute("data-destination")).toBe("activity");
    expect(
      root.querySelector("[data-testid='destination-placeholder-title']")
        ?.textContent,
    ).toBe("Activity");
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
