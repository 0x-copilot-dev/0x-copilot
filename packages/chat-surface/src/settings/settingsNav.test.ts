import { describe, expect, it } from "vitest";

import {
  DEFAULT_SETTINGS_SLUG,
  SETTINGS_NAV_ITEMS,
  SOLO_FOOTER_COPY,
  isSettingsSlugVisible,
  resolveSettingsSlug,
  settingsNavForProfile,
  settingsNavItem,
  showSoloFooter,
  visibleSettingsSlugs,
  type SettingsSectionSlug,
} from "./settingsNav";

const TEAM_ONLY: readonly SettingsSectionSlug[] = [
  "workspace",
  "members",
  "billing",
  "audit",
];

describe("settingsNav — profile gate (FR-5.3 / FR-5.4)", () => {
  it("hides team-admin sections on single_user_desktop", () => {
    const slugs = visibleSettingsSlugs("single_user_desktop");
    for (const gated of TEAM_ONLY) {
      expect(slugs).not.toContain(gated);
    }
    // solo still has the core sections
    expect(slugs).toContain("profile");
    expect(slugs).toContain("provider-keys");
    expect(slugs).toContain("app-lock");
  });

  it("adds Workspace / Members / Billing / Audit on team", () => {
    const slugs = visibleSettingsSlugs("team");
    for (const gated of TEAM_ONLY) {
      expect(slugs).toContain(gated);
    }
  });

  it("omits the whole Workspace group for solo but includes it for team", () => {
    const solo = settingsNavForProfile("single_user_desktop");
    expect(solo.some((group) => group.id === "workspace")).toBe(false);

    const team = settingsNavForProfile("team");
    const workspaceGroup = team.find((group) => group.id === "workspace");
    expect(workspaceGroup).toBeDefined();
    expect(workspaceGroup?.items.map((i) => i.id)).toEqual(TEAM_ONLY);
  });
});

describe("settingsNav — group structure (FR-5.2)", () => {
  it("renders the solo groups in DESIGN-SPEC §4 order", () => {
    const groups = settingsNavForProfile("single_user_desktop");
    expect(groups.map((group) => group.id)).toEqual([
      "account",
      "models",
      "data",
      "notifications",
      "advanced",
    ]);
  });

  it("marks Advanced (and only Advanced) collapsible", () => {
    const groups = settingsNavForProfile("single_user_desktop");
    for (const group of groups) {
      expect(group.collapsible).toBe(group.id === "advanced");
    }
  });

  it("groups the section items per §4", () => {
    const groups = settingsNavForProfile("single_user_desktop");
    const byId = Object.fromEntries(groups.map((g) => [g.id, g]));
    expect(byId.account.items.map((i) => i.id)).toEqual([
      "profile",
      "appearance",
      "shortcuts",
    ]);
    expect(byId.models.items.map((i) => i.id)).toEqual([
      "provider-keys",
      "local-models",
      "model-behavior",
    ]);
    expect(byId.advanced.items.map((i) => i.id)).toEqual([
      "app-lock",
      "developer-tokens",
    ]);
  });

  it("tags Provider keys with the mono BYOK label", () => {
    const providerKeys = settingsNavItem("provider-keys");
    expect(providerKeys?.tag).toBe("BYOK");
  });
});

describe("settingsNav — slug resolution (FR-5.5)", () => {
  it("returns the default section for unknown / null / undefined slugs", () => {
    expect(resolveSettingsSlug("nope", "single_user_desktop")).toBe(
      DEFAULT_SETTINGS_SLUG,
    );
    expect(resolveSettingsSlug(null, "single_user_desktop")).toBe(
      DEFAULT_SETTINGS_SLUG,
    );
    expect(resolveSettingsSlug(undefined, "team")).toBe(DEFAULT_SETTINGS_SLUG);
  });

  it("falls back to the default when a gated slug is requested under solo", () => {
    expect(resolveSettingsSlug("members", "single_user_desktop")).toBe(
      DEFAULT_SETTINGS_SLUG,
    );
    // …but the same slug resolves to itself under team.
    expect(resolveSettingsSlug("members", "team")).toBe("members");
  });

  it("passes through a visible slug unchanged", () => {
    expect(resolveSettingsSlug("appearance", "single_user_desktop")).toBe(
      "appearance",
    );
  });

  it("isSettingsSlugVisible respects the profile gate", () => {
    expect(isSettingsSlugVisible("billing", "single_user_desktop")).toBe(false);
    expect(isSettingsSlugVisible("billing", "team")).toBe(true);
    expect(isSettingsSlugVisible("profile", "single_user_desktop")).toBe(true);
  });

  it("keeps the default section visible under both profiles", () => {
    expect(
      isSettingsSlugVisible(DEFAULT_SETTINGS_SLUG, "single_user_desktop"),
    ).toBe(true);
    expect(isSettingsSlugVisible(DEFAULT_SETTINGS_SLUG, "team")).toBe(true);
  });
});

describe("settingsNav — solo footer (FR-5.3 / FR-5.4)", () => {
  it("shows the footer for solo and hides it for team", () => {
    expect(showSoloFooter("single_user_desktop")).toBe(true);
    expect(showSoloFooter("team")).toBe(false);
  });

  it("carries the exact DESIGN-SPEC §4 copy", () => {
    expect(SOLO_FOOTER_COPY).toBe(
      "Solo desktop mode. Workspace, members & billing appear only when 0xCopilot is deployed for a team.",
    );
  });
});

describe("settingsNav — SSOT integrity", () => {
  it("has unique slugs", () => {
    const ids = SETTINGS_NAV_ITEMS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("only gates team-admin sections", () => {
    for (const item of SETTINGS_NAV_ITEMS) {
      if (item.profileGate !== undefined) {
        expect(TEAM_ONLY).toContain(item.id);
      }
    }
  });
});
