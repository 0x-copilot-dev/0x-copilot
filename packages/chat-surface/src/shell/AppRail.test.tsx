import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppRail } from "./AppRail";
import { SHELL_DESTINATIONS, type ShellDestinationSlug } from "./destinations";

describe("AppRail", () => {
  it("renders 12 destination buttons in order (incl. Routines)", () => {
    render(<AppRail activeDestination="home" onNavigate={() => {}} />);
    const nav = screen.getByRole("navigation", {
      name: /copilot destinations/i,
    });
    const buttons = within(nav).getAllByRole("button");
    expect(buttons).toHaveLength(12);
    const slugs = buttons.map((b) => b.getAttribute("data-destination"));
    expect(slugs).toEqual(SHELL_DESTINATIONS.map((d) => d.slug));
    // P5-B1: Routines is the 12th destination.
    expect(slugs[slugs.length - 1]).toBe("routines");
  });

  it("clicking a destination button calls onNavigate with that slug", () => {
    const onNavigate = vi.fn<(slug: ShellDestinationSlug) => void>();
    render(<AppRail activeDestination="home" onNavigate={onNavigate} />);
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    expect(onNavigate).toHaveBeenCalledWith("chats");

    fireEvent.click(screen.getByRole("button", { name: "Inbox" }));
    expect(onNavigate).toHaveBeenCalledWith("inbox");

    fireEvent.click(screen.getByRole("button", { name: "Home" }));
    expect(onNavigate).toHaveBeenCalledWith("home");

    expect(onNavigate).toHaveBeenCalledTimes(3);
  });

  it("every destination is independently navigable (not just chats)", () => {
    const onNavigate = vi.fn<(slug: ShellDestinationSlug) => void>();
    render(<AppRail activeDestination="home" onNavigate={onNavigate} />);
    // Regression: prior implementation made every non-chats button a
    // navigation no-op. Each slug must fire.
    for (const d of SHELL_DESTINATIONS) {
      fireEvent.click(screen.getByRole("button", { name: d.label }));
      expect(onNavigate).toHaveBeenLastCalledWith(d.slug);
    }
    expect(onNavigate).toHaveBeenCalledTimes(SHELL_DESTINATIONS.length);
  });

  it("marks the active destination with aria-current=page", () => {
    render(<AppRail activeDestination="chats" onNavigate={() => {}} />);
    const chats = screen.getByRole("button", { name: "Chats" });
    expect(chats).toHaveAttribute("aria-current", "page");
    expect(chats).toHaveAttribute("data-state", "active");
    const home = screen.getByRole("button", { name: "Home" });
    expect(home).not.toHaveAttribute("aria-current");
  });

  it("changing the activeDestination prop moves the active highlight", () => {
    const { rerender } = render(
      <AppRail activeDestination="chats" onNavigate={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "Chats" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    rerender(<AppRail activeDestination="connectors" onNavigate={() => {}} />);
    expect(screen.getByRole("button", { name: "Connectors" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: "Chats" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});
