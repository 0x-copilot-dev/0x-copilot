import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppRail } from "./AppRail";
import {
  destinationsForProfile,
  SHELL_DESTINATIONS,
  type ShellDestinationSlug,
} from "./destinations";

/** Destination buttons only — excludes the brand mark + foot (settings/avatar). */
function destinationButtons(): HTMLElement[] {
  const nav = screen.getByRole("navigation", { name: /copilot destinations/i });
  return Array.from(
    nav.querySelectorAll<HTMLElement>("button[data-destination]"),
  );
}

describe("AppRail", () => {
  describe("legacy default (no destinations prop — web path)", () => {
    it("renders the 12 legacy destination buttons in order (incl. Routines)", () => {
      render(<AppRail activeDestination="home" onNavigate={() => {}} />);
      const buttons = destinationButtons();
      expect(buttons).toHaveLength(12);
      const slugs = buttons.map((b) => b.getAttribute("data-destination"));
      expect(slugs).toEqual(SHELL_DESTINATIONS.map((d) => d.slug));
      // Routines is the 12th (last) destination.
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
      // Regression: a prior implementation made every non-chats button a
      // navigation no-op. Each slug must fire.
      for (const d of SHELL_DESTINATIONS) {
        fireEvent.click(screen.getByRole("button", { name: d.label }));
        expect(onNavigate).toHaveBeenLastCalledWith(d.slug);
      }
      expect(onNavigate).toHaveBeenCalledTimes(SHELL_DESTINATIONS.length);
    });
  });

  describe("profile-derived list (destinations prop)", () => {
    it("renders exactly the 6 solo destinations with the relabelled Tools/Skills", () => {
      render(
        <AppRail
          activeDestination="run"
          onNavigate={() => {}}
          destinations={destinationsForProfile("single_user_desktop")}
        />,
      );
      const buttons = destinationButtons();
      expect(buttons.map((b) => b.getAttribute("data-destination"))).toEqual([
        "run",
        "chats",
        "projects",
        "activity",
        "connectors",
        "tools",
      ]);
      // Slug identity is preserved; only the labels are relabelled per profile.
      expect(buttons.map((b) => b.getAttribute("aria-label"))).toEqual([
        "Run",
        "Chats",
        "Projects",
        "Activity",
        "Tools", // slug `connectors`
        "Skills", // slug `tools`
      ]);
    });

    it("renders a glyph for the new run/activity slugs without an exhaustiveness crash", () => {
      render(
        <AppRail
          activeDestination="run"
          onNavigate={() => {}}
          destinations={destinationsForProfile("single_user_desktop")}
        />,
      );
      for (const label of ["Run", "Activity"]) {
        const btn = screen.getByRole("button", { name: label });
        expect(btn.querySelector("svg")).not.toBeNull();
      }
    });

    it("renders the 9 team destinations when given the team list", () => {
      render(
        <AppRail
          activeDestination="run"
          onNavigate={() => {}}
          destinations={destinationsForProfile("team")}
        />,
      );
      const slugs = destinationButtons().map((b) =>
        b.getAttribute("data-destination"),
      );
      expect(slugs).toHaveLength(9);
      expect(slugs.slice(-3)).toEqual(["team", "members", "billing"]);
    });
  });

  describe("active state", () => {
    it("marks the active destination with aria-current=page + a left-bar marker", () => {
      render(<AppRail activeDestination="chats" onNavigate={() => {}} />);
      const chats = screen.getByRole("button", { name: "Chats" });
      expect(chats).toHaveAttribute("aria-current", "page");
      expect(chats).toHaveAttribute("data-state", "active");
      // The 2px accent left bar renders only inside the active button.
      expect(chats.querySelector("[data-rail-active-bar]")).not.toBeNull();

      const home = screen.getByRole("button", { name: "Home" });
      expect(home).not.toHaveAttribute("aria-current");
      expect(home.querySelector("[data-rail-active-bar]")).toBeNull();
    });

    it("changing the activeDestination prop moves the active highlight + bar", () => {
      const { rerender } = render(
        <AppRail activeDestination="chats" onNavigate={() => {}} />,
      );
      expect(screen.getByRole("button", { name: "Chats" })).toHaveAttribute(
        "aria-current",
        "page",
      );
      rerender(
        <AppRail activeDestination="connectors" onNavigate={() => {}} />,
      );
      const connectors = screen.getByRole("button", { name: "Connectors" });
      expect(connectors).toHaveAttribute("aria-current", "page");
      expect(connectors.querySelector("[data-rail-active-bar]")).not.toBeNull();
      expect(screen.getByRole("button", { name: "Chats" })).not.toHaveAttribute(
        "aria-current",
      );
    });
  });

  describe("v2 geometry", () => {
    it("uses a 48px rail and 34px destination buttons", () => {
      render(<AppRail activeDestination="home" onNavigate={() => {}} />);
      const nav = screen.getByRole("navigation", {
        name: /copilot destinations/i,
      });
      expect(nav.style.width).toBe("48px");
      const first = destinationButtons()[0];
      expect(first.style.width).toBe("34px");
      expect(first.style.height).toBe("34px");
    });

    it("renders a 32px brand mark at the top that navigates to Run", () => {
      const onNavigate = vi.fn<(slug: ShellDestinationSlug) => void>();
      render(<AppRail activeDestination="home" onNavigate={onNavigate} />);
      const nav = screen.getByRole("navigation", {
        name: /copilot destinations/i,
      });
      const brand = nav.querySelector<HTMLElement>("[data-rail-brand]");
      expect(brand).not.toBeNull();
      expect(brand?.style.width).toBe("32px");
      fireEvent.click(brand as HTMLElement);
      expect(onNavigate).toHaveBeenCalledWith("run");
    });
  });

  describe("foot (settings + avatar)", () => {
    it("renders the Settings gear + a 26px avatar only when onOpenSettings is supplied", () => {
      const onOpenSettings = vi.fn();
      const { rerender } = render(
        <AppRail
          activeDestination="run"
          onNavigate={() => {}}
          onOpenSettings={onOpenSettings}
        />,
      );
      const settings = screen.getByRole("button", { name: "Settings" });
      const avatar = screen.getByRole("button", { name: "Account" });
      expect(settings).toHaveAttribute("data-rail-action", "settings");
      expect(avatar).toHaveAttribute("data-rail-me");
      expect(avatar.style.width).toBe("26px");

      fireEvent.click(settings);
      fireEvent.click(avatar);
      expect(onOpenSettings).toHaveBeenCalledTimes(2);

      // Absent handler → the whole foot (settings + avatar) is omitted.
      rerender(<AppRail activeDestination="run" onNavigate={() => {}} />);
      expect(
        screen.queryByRole("button", { name: "Settings" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Account" }),
      ).not.toBeInTheDocument();
    });
  });
});
