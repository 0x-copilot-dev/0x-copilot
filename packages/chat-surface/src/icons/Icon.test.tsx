import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Icon } from "./Icon";
import { ICON_NAMES, ICON_PATHS, hasIcon, type IconName } from "./paths";
import type { SettingsNavIcon } from "../settings/settingsNav";

// Compile-time guarantee (FR-A.5): every settings-nav icon token is a valid
// IconName. If a token is added to SettingsNavIcon without a glyph, this line
// fails to type-check.
type _SettingsIconsAreRenderable = SettingsNavIcon extends IconName
  ? true
  : never;
const _assertSettingsSubset: _SettingsIconsAreRenderable = true;
void _assertSettingsSubset;

describe("<Icon>", () => {
  it("renders the canonical frame (viewBox, currentColor, stroke 1.7)", () => {
    const { container } = render(<Icon name="plug" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("viewBox", "0 0 24 24");
    expect(svg).toHaveAttribute("fill", "none");
    expect(svg).toHaveAttribute("stroke", "currentColor");
    expect(svg).toHaveAttribute("stroke-width", "1.7");
    expect(svg).toHaveAttribute("stroke-linecap", "round");
  });

  it("renders the plug glyph geometry (the Tools destination icon)", () => {
    const { container } = render(<Icon name="plug" />);
    const path = container.querySelector("path")!;
    expect(path).toHaveAttribute(
      "d",
      "M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-12 0z M12 18v3",
    );
  });

  it("sizes width and height from `size`", () => {
    const { container } = render(<Icon name="folder" size={17} />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("width", "17");
    expect(svg).toHaveAttribute("height", "17");
  });

  it("defaults to size 16 and stroke 1.7", () => {
    const { container } = render(<Icon name="gear" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("width", "16");
    expect(svg).toHaveAttribute("stroke-width", "1.7");
  });

  it("is decorative by default (aria-hidden, no role)", () => {
    const { container } = render(<Icon name="chats" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).not.toHaveAttribute("role");
  });

  it("becomes an accessible image when `title` is set", () => {
    const { container } = render(<Icon name="chats" title="Chats" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("role", "img");
    expect(svg).toHaveAttribute("aria-label", "Chats");
    expect(svg).not.toHaveAttribute("aria-hidden");
  });

  it("allows overriding stroke width (e.g. denser rows)", () => {
    const { container } = render(<Icon name="activity" strokeWidth={1.5} />);
    expect(container.querySelector("svg")).toHaveAttribute(
      "stroke-width",
      "1.5",
    );
  });

  it.each(ICON_NAMES)(
    "every IconName %s resolves to a non-empty glyph",
    (name) => {
      expect(ICON_PATHS[name]).toBeTruthy();
      const { container } = render(<Icon name={name} />);
      // Each glyph draws at least one shape element inside the frame.
      expect(container.querySelectorAll("svg *").length).toBeGreaterThan(0);
    },
  );

  it("hasIcon guards unknown names", () => {
    expect(hasIcon("plug")).toBe(true);
    expect(hasIcon("not-a-real-icon")).toBe(false);
  });
});
