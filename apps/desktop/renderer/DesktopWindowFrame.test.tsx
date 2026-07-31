import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WindowChromeState } from "../main/window-channels";
import type { WindowBridge } from "../preload/window-bridge-types";
import { DesktopWindowFrame } from "./DesktopWindowFrame";

afterEach(() => {
  Reflect.deleteProperty(window, "bridge");
});

describe("DesktopWindowFrame", () => {
  it("projects native traffic-light and fullscreen state into stable CSS hooks", () => {
    const handlers = new Set<(state: WindowChromeState) => void>();
    const bridge: WindowBridge = {
      ipc: {
        invoke: vi.fn(),
        on: vi.fn(() => () => {}),
      },
      windowChrome: {
        subscribe(handler) {
          handlers.add(handler);
          handler({
            isFullScreen: false,
            hasNativeTrafficLights: true,
          });
          return () => {
            handlers.delete(handler);
          };
        },
      },
    };
    Object.defineProperty(window, "bridge", {
      configurable: true,
      value: bridge,
    });

    const mounted = render(
      <DesktopWindowFrame>
        <div>content</div>
      </DesktopWindowFrame>,
    );
    const frame = screen.getByTestId("desktop-window-frame");
    expect(frame).toHaveAttribute("data-native-traffic-lights", "true");
    expect(frame).toHaveAttribute("data-full-screen", "false");

    act(() => {
      for (const handler of handlers) {
        handler({
          isFullScreen: true,
          hasNativeTrafficLights: true,
        });
      }
    });
    expect(frame).toHaveAttribute("data-full-screen", "true");

    mounted.unmount();
    expect(handlers).toHaveLength(0);
  });
});
