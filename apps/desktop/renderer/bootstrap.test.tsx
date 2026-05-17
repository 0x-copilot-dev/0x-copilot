// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mountApp } from "./bootstrap";

describe("renderer bootstrap", () => {
  let container: HTMLElement | null = null;
  let unmount: (() => void) | null = null;

  beforeEach(() => {
    // jsdom has no preload-injected window.bridge. Stub a noop so
    // IpcTransport's constructor (which installs a stream-event listener
    // at construction time) can wire up cleanly.
    (window as unknown as { bridge: unknown }).bridge = {
      ipc: {
        invoke: () => Promise.resolve(),
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

  it("mounts <ChatShell /> with the desktop placeholder visible", () => {
    container = document.createElement("div");
    container.id = "root";
    document.body.appendChild(container);

    act(() => {
      unmount = mountApp(container as HTMLElement);
    });

    const placeholder = container.querySelector(
      "[data-testid='desktop-placeholder']",
    );
    expect(placeholder).not.toBeNull();
    expect(placeholder?.textContent).toContain("Atlas desktop");
  });
});
