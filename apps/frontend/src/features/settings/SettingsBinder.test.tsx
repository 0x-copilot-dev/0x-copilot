// SettingsBinder — web→SSOT section mapping (PRD-E FR-E.5).
//
// The binder bridges the web router's legacy `SettingsSection` slugs and the
// chat-surface `SettingsSectionSlug` SSOT. These tests pin the two invariants
// that keep any section from being lost during the convergence:
//   1. Every SSOT slug the settings nav can navigate to maps to a real web
//      section (so a nav click always reflects to a routable URL).
//   2. `connectors`/`skills` are the ONLY sections the binder does not own
//      (they stay on the legacy screen — rail destinations, not settings).

import { describe, expect, it } from "vitest";

import { SETTINGS_NAV_ITEMS } from "@0x-copilot/chat-surface";

import { SETTINGS_SECTIONS } from "./sections";
import { isBinderSection, webSectionForSlug } from "./SettingsBinder";
import type { SettingsSection } from "./SettingsScreen";

describe("SettingsBinder section mapping (FR-E.5)", () => {
  it("maps every SSOT nav slug to a valid web SettingsSection", () => {
    for (const item of SETTINGS_NAV_ITEMS) {
      const section = webSectionForSlug(item.id);
      expect(SETTINGS_SECTIONS).toContain(section);
    }
  });

  it("resolves the spelling deltas the web router keeps", () => {
    expect(webSectionForSlug("model-behavior")).toBe("model-and-behavior");
    expect(webSectionForSlug("privacy")).toBe("privacy-data");
    expect(webSectionForSlug("developer-tokens")).toBe("api-keys");
    expect(webSectionForSlug("audit")).toBe("audit-log");
  });

  it("falls back to profile for an unknown slug", () => {
    expect(webSectionForSlug("not-a-real-slug")).toBe("profile");
  });

  it("owns every settings section except connectors/skills", () => {
    for (const section of SETTINGS_SECTIONS) {
      const expected = section !== "connectors" && section !== "skills";
      expect(isBinderSection(section as SettingsSection)).toBe(expected);
    }
  });
});
