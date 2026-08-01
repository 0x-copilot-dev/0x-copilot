import { readFileSync } from "node:fs";
import { resolve } from "node:path";

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

  it("owns the only rounded frame when RunDestination is nested in ChatShell", () => {
    const desktopCss = readFileSync(
      resolve(process.cwd(), "renderer/desktop.css"),
      "utf8",
    );

    expect(desktopCss).toMatch(
      /\.desktop-window-frame\s+\[data-testid="destination-outlet"\]\s*>\s*\.run-destination\s*\{/,
    );
    expect(desktopCss).not.toMatch(
      /\.desktop-window-frame\s*>\s*\[data-testid="destination-outlet"\]\s*>\s*\.run-destination/,
    );
  });

  it("clears the native title-bar band above the topbar-less Settings nav", () => {
    // Every other destination gets a topbar, which is padded left to clear the
    // traffic lights and the translated turbine. Settings suppresses the topbar,
    // so its nav column takes the inset instead — vertically, because the column
    // is too narrow for a 100px left inset. Without this the "Settings" title
    // renders beneath both the window controls and the z-index:2 brand mark.
    const desktopCss = readFileSync(
      resolve(process.cwd(), "renderer/desktop.css"),
      "utf8",
    );

    expect(desktopCss).toMatch(
      /\[data-native-traffic-lights="true"\]\[data-full-screen="false"\]\s*\[data-settings-nav\]\s*\{[^}]*padding-top:[^}]*--desktop-titlebar-height[^}]*!important/,
    );
  });

  it("insets RunHeader for the traffic lights on the same rule as the topbar", () => {
    // RunHeader carried an exemption from this inset, justified by a centred
    // product identity. PRD-02 made the goal the title — left-aligned, with a
    // `leading` slot — and the exemption outlived the layout it described, so
    // the turbine painted over the leading control and the goal's first
    // characters. Asserting the two selectors share ONE declaration is the part
    // that matters: separate rules are how they drifted apart before.
    const desktopCss = readFileSync(
      resolve(process.cwd(), "renderer/desktop.css"),
      "utf8",
    );

    expect(desktopCss).toMatch(
      /\[data-native-traffic-lights="true"\]\[data-full-screen="false"\]\s*\[data-testid="run-header"\]\s*\{[^}]*padding-left:[^}]*--desktop-traffic-light-clearance[^}]*!important/,
    );
    expect(desktopCss).toMatch(
      /\[data-component="topbar"\],\s*\.desktop-window-frame\[data-native-traffic-lights="true"\]\[data-full-screen="false"\]\s*\[data-testid="run-header"\]\s*\{\s*padding-left:/,
    );
  });
});
