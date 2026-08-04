import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { APP_RAIL_WIDTH } from "@0x-copilot/chat-surface";
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
    // traffic lights. Settings suppresses the topbar, so its nav column takes
    // the inset instead — vertically, because a left inset on a 216px column
    // indents every row it heads. Without this the "Settings" title renders
    // beneath the window controls.
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

  it("subtracts the rail width from the headers' traffic-light inset", () => {
    // Both headers start to the RIGHT of the rail, so an inset measured from the
    // window's left edge double-counts it. It shipped as `clearance + 30px`,
    // which put the Threads toggle at x=160 and the goal title at x=184 — a full
    // rail-width-plus into the main column, floating over the Threads panel
    // instead of sitting beside the window controls.
    const desktopCss = readFileSync(
      resolve(process.cwd(), "renderer/desktop.css"),
      "utf8",
    );

    const inset = /\[data-testid="run-header"\]\s*\{([^}]*)\}/.exec(desktopCss);
    expect(inset).not.toBeNull();
    const declaration = inset?.[1] ?? "";
    expect(declaration).toContain("- var(--desktop-app-rail-width)");
    expect(declaration).not.toMatch(/--desktop-traffic-light-clearance\)\s*\+/);
  });

  it("keeps the windowed brand mark inside the rail column", () => {
    // The mark used to be `translateX(70px)` out of the 48px rail and into the
    // header's leading area at z-index 2. That is what forced every header to
    // clear a RAIL button rather than the window controls. The band is reserved
    // vertically instead, so the mark drops below the controls in its own
    // column and the title bar's left edge stays free for the header.
    const desktopCss = readFileSync(
      resolve(process.cwd(), "renderer/desktop.css"),
      "utf8",
    );

    // Comments stripped: the rule this guards against is described at length in
    // the stylesheet's own prose, and a bare text match would fail on the
    // explanation rather than on a reinstated declaration.
    const declarations = desktopCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(declarations).not.toMatch(/\[data-rail-brand\]/);
    expect(declarations).not.toMatch(/translateX/);
    expect(desktopCss).toMatch(
      /\[data-native-traffic-lights="true"\]\[data-full-screen="false"\]\s*\[data-component="app-rail"\]\s*\{[^}]*padding-top:[^}]*--desktop-titlebar-height[^}]*!important/,
    );
  });

  it("keeps --desktop-app-rail-width equal to the shell's APP_RAIL_WIDTH", () => {
    // The CSS cannot import the constant, so the coupling is asserted instead:
    // if the rail is ever resized in chat-surface, this fails rather than
    // silently re-introducing the offset it exists to cancel.
    const desktopCss = readFileSync(
      resolve(process.cwd(), "renderer/desktop.css"),
      "utf8",
    );

    const declared = /--desktop-app-rail-width:\s*(\d+)px/.exec(desktopCss);
    expect(declared).not.toBeNull();
    expect(Number(declared?.[1])).toBe(APP_RAIL_WIDTH);
  });
});
