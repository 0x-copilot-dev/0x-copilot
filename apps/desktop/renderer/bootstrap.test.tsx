// @vitest-environment jsdom
import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { mountApp } from "./bootstrap";

describe("renderer bootstrap", () => {
  let container: HTMLElement | null = null;
  let unmount: (() => void) | null = null;

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
  });

  it("mounts <ChatShell /> with the desktop placeholder visible", () => {
    container = document.createElement("div");
    container.id = "root";
    document.body.appendChild(container);

    // React 19's createRoot.render commits asynchronously; act() flushes
    // the render before we query the DOM.
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
