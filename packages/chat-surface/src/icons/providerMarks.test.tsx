import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  PROVIDER_BRAND_COLOR,
  PROVIDER_BRAND_COLOR_FALLBACK,
  PROVIDER_MARK_IDS,
  ProviderMark,
  hasProviderMark,
  providerBrandColor,
  providerInitials,
} from "./providerMarks";

describe("PROVIDER_BRAND_COLOR", () => {
  it("carries the composer's dot hues verbatim (ModelPill KEY_PROVIDER_DOT)", () => {
    expect(PROVIDER_BRAND_COLOR.anthropic).toBe("#d97757");
    expect(PROVIDER_BRAND_COLOR.openai).toBe("#6aa88f");
    expect(PROVIDER_BRAND_COLOR.openrouter).toBe("#9a7fd6");
    expect(PROVIDER_BRAND_COLOR.google).toBe("#4285f4");
  });

  it("covers ollama with a theme-aware neutral (local software, no brand hue)", () => {
    expect(providerBrandColor("ollama")).toBe("var(--color-text-strong)");
  });

  it("falls back to a token for unknown providers, and is case-insensitive", () => {
    expect(providerBrandColor("acme-llm")).toBe(PROVIDER_BRAND_COLOR_FALLBACK);
    expect(PROVIDER_BRAND_COLOR_FALLBACK.startsWith("var(--")).toBe(true);
    expect(providerBrandColor("OpenAI")).toBe("#6aa88f");
  });
});

describe("providerInitials", () => {
  it("derives two-letter initials for the known providers", () => {
    expect(providerInitials("anthropic")).toBe("An");
    expect(providerInitials("openai")).toBe("Op");
    expect(providerInitials("google")).toBe("Go");
    expect(providerInitials("openrouter")).toBe("Or");
    expect(providerInitials("ollama")).toBe("Ol");
  });

  it("reads a display label, splitting words and ignoring short acronyms", () => {
    expect(providerInitials("Hugging Face")).toBe("Hf");
    expect(providerInitials("Mistral AI")).toBe("Mi");
    expect(providerInitials("together-ai")).toBe("To");
  });

  it("handles one-character input", () => {
    expect(providerInitials("q")).toBe("Q");
    expect(providerInitials("X")).toBe("X");
  });

  it("handles empty and punctuation-only input", () => {
    expect(providerInitials("")).toBe("");
    expect(providerInitials("   ")).toBe("");
    expect(providerInitials("--")).toBe("");
  });
});

describe("<ProviderMark>", () => {
  it.each(PROVIDER_MARK_IDS)("renders an inline <svg> for %s", (provider) => {
    const { container } = render(<ProviderMark provider={provider} />);
    const svg = container.querySelector("svg")!;
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute("viewBox", "0 0 24 24");
    // The mark draws real geometry, not an empty frame.
    expect(container.querySelectorAll("svg *").length).toBeGreaterThan(0);
    expect(container.querySelector("img")).toBeNull();
  });

  it("bundles the marks locally — no network reference anywhere", () => {
    const { container } = render(<ProviderMark provider="anthropic" />);
    expect(container.innerHTML).not.toMatch(/https?:|url\(|<img/i);
  });

  it("sizes both branches to the same box", () => {
    const known = render(<ProviderMark provider="google" size={13} />);
    expect(known.container.querySelector("svg")).toHaveAttribute("width", "13");
    const unknown = render(<ProviderMark provider="acme-llm" size={13} />);
    expect(unknown.container.querySelector("span")).toHaveStyle({
      width: "13px",
      height: "13px",
    });
  });

  it("renders two-letter initials for an unknown provider", () => {
    const { container } = render(<ProviderMark provider="openrouter" />);
    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).toBe("Or");
  });

  it("derives the initials from an explicit label when given", () => {
    const { container } = render(
      <ProviderMark provider="together" label="Together AI" />,
    );
    expect(container.textContent).toBe("To");
  });

  it("renders a last-resort glyph when nothing can be derived", () => {
    const { container } = render(<ProviderMark provider="" />);
    expect(container.textContent).toBe("?");
  });

  it('is monochrome by default and tinted on tone="brand"', () => {
    const mono = render(<ProviderMark provider="anthropic" />);
    expect(mono.container.querySelector("svg")).not.toHaveStyle({
      color: "#d97757",
    });
    const brand = render(<ProviderMark provider="anthropic" tone="brand" />);
    expect(brand.container.querySelector("svg")).toHaveStyle({
      color: "#d97757",
    });
  });

  it("is decorative by default and an image when titled", () => {
    const plain = render(<ProviderMark provider="google" />);
    expect(plain.container.querySelector("svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    const titled = render(<ProviderMark provider="google" title="Google" />);
    const svg = titled.container.querySelector("svg")!;
    expect(svg).toHaveAttribute("role", "img");
    expect(svg).toHaveAttribute("aria-label", "Google");
  });

  it("hasProviderMark reports which providers are bundled", () => {
    expect(hasProviderMark("openai")).toBe(true);
    expect(hasProviderMark("OpenAI")).toBe(true);
    expect(hasProviderMark("openrouter")).toBe(false);
    expect(hasProviderMark("acme-llm")).toBe(false);
  });
});
