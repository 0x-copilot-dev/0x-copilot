// Slug ↔ page invariant (D5). Guards the two failure modes web/desktop
// convergence is most exposed to: a nav entry with NO page (a slug you can
// click that renders nothing but the placeholder), and a page with NO route (a
// built section component that no slug ever mounts). The ownership SSOT
// (`settingsPages.ts`) is exhaustive over the slug union at COMPILE time; this
// test closes the loop at RUNTIME by tying each chat-surface-owned slug to its
// real, exported page component (a bijection) and pinning host-owned slugs to
// the team profile gate.

import { describe, expect, it } from "vitest";

import { AppLockPage } from "./AppLockPage";
import { AppearancePage } from "./AppearancePage";
import { DeveloperTokensPage } from "./DeveloperTokensPage";
import { LocalModelsPage } from "./LocalModelsPage";
import { ModelBehaviorPage } from "./ModelBehaviorPage";
import { ModelsPage } from "./ModelsPage";
import { NotificationsPage } from "./NotificationsPage";
import { PrivacyPage } from "./PrivacyPage";
import { ProfilePage } from "./ProfilePage";
import { ProviderKeysPage } from "./ProviderKeysPage";
import { ShortcutsPage } from "./ShortcutsPage";
import { SETTINGS_NAV_ITEMS, type SettingsSectionSlug } from "./settingsNav";
import {
  SETTINGS_PAGE_OWNERSHIP,
  chatSurfaceOwnedSlugs,
  hostOwnedSlugs,
} from "./settingsPages";

// The mounted chat-surface page components, keyed by the slug that mounts them.
// This is the test's independent enumeration of "every page chat-surface
// provides"; the assertions below prove it is in bijection with the slugs the
// ownership SSOT marks "chat-surface".
const CHAT_SURFACE_PAGES: Record<string, unknown> = {
  profile: ProfilePage,
  appearance: AppearancePage,
  shortcuts: ShortcutsPage,
  "provider-keys": ProviderKeysPage,
  models: ModelsPage,
  "local-models": LocalModelsPage,
  "model-behavior": ModelBehaviorPage,
  privacy: PrivacyPage,
  notifications: NotificationsPage,
  "app-lock": AppLockPage,
  "developer-tokens": DeveloperTokensPage,
};

const TEAM_ONLY: readonly SettingsSectionSlug[] = [
  "workspace",
  "members",
  "billing",
  "audit",
];

describe("settingsPages — nav ↔ ownership bijection", () => {
  it("classifies every nav slug (no nav entry without a page home)", () => {
    for (const item of SETTINGS_NAV_ITEMS) {
      expect(SETTINGS_PAGE_OWNERSHIP[item.id]).toBeDefined();
    }
  });

  it("has no ownership entry that isn't a real nav slug", () => {
    const navSlugs = new Set(SETTINGS_NAV_ITEMS.map((item) => item.id));
    for (const slug of Object.keys(
      SETTINGS_PAGE_OWNERSHIP,
    ) as SettingsSectionSlug[]) {
      expect(navSlugs.has(slug)).toBe(true);
    }
  });

  it("covers the nav union exactly once (ownership keys === nav slugs)", () => {
    const navSlugs = [...SETTINGS_NAV_ITEMS.map((item) => item.id)].sort();
    const ownedSlugs = Object.keys(SETTINGS_PAGE_OWNERSHIP).sort();
    expect(ownedSlugs).toEqual(navSlugs);
    // No duplicate nav slugs (the ownership Record could not express them).
    expect(new Set(navSlugs).size).toBe(navSlugs.length);
  });
});

describe("settingsPages — chat-surface page reachability", () => {
  it("every chat-surface-owned slug mounts exactly one real page component", () => {
    for (const slug of chatSurfaceOwnedSlugs()) {
      const component = CHAT_SURFACE_PAGES[slug];
      // A slug with no page → the placeholder ships to users. Fail loudly.
      expect(
        component,
        `chat-surface-owned slug "${slug}" has no mounted page component`,
      ).toBeDefined();
      expect(typeof component).toBe("function");
    }
  });

  it("every mounted page is reachable by exactly one slug (no orphan page)", () => {
    const owned = new Set<string>(chatSurfaceOwnedSlugs());
    for (const slug of Object.keys(CHAT_SURFACE_PAGES)) {
      // A page keyed by a slug the SSOT does not mark chat-surface-owned is an
      // orphan — a built section with no route.
      expect(
        owned.has(slug),
        `page mounted for "${slug}" is not reachable by any chat-surface-owned slug`,
      ).toBe(true);
    }
  });

  it("the page registry and the owned-slug set are the same size (bijection)", () => {
    expect(Object.keys(CHAT_SURFACE_PAGES).length).toBe(
      chatSurfaceOwnedSlugs().length,
    );
  });
});

describe("settingsPages — host-owned slugs are exactly the team gate", () => {
  it("host-owned slugs === the team-gated admin sections", () => {
    expect([...hostOwnedSlugs()].sort()).toEqual([...TEAM_ONLY].sort());
  });

  it("every host-owned slug is a team-profile-gated nav item", () => {
    const gated = new Set(
      SETTINGS_NAV_ITEMS.filter((item) => item.profileGate === "team").map(
        (item) => item.id,
      ),
    );
    for (const slug of hostOwnedSlugs()) {
      expect(gated.has(slug)).toBe(true);
    }
  });
});
