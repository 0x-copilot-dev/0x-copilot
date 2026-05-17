// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mountApp } from "./bootstrap";

describe("renderer bootstrap", () => {
  let container: HTMLElement | null = null;
  let unmount: (() => void) | null = null;

  beforeEach(() => {
    (window as unknown as { bridge: unknown }).bridge = {
      ipc: {
        invoke: () => Promise.resolve(null),
        on: () => () => undefined,
      },
    };
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

  it("mounts the sign-in gate while no session is present", async () => {
    container = document.createElement("div");
    container.id = "root";
    document.body.appendChild(container);

    await act(async () => {
      unmount = mountApp(container as HTMLElement);
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
    (window as unknown as { bridge: unknown }).bridge = {
      ipc: {
        invoke: (channel: string) => {
          if (channel === "auth.get-session") {
            return Promise.resolve({
              workspaceId: "org_acme",
              expiresAt: Date.now() + 60_000,
              displayName: "Sarah",
              email: "sarah@acme.test",
            });
          }
          return Promise.resolve(null);
        },
        on: () => () => undefined,
      },
    };

    container = document.createElement("div");
    container.id = "root";
    document.body.appendChild(container);

    await act(async () => {
      unmount = mountApp(container as HTMLElement);
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
});
