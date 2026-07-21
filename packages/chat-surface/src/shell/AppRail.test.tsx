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

  describe("PRD-C parity — icons, tokens, badge, identity", () => {
    const solo = destinationsForProfile("single_user_desktop");

    function button(slug: ShellDestinationSlug): HTMLElement {
      return destinationButtons().find(
        (b) => b.getAttribute("data-destination") === slug,
      )!;
    }

    it("renders the design glyphs for the drifted solo destinations", () => {
      render(
        <AppRail
          activeDestination="run"
          destinations={solo}
          onNavigate={() => {}}
        />,
      );
      // projects → rounded folder (not the old square folder path).
      expect(button("projects").querySelector("path")).toHaveAttribute(
        "d",
        "M3 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
      );
      // connectors ("Tools") → power plug (not the node-graph).
      expect(button("connectors").querySelector("path")).toHaveAttribute(
        "d",
        "M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-12 0z M12 18v3",
      );
      // tools ("Skills") → sparkle (not the wrench).
      expect(button("tools").querySelector("path")).toHaveAttribute(
        "d",
        "M12 3l2.1 5.3L20 10l-5.9 1.7L12 17l-2.1-5.3L4 10l5.9-1.7z",
      );
      // icons render at the design stroke 1.7 / size 17.
      const svg = button("projects").querySelector("svg")!;
      expect(svg).toHaveAttribute("stroke-width", "1.7");
      expect(svg).toHaveAttribute("width", "17");
    });

    it("puts the rail on the elevated bg and active items on surface-muted", () => {
      render(
        <AppRail
          activeDestination="projects"
          destinations={solo}
          onNavigate={() => {}}
        />,
      );
      const nav = screen.getByRole("navigation", {
        name: /copilot destinations/i,
      });
      expect(nav.style.backgroundColor).toBe("var(--color-bg-elevated)");
      expect(button("projects").style.background).toBe(
        "var(--color-surface-muted)",
      );
      // inactive item is transparent.
      expect(button("chats").style.background).toBe("transparent");
    });

    it("shows a Run badge only when the count > 0 and Run is not active", () => {
      const { rerender } = render(
        <AppRail
          activeDestination="chats"
          destinations={solo}
          onNavigate={() => {}}
          badges={{ run: 2 }}
        />,
      );
      expect(
        button("run").querySelector("[data-rail-badge]"),
      ).toHaveTextContent("2");
      // active Run hides the badge (design: shown only when off-workspace).
      rerender(
        <AppRail
          activeDestination="run"
          destinations={solo}
          onNavigate={() => {}}
          badges={{ run: 2 }}
        />,
      );
      expect(
        button("run").querySelector("[data-rail-badge]"),
      ).not.toBeInTheDocument();
      // zero → no badge.
      rerender(
        <AppRail
          activeDestination="chats"
          destinations={solo}
          onNavigate={() => {}}
          badges={{ run: 0 }}
        />,
      );
      expect(
        button("run").querySelector("[data-rail-badge]"),
      ).not.toBeInTheDocument();
    });

    it("renders the user's initial in the avatar when identity is supplied", () => {
      render(
        <AppRail
          activeDestination="run"
          destinations={solo}
          onNavigate={() => {}}
          onOpenSettings={() => {}}
          identity={{ initial: "sasha" }}
        />,
      );
      const avatar = screen.getByRole("button", { name: "Account" });
      expect(avatar).toHaveTextContent("S");
      expect(avatar.style.background).toBe("var(--color-surface-elevated)");
    });

    it("falls back to a neutral user glyph without identity", () => {
      render(
        <AppRail
          activeDestination="run"
          destinations={solo}
          onNavigate={() => {}}
          onOpenSettings={() => {}}
        />,
      );
      const avatar = screen.getByRole("button", { name: "Account" });
      expect(avatar.querySelector("svg")).toBeInTheDocument();
      expect(avatar).toHaveTextContent("");
    });
  });
});
