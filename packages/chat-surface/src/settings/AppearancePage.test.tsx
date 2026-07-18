import { ACCENT_SCHEMES } from "@0x-copilot/design-system";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  APPEARANCE_ACCENTS,
  AppearancePage,
  appearanceAttributes,
  splitAppearancePersistence,
  type AppearanceValue,
} from "./AppearancePage";

const VALUE: AppearanceValue = {
  theme: "dark",
  accent: "sky",
  density: "comfortable",
  reduceMotion: false,
};

function renderPage(
  overrides: Partial<ComponentProps<typeof AppearancePage>> = {},
) {
  const onChange = vi.fn();
  render(<AppearancePage value={VALUE} onChange={onChange} {...overrides} />);
  return { onChange };
}

describe("<AppearancePage>", () => {
  it("renders exactly 3 theme tiles (slate is not surfaced)", () => {
    renderPage();
    const tiles = within(screen.getByTestId("appearance-theme")).getAllByRole(
      "radio",
    );
    expect(tiles).toHaveLength(3);
    const labels = tiles.map((t) => t.getAttribute("data-value"));
    expect(labels).toEqual(["dark", "light", "system"]);
    expect(labels).not.toContain("slate");
  });

  it("renders only the reconciled 4-accent set — narrower than the 9-entry ACCENT_SCHEMES", () => {
    renderPage();
    const swatches = within(
      screen.getByTestId("appearance-accent"),
    ).getAllByRole("radio");
    expect(swatches).toHaveLength(4);
    expect(swatches.map((s) => s.getAttribute("data-value"))).toEqual([
      "sky",
      "jade",
      "ember",
      "violet",
    ]);
    // Single-accent discipline: strictly narrower than the shipped palette.
    expect(APPEARANCE_ACCENTS.length).toBeLessThan(ACCENT_SCHEMES.length);
  });

  it("renders the density options including Spacious", () => {
    renderPage();
    const density = within(
      screen.getByTestId("segmented-control"),
    ).getAllByRole("radio");
    expect(density.map((d) => d.getAttribute("data-value"))).toEqual([
      "comfortable",
      "compact",
      "spacious",
    ]);
  });

  it("renders a reduce-motion toggle reflecting the current value", () => {
    renderPage({ value: { ...VALUE, reduceMotion: true } });
    expect(screen.getByTestId("appearance-reduce-motion")).toBeChecked();
  });

  it("reflects the current selection via aria-checked and the page attributes", () => {
    render(
      <AppearancePage
        value={{
          theme: "light",
          accent: "violet",
          density: "compact",
          reduceMotion: false,
        }}
        onChange={() => undefined}
      />,
    );
    const page = screen.getByTestId("appearance-page");
    expect(page).toHaveAttribute("data-theme", "light");
    expect(page).toHaveAttribute("data-accent", "violet");
    expect(page).toHaveAttribute("data-density", "compact");
    expect(page).toHaveAttribute("data-reduce-motion", "auto");

    const activeSwatch = within(screen.getByTestId("appearance-accent"))
      .getAllByRole("radio")
      .find((s) => s.getAttribute("data-value") === "violet");
    expect(activeSwatch).toHaveAttribute("aria-checked", "true");
  });

  it("round-trips a legacy slate theme with no tile selected", () => {
    render(
      <AppearancePage
        value={{ ...VALUE, theme: "slate" }}
        onChange={() => undefined}
      />,
    );
    const checked = within(screen.getByTestId("appearance-theme"))
      .getAllByRole("radio")
      .filter((t) => t.getAttribute("aria-checked") === "true");
    expect(checked).toHaveLength(0);
    // …but the value survives on the page attribute mirror.
    expect(screen.getByTestId("appearance-page")).toHaveAttribute(
      "data-theme",
      "slate",
    );
  });

  it("reports theme / accent / density / reduce-motion edits through onChange", () => {
    const { onChange } = renderPage();

    fireEvent.click(
      within(screen.getByTestId("appearance-theme"))
        .getAllByRole("radio")
        .find((t) => t.getAttribute("data-value") === "light")!,
    );
    expect(onChange).toHaveBeenCalledWith({ theme: "light" });

    fireEvent.click(
      within(screen.getByTestId("appearance-accent"))
        .getAllByRole("radio")
        .find((s) => s.getAttribute("data-value") === "jade")!,
    );
    expect(onChange).toHaveBeenCalledWith({ accent: "jade" });

    fireEvent.click(
      within(screen.getByTestId("segmented-control"))
        .getAllByRole("radio")
        .find((d) => d.getAttribute("data-value") === "spacious")!,
    );
    expect(onChange).toHaveBeenCalledWith({ density: "spacious" });

    fireEvent.click(screen.getByTestId("appearance-reduce-motion"));
    expect(onChange).toHaveBeenCalledWith({ reduceMotion: true });
  });

  it("shows a loading skeleton, never a bare blank", () => {
    renderPage({ loading: true });
    expect(screen.getByTestId("appearance-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("appearance-theme")).not.toBeInTheDocument();
  });

  it("shows a role=alert error with a Retry affordance", () => {
    const onRetry = vi.fn();
    renderPage({ error: "Preferences unavailable", onRetry });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Preferences unavailable",
    );
    fireEvent.click(screen.getByTestId("appearance-retry"));
    expect(onRetry).toHaveBeenCalled();
  });
});

describe("appearanceAttributes", () => {
  it("maps the value to the design-system :root attribute contract", () => {
    expect(
      appearanceAttributes({
        theme: "system",
        accent: "jade",
        density: "spacious",
        reduceMotion: true,
      }),
    ).toEqual({
      "data-theme": "dark", // system resolves to dark by default
      "data-accent": "jade",
      "data-density": "spacious",
      "data-reduce-motion": "always",
    });
  });

  it("resolves system to light when the OS prefers light", () => {
    expect(
      appearanceAttributes(
        {
          theme: "system",
          accent: "sky",
          density: "comfortable",
          reduceMotion: false,
        },
        { systemPrefersDark: false },
      ),
    ).toMatchObject({ "data-theme": "light", "data-reduce-motion": "auto" });
  });

  it("passes a legacy theme through unchanged", () => {
    expect(
      appearanceAttributes({
        theme: "slate",
        accent: "sky",
        density: "comfortable",
        reduceMotion: false,
      }),
    ).toMatchObject({ "data-theme": "slate" });
  });
});

describe("splitAppearancePersistence (FR-5.9a — no option silently fails to persist)", () => {
  it("routes contract-valid fields to the profile store", () => {
    expect(splitAppearancePersistence({ theme: "system" }).profile).toEqual({
      theme: "system",
    });
    expect(splitAppearancePersistence({ accent: "sky" })).toEqual({
      profile: { accent: "sky" },
      local: {},
    });
    expect(splitAppearancePersistence({ density: "compact" })).toEqual({
      profile: { density: "compact" },
      local: {},
    });
    expect(splitAppearancePersistence({ reduceMotion: true }).profile).toEqual({
      reduce_motion: "always",
    });
    expect(splitAppearancePersistence({ reduceMotion: false }).profile).toEqual(
      { reduce_motion: "auto" },
    );
  });

  it("routes off-contract fields (jade/ember accent, spacious density) to the local fallback", () => {
    expect(splitAppearancePersistence({ accent: "jade" })).toEqual({
      profile: {},
      local: { accent: "jade" },
    });
    expect(splitAppearancePersistence({ accent: "ember" })).toEqual({
      profile: {},
      local: { accent: "ember" },
    });
    expect(splitAppearancePersistence({ density: "spacious" })).toEqual({
      profile: {},
      local: { density: "spacious" },
    });
  });
});
