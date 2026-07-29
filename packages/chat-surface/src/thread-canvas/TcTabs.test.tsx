import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TcTabs, type TcTab } from "./TcTabs";

const baseTabs: readonly TcTab[] = [
  { uri: "email://draft-1", title: "Renewal email" },
  { uri: "sf-opp://acme/op-1", title: "Acme — Closed Won", pinned: true },
  { uri: "sheet-row://q/2", title: "Pricing row" },
];

describe("TcTabs", () => {
  it("renders one tab per entry with the title text", () => {
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByText("Renewal email")).toBeInTheDocument();
    expect(screen.getByText("Acme — Closed Won")).toBeInTheDocument();
    expect(screen.getByText("Pricing row")).toBeInTheDocument();
  });

  it("marks the active tab with aria-current and aria-selected", () => {
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="sheet-row://q/2"
        onActivate={() => {}}
        onClose={() => {}}
      />,
    );
    const active = screen.getByText("Pricing row").closest('[role="tab"]');
    expect(active).not.toBeNull();
    expect(active).toHaveAttribute("aria-current", "page");
    expect(active).toHaveAttribute("aria-selected", "true");

    const inactive = screen.getByText("Renewal email").closest('[role="tab"]');
    expect(inactive).toHaveAttribute("aria-selected", "false");
    expect(inactive).not.toHaveAttribute("aria-current");
  });

  it("calls onActivate with the tab uri on click", () => {
    const onActivate = vi.fn();
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={onActivate}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByText("Pricing row"));
    expect(onActivate).toHaveBeenCalledWith("sheet-row://q/2");
  });

  it("activates a tab on keyboard Enter/Space", () => {
    const onActivate = vi.fn();
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={onActivate}
        onClose={() => {}}
      />,
    );
    const target = screen.getByText("Pricing row").closest('[role="tab"]');
    if (!target) throw new Error("tab not found");
    fireEvent.keyDown(target, { key: "Enter" });
    fireEvent.keyDown(target, { key: " " });
    expect(onActivate).toHaveBeenCalledTimes(2);
    expect(onActivate).toHaveBeenLastCalledWith("sheet-row://q/2");
  });

  it("renders a close button only on non-pinned tabs", () => {
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={() => {}}
        onClose={() => {}}
      />,
    );
    expect(
      screen.getByTestId("tc-tabs-close-email://draft-1"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("tc-tabs-close-sheet-row://q/2"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-tabs-close-sf-opp://acme/op-1"),
    ).not.toBeInTheDocument();
  });

  it("calls onClose without activating when the close button is clicked", () => {
    const onActivate = vi.fn();
    const onClose = vi.fn();
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={onActivate}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-tabs-close-sheet-row://q/2"));
    expect(onClose).toHaveBeenCalledWith("sheet-row://q/2");
    expect(onActivate).not.toHaveBeenCalled();
  });

  // Layout (row direction, horizontal overflow) moved to `surface-language.css`
  // when the strip stopped carrying a private hardcoded palette. jsdom loads no
  // stylesheets, so asserting a computed value here would assert nothing; the
  // honest unit-level claim is that the element opts into the class that owns
  // those rules. The rendered result is covered by the design-parity harness,
  // which reads real computed styles in a browser.
  it("carries the surface-language strip class", () => {
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("tc-tabs")).toHaveClass("tc-tabs");
  });

  describe("source hue", () => {
    it("derives a tab's hue from its URI scheme", () => {
      render(
        <TcTabs
          tabs={[
            { uri: "table://safe/batch", title: "Batch" },
            { uri: "artifact-dataset://art_1@1", title: "forecast" },
            { uri: "board://linear/cycle/14", title: "Cycle 14" },
          ]}
          activeUri="table://safe/batch"
          onActivate={() => {}}
          onClose={() => {}}
        />,
      );
      const hueOf = (title: string): string | null =>
        screen
          .getByText(title)
          .closest("[role='tab']")!
          .getAttribute("data-surface-hue");
      expect(hueOf("Batch")).toBe("jade");
      expect(hueOf("forecast")).toBe("sky");
      expect(hueOf("Cycle 14")).toBe("plum");
    });

    it("shows no identity for a surface whose scheme resolves to none", () => {
      render(
        <TcTabs
          tabs={[{ uri: "incident://pagerduty/4127", title: "Incident 4127" }]}
          activeUri="incident://pagerduty/4127"
          onActivate={() => {}}
          onClose={() => {}}
        />,
      );
      expect(screen.getByRole("tab").getAttribute("data-surface-hue")).toBe(
        "none",
      );
    });

    // The seam a `publish_artifact` accent arrives through.
    it("lets an explicit hue override the scheme's default", () => {
      render(
        <TcTabs
          tabs={[
            {
              uri: "artifact-dataset://art_1@1",
              title: "forecast",
              hue: "ember",
            },
          ]}
          activeUri="artifact-dataset://art_1@1"
          onActivate={() => {}}
          onClose={() => {}}
        />,
      );
      expect(screen.getByRole("tab").getAttribute("data-surface-hue")).toBe(
        "ember",
      );
    });

    // A malformed choice must not strip the artifact of the identity its kind
    // already implies — it falls back, it does not blank out.
    it("ignores an unrecognised hue and keeps the scheme default", () => {
      render(
        <TcTabs
          tabs={[
            {
              uri: "artifact-dataset://art_1@1",
              title: "forecast",
              hue: "#ff00ff",
            },
          ]}
          activeUri="artifact-dataset://art_1@1"
          onActivate={() => {}}
          onClose={() => {}}
        />,
      );
      expect(screen.getByRole("tab").getAttribute("data-surface-hue")).toBe(
        "sky",
      );
    });
  });
});
