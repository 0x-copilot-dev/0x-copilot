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

// PR-6.6: dispatch a DESIGN-SPEC §6 chord on `document` (where `useShellShortcuts`
// listens). `metaKey` stands in for the command modifier on every §6 chord.
function pressChord(key: string, opts: { shift?: boolean } = {}): void {
  act(() => {
    document.dispatchEvent(
      new KeyboardEvent("keydown", {
        key,
        metaKey: true,
        shiftKey: opts.shift ?? false,
        bubbles: true,
        cancelable: true,
      }),
    );
  });
}

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
      // First-run gate (P0/P1): a returning user's flag is already complete, so
      // the gate drops straight through to the shell these tests assert on. A
      // first-time user's onboarding surface is covered by FirstRunGate.test.tsx.
      if (channel === "first-run.get") {
        return Promise.resolve({ completed: true });
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

    // The destination outlet renders the real Run cockpit (PR-6.7), not a
    // phase placeholder: its root + header + idle empty composer are present.
    const outlet = root.querySelector("[data-testid='destination-outlet']");
    expect(outlet).not.toBeNull();
    expect(outlet?.getAttribute("data-destination")).toBe("run");
    expect(
      root.querySelector("[data-testid='run-destination']"),
    ).not.toBeNull();
    expect(root.querySelector("[data-testid='run-header']")).not.toBeNull();
    // No active run → the design's "What should we run first?" rich composer
    // (RunBinder injects `renderEmptyComposer`), not the plain goal card.
    expect(
      root.querySelector("[data-testid='run-empty-composer']"),
    ).not.toBeNull();
    expect(
      root.querySelector("[data-testid='first-run-composer']"),
    ).not.toBeNull();
    // No phase placeholder anywhere in the mounted shell.
    expect(
      root.querySelector("[data-testid='destination-placeholder']"),
    ).toBeNull();
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

  it("signs out from the Profile section: fires authSignOut and returns to the sign-in gate", async () => {
    // Record the IPC channels main sees, and hand back a live session for the
    // boot-time getSession so we land in the signed-in shell.
    const invoked: string[] = [];
    const controls = installFakeBridge((channel: string) => {
      invoked.push(channel);
      if (channel === "auth.get-session") {
        return Promise.resolve({
          workspaceId: "org_acme",
          expiresAt: Date.now() + 60_000,
          displayName: "Sarah",
          email: "sarah@acme.test",
        });
      }
      // Returning user — first-run flag complete, so the gate mounts the shell
      // (Settings/Profile) this test drives.
      if (channel === "first-run.get") {
        return Promise.resolve({ completed: true });
      }
      // auth.sign-out (and anything else) resolves cleanly.
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

    // Open Settings — Profile is the default section, so the Sign out CTA shows.
    await act(async () => {
      (
        container?.querySelector(
          "[data-rail-action='settings']",
        ) as HTMLButtonElement
      )?.click();
    });
    const signOutButton = container?.querySelector(
      "[data-testid='profile-signout']",
    ) as HTMLButtonElement | null;
    expect(signOutButton).not.toBeNull();

    await act(async () => {
      signOutButton?.click();
    });
    // Flush the authSignOut promise + the phase→anon transition.
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await Promise.resolve();
    });

    // The real sign-out IPC fired (not a dead click)…
    expect(invoked).toContain("auth.sign-out");
    // …and the app returned to the sign-in gate with Settings torn down.
    expect(
      container?.querySelector("[data-testid='sign-in-gate']"),
    ).not.toBeNull();
    expect(
      container?.querySelector("[data-testid='settings-surface']"),
    ).toBeNull();
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

    // Settings closed; the outlet now shows the real Activity surface (PR-6.7),
    // not a phase placeholder.
    expect(root.querySelector("[data-testid='settings-surface']")).toBeNull();
    const outlet = root.querySelector("[data-testid='destination-outlet']");
    expect(outlet?.getAttribute("data-destination")).toBe("activity");
    expect(
      root.querySelector("[data-testid='activity-destination']"),
    ).not.toBeNull();
    expect(
      root.querySelector("[data-testid='destination-placeholder']"),
    ).toBeNull();
  });

  // === PR-6.6: shell keyboard shortcuts wired via useShellShortcuts ===

  it("opens Settings on ⌘,", async () => {
    const root = await mountSignedInShell();
    expect(root.querySelector("[data-testid='settings-surface']")).toBeNull();

    pressChord(",");

    expect(
      root.querySelector("[data-testid='settings-surface']"),
    ).not.toBeNull();
    expect(root.querySelector("[data-testid='destination-outlet']")).toBeNull();
  });

  it("opens Settings at the local-models section on ⌘⇧M", async () => {
    const root = await mountSignedInShell();

    pressChord("m", { shift: true });

    expect(
      root.querySelector("[data-testid='settings-surface']"),
    ).not.toBeNull();
    // The section deep-link lands on the local-models slug (the model picker).
    expect(
      root
        .querySelector("[data-testid='settings-content']")
        ?.getAttribute("data-active-slug"),
    ).toBe("local-models");
  });

  it("starts/opens a run on ⌘N, leaving Settings", async () => {
    const root = await mountSignedInShell();

    // Open Settings first (via ⌘,) so ⌘N has to both leave Settings and land
    // on the Run cockpit — driven purely through the wired global chords.
    pressChord(",");
    expect(
      root.querySelector("[data-testid='settings-surface']"),
    ).not.toBeNull();

    pressChord("n");

    expect(root.querySelector("[data-testid='settings-surface']")).toBeNull();
    expect(
      root
        .querySelector("[data-testid='destination-outlet']")
        ?.getAttribute("data-destination"),
    ).toBe("run");
  });

  it("toggles the ⌘K palette exactly once per press (single ⌘K listener)", async () => {
    const root = await mountSignedInShell();
    const host = root.querySelector("[data-testid='desktop-palette-host']");
    expect(host?.getAttribute("data-palette-open")).toBe("false");

    // One press → exactly one toggle. A duplicate ⌘K listener would toggle
    // twice (net no-op), so the palette landing open proves single sourcing.
    pressChord("k");
    expect(host?.getAttribute("data-palette-open")).toBe("true");

    // A second press toggles it back closed — still one toggle per press.
    pressChord("k");
    expect(host?.getAttribute("data-palette-open")).toBe("false");
  });

  it("does not fire any bootstrap-level handler for the run-scoped ⌘M chord", async () => {
    const root = await mountSignedInShell();
    const host = root.querySelector("[data-testid='desktop-palette-host']");

    // ⌘M (no shift) is a run-scoped chord owned by the cockpit (useRunMode),
    // not wired at the bootstrap level. It must not open Settings, must not
    // touch the palette, and must not navigate.
    pressChord("m");

    expect(root.querySelector("[data-testid='settings-surface']")).toBeNull();
    expect(host?.getAttribute("data-palette-open")).toBe("false");
    expect(
      root
        .querySelector("[data-testid='destination-outlet']")
        ?.getAttribute("data-destination"),
    ).toBe("run");
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
