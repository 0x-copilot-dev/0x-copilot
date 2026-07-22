// Settings slug → page ownership SSOT (D5 web-convergence capstone).
//
// `settingsNav.ts` is the single source of truth for the section SLUGS; this
// module is the single source of truth for WHO PROVIDES EACH SLUG'S BODY:
//
//   "chat-surface" — this package exports a `*Page` component that fills the
//                    `SettingsSurface.renderSection` slot for the slug. Both
//                    hosts mount the SAME component (web/desktop convergence).
//   "host"         — the body is supplied by the host binder itself; there is
//                    no chat-surface page. The team-admin sections
//                    (Workspace/Members/Billing/Audit) are host-owned because
//                    they render only under the `team` profile and live in the
//                    host apps (e.g. web `WorkspaceSettings`).
//
// The map is EXHAUSTIVE over `SettingsSectionSlug` (a `Record<…>`), so adding a
// new slug to the nav union fails to compile until it is classified here — that
// is the compile-time guard against "a nav entry with no page". The runtime
// invariant test (`settingsPages.test.ts`) closes the loop: it asserts every
// chat-surface-owned slug resolves to exactly one real, exported page component
// and every such page is reachable by exactly one slug (no page without a
// route, no route without a page).
//
// Substrate-agnostic: pure data + pure functions, no React, no browser globals
// (mirrors `settingsNav.ts`).

import type { SettingsSectionSlug } from "./settingsNav";

/** Who fills a settings slug's `SettingsSurface` body. */
export type SettingsPageOwner = "chat-surface" | "host";

/**
 * SSOT: the body owner for every settings slug. Exhaustive by construction —
 * a new `SettingsSectionSlug` must be classified here or the package fails to
 * typecheck.
 */
export const SETTINGS_PAGE_OWNERSHIP: Record<
  SettingsSectionSlug,
  SettingsPageOwner
> = {
  // Account
  profile: "chat-surface",
  appearance: "chat-surface",
  shortcuts: "chat-surface",
  // Models & keys
  "provider-keys": "chat-surface",
  models: "chat-surface",
  "local-models": "chat-surface",
  "model-behavior": "chat-surface",
  // Data & privacy
  privacy: "chat-surface",
  // Notifications
  notifications: "chat-surface",
  // Advanced
  "app-lock": "chat-surface",
  "developer-tokens": "chat-surface",
  // Team-gated admin — host-owned (no chat-surface page)
  workspace: "host",
  members: "host",
  billing: "host",
  audit: "host",
};

/** The body owner for a slug. */
export function settingsPageOwner(
  slug: SettingsSectionSlug,
): SettingsPageOwner {
  return SETTINGS_PAGE_OWNERSHIP[slug];
}

/** Slugs whose body is a chat-surface `*Page` component (mounted by both hosts). */
export function chatSurfaceOwnedSlugs(): readonly SettingsSectionSlug[] {
  return (Object.keys(SETTINGS_PAGE_OWNERSHIP) as SettingsSectionSlug[]).filter(
    (slug) => SETTINGS_PAGE_OWNERSHIP[slug] === "chat-surface",
  );
}

/** Slugs whose body the host supplies (team-admin sections). */
export function hostOwnedSlugs(): readonly SettingsSectionSlug[] {
  return (Object.keys(SETTINGS_PAGE_OWNERSHIP) as SettingsSectionSlug[]).filter(
    (slug) => SETTINGS_PAGE_OWNERSHIP[slug] === "host",
  );
}
