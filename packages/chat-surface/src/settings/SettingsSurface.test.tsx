import { fireEvent, render, screen, within } from "@testing-library/react";
import { useEffect, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { DeploymentProfileProvider } from "../providers/DeploymentProfileProvider";
import {
  SETTINGS_CONTENT_MAX_WIDTH,
  SETTINGS_NAV_WIDTH,
  SettingsSurface,
  type SettingsSurfaceController,
  type SettingsSurfaceProps,
} from "./SettingsSurface";

function renderSurface(
  props: SettingsSurfaceProps = {},
  profile: "single_user_desktop" | "team" = "single_user_desktop",
): ReturnType<typeof render> {
  return render(
    <DeploymentProfileProvider profile={profile}>
      <SettingsSurface {...props} />
    </DeploymentProfileProvider>,
  );
}

describe("SettingsSurface — PRD-E nav header + icons", () => {
  it("renders the nav header with a profile-derived hint", () => {
    renderSurface();
    const tablist = screen.getByRole("tablist", { name: "Settings sections" });
    expect(within(tablist).getByText("Settings")).toBeInTheDocument();
    expect(within(tablist).getByText("Solo desktop")).toBeInTheDocument();
  });

  it("shows a team hint under the team profile", () => {
    renderSurface({}, "team");
    const tablist = screen.getByRole("tablist", { name: "Settings sections" });
    expect(within(tablist).getByText("Team workspace")).toBeInTheDocument();
  });

  it("renders a nav icon per item when renderNavIcon is supplied", () => {
    renderSurface({
      renderNavIcon: (icon) => (
        <span data-testid="nav-icon" data-icon={icon}>
          i
        </span>
      ),
    });
    // Profile items (Profile / Appearance / Shortcuts / Provider keys / …) each
    // get an icon; without renderNavIcon (the historic desktop mount) there were
    // none.
    expect(screen.getAllByTestId("nav-icon").length).toBeGreaterThan(3);
  });
});

describe("SettingsSurface — shell & layout (FR-5.1 / FR-5.2 / FR-5.6)", () => {
  it("renders a labelled full-height Settings region (topbar-suppressed surface)", () => {
    renderSurface();
    const region = screen.getByRole("region", { name: "Settings" });
    expect(region).toHaveAttribute("data-surface", "settings");
    expect((region as HTMLElement).style.height).toBe("100%");
  });

  it("renders the nav as a vertical tablist and content as a tabpanel", () => {
    renderSurface();
    const tablist = screen.getByRole("tablist", { name: "Settings sections" });
    expect(tablist).toHaveAttribute("aria-orientation", "vertical");
    expect((tablist as HTMLElement).style.width).toBe(
      `${SETTINGS_NAV_WIDTH}px`,
    );

    const panel = screen.getByRole("tabpanel");
    expect((panel as HTMLElement).style.maxWidth).toBe(
      `${SETTINGS_CONTENT_MAX_WIDTH}px`,
    );
  });

  it("renders the solo nav groups with their section tabs", () => {
    renderSurface();
    expect(screen.getByText("Account")).toBeInTheDocument();
    expect(screen.getByText("Models & keys")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Profile/ })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /Provider keys/ }),
    ).toBeInTheDocument();
    // BYOK mono tag comes through.
    expect(screen.getByText("BYOK")).toBeInTheDocument();
  });
});

describe("SettingsSurface — profile gate (FR-5.3 / FR-5.4)", () => {
  it("shows the solo footer and hides team sections on single_user_desktop", () => {
    renderSurface();
    expect(screen.getByTestId("settings-solo-footer")).toHaveTextContent(
      "Solo desktop mode. Workspace, members & billing appear only when 0xCopilot is deployed for a team.",
    );
    expect(screen.queryByRole("tab", { name: /Members/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Billing/ })).toBeNull();
    expect(screen.queryByText("Workspace")).toBeNull();
  });

  it("shows team sections and hides the footer on team", () => {
    renderSurface({}, "team");
    expect(screen.queryByTestId("settings-solo-footer")).toBeNull();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Members/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Billing/ })).toBeInTheDocument();
  });

  it("resolves a gated deep-link slug to the default section under solo", () => {
    renderSurface({ activeSlug: "members" });
    // Members is gated off → the surface falls back to the default (Profile).
    expect(
      screen.getByRole("heading", { name: "Profile" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Members/ })).toBeNull();
  });
});

describe("SettingsSurface — content router (FR-5.5)", () => {
  it("defaults an unknown initial slug to the profile default section", () => {
    renderSurface({ initialSlug: "does-not-exist" });
    expect(
      screen.getByRole("heading", { name: "Profile" }),
    ).toBeInTheDocument();
  });

  it("switches the active section when a nav tab is clicked (uncontrolled)", () => {
    const onNavigate = vi.fn();
    renderSurface({ onNavigate });
    expect(
      screen.getByRole("heading", { name: "Profile" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Appearance/ }));

    expect(onNavigate).toHaveBeenCalledWith("appearance");
    expect(
      screen.getByRole("heading", { name: "Appearance" }),
    ).toBeInTheDocument();
    const appearanceTab = screen.getByRole("tab", { name: /Appearance/ });
    expect(appearanceTab).toHaveAttribute("aria-selected", "true");
  });

  it("renders the injected section body via the renderSection slot", () => {
    renderSurface({
      renderSection: (slug) =>
        slug === "profile" ? <div>injected profile body</div> : undefined,
    });
    expect(screen.getByText("injected profile body")).toBeInTheDocument();
    // A slug with no injected body still shows the placeholder.
    fireEvent.click(screen.getByRole("tab", { name: /Shortcuts/ }));
    expect(
      screen.getByTestId("settings-placeholder-shortcuts"),
    ).toBeInTheDocument();
  });
});

describe("SettingsSurface — Advanced collapsible (FR-5.25)", () => {
  it("starts collapsed and expands on click", () => {
    renderSurface();
    const toggle = screen.getByTestId("settings-group-toggle-advanced");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tab", { name: /Key storage/ })).toBeNull();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("tab", { name: /Key storage/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /Developer tokens/ }),
    ).toBeInTheDocument();
  });

  it("respects defaultAdvancedExpanded", () => {
    renderSurface({ defaultAdvancedExpanded: true });
    expect(
      screen.getByTestId("settings-group-toggle-advanced"),
    ).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("tab", { name: /Key storage/ }),
    ).toBeInTheDocument();
  });
});

describe("SettingsSurface — savebar / toast host (FR-5.7)", () => {
  function DirtySection({
    controller,
    onSave,
  }: {
    controller: SettingsSurfaceController;
    onSave: () => void;
  }): ReactElement {
    useEffect(() => {
      controller.setDirty({ onSave, onDiscard: () => undefined });
      return () => controller.setDirty(null);
    }, [controller, onSave]);
    return <div>dirty section</div>;
  }

  it("docks a SaveBar while a section is dirty and clears it on navigation", () => {
    const onSave = vi.fn();
    renderSurface({
      renderSection: (slug, controller) =>
        slug === "profile" ? (
          <DirtySection controller={controller} onSave={onSave} />
        ) : undefined,
    });

    expect(screen.getByTestId("settings-savebar")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("settings-savebar-save"));
    expect(onSave).toHaveBeenCalledTimes(1);

    // Switching to a clean section drops the savebar (slug-tagged state).
    fireEvent.click(screen.getByRole("tab", { name: /Appearance/ }));
    expect(screen.queryByTestId("settings-savebar")).toBeNull();
  });

  it("fires a Toast (not the SaveBar) for a one-shot action", () => {
    function ToastSection({
      controller,
    }: {
      controller: SettingsSurfaceController;
    }): ReactElement {
      return (
        <button
          type="button"
          onClick={() => controller.showToast({ message: "Export queued" })}
        >
          Export
        </button>
      );
    }
    renderSurface({
      renderSection: (slug, controller) =>
        slug === "profile" ? (
          <ToastSection controller={controller} />
        ) : undefined,
    });

    expect(screen.queryByTestId("settings-toast")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Export" }));

    const toast = screen.getByRole("status");
    expect(within(toast).getByText("Export queued")).toBeInTheDocument();
    // A one-shot toast must not be conflated with the dirty savebar.
    expect(screen.queryByTestId("settings-savebar")).toBeNull();
  });
});

describe("SettingsSurface — roving nav focus (a11y §9)", () => {
  it("moves focus between tabs with ArrowDown/ArrowUp", () => {
    renderSurface();
    const profileTab = screen.getByRole("tab", { name: /Profile/ });
    profileTab.focus();
    expect(profileTab).toHaveFocus();

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowDown" });
    expect(screen.getByRole("tab", { name: /Appearance/ })).toHaveFocus();

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowUp" });
    expect(profileTab).toHaveFocus();
  });
});
