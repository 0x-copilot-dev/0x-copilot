// Settings nav — single source of truth (DESIGN-SPEC §4, PRD PR-5.1 §5.1/§5.3).
//
// The settings surface has ONE canonical list of sections. It drives:
//   - the 216px nav (grouped items + collapsible Advanced),
//   - the content router (which section body is active),
//   - the profile gate (Workspace/Members/Billing/Audit only render for a
//     `team` deployment; the solo footer shows otherwise),
//   - and downstream consumers (Phase 6A command-palette entries read this
//     list rather than re-deriving their own).
//
// This replaces the two duplicated section lists the web monolith carried
// (`apps/frontend/.../settings/sections.ts` + the `SettingsSection` type in
// `SettingsScreen.tsx`). Slug identity is the contract; labels/icons are
// presentation and may change without breaking routing.
//
// Substrate-agnostic: pure data + pure functions, no React, no browser globals.

import type { DeploymentProfile } from "../providers/DeploymentProfileProvider";

// ---------------------------------------------------------------------------
// Canonical section slug union — the SSOT identity for every settings section.
// ---------------------------------------------------------------------------

export type SettingsSectionSlug =
  // Account
  | "profile"
  | "appearance"
  | "shortcuts"
  // Models & keys
  | "provider-keys"
  | "models"
  | "local-models"
  | "model-behavior"
  // Data & privacy
  | "privacy"
  // Notifications
  | "notifications"
  // Advanced (collapsible)
  | "app-lock"
  | "developer-tokens"
  // Team-gated admin (only when deployment_profile === "team")
  | "workspace"
  | "members"
  | "billing"
  | "audit";

export type SettingsNavGroupId =
  | "account"
  | "models"
  | "data"
  | "notifications"
  | "advanced"
  | "workspace";

// Icon token names from DESIGN-SPEC §7's stroke-icon set. Carried in the SSOT
// so the host (which owns the actual icon components) can render them and the
// command palette can reuse them; this module never imports an icon component.
export type SettingsNavIcon =
  | "user"
  | "sun"
  | "cmd"
  | "key"
  | "chip"
  | "sliders"
  | "shield"
  | "bell"
  | "lock"
  | "bolt"
  | "gear"
  | "coin"
  | "activity";

/** The only profile that unlocks team-admin sections. */
export type SettingsProfileGate = "team";

export interface SettingsNavItem {
  readonly id: SettingsSectionSlug;
  readonly label: string;
  readonly icon: SettingsNavIcon;
  readonly group: SettingsNavGroupId;
  /** Mono tag rendered after the label (DESIGN-SPEC §4: Provider keys "BYOK"). */
  readonly tag?: string;
  /** When set, the item renders only under a matching deployment profile. */
  readonly profileGate?: SettingsProfileGate;
}

interface SettingsNavGroupMeta {
  readonly id: SettingsNavGroupId;
  readonly label: string;
  /** Advanced is the only collapsible group (DESIGN-SPEC §4). */
  readonly collapsible?: boolean;
  /** Whole-group gate (the Workspace group is team-only). */
  readonly profileGate?: SettingsProfileGate;
}

// ---------------------------------------------------------------------------
// The canonical data. Group ORDER is the nav order; item order within a group
// follows DESIGN-SPEC §4.
// ---------------------------------------------------------------------------

export const SETTINGS_NAV_GROUPS: readonly SettingsNavGroupMeta[] = [
  { id: "account", label: "Account" },
  { id: "models", label: "Models & keys" },
  { id: "data", label: "Data & privacy" },
  { id: "notifications", label: "Notifications" },
  { id: "advanced", label: "Advanced", collapsible: true },
  { id: "workspace", label: "Workspace", profileGate: "team" },
];

export const SETTINGS_NAV_ITEMS: readonly SettingsNavItem[] = [
  // Account
  { id: "profile", label: "Profile", icon: "user", group: "account" },
  { id: "appearance", label: "Appearance", icon: "sun", group: "account" },
  { id: "shortcuts", label: "Shortcuts", icon: "cmd", group: "account" },
  // Models & keys
  {
    id: "provider-keys",
    label: "Provider keys",
    icon: "key",
    group: "models",
    tag: "BYOK",
  },
  // Distinct icon from "Model & behavior" (sliders): the Models curation page
  // is a chip-style catalog, so it takes `coin` to avoid a duplicate glyph (PRD-E).
  { id: "models", label: "Models", icon: "coin", group: "models" },
  { id: "local-models", label: "Local models", icon: "chip", group: "models" },
  {
    id: "model-behavior",
    label: "Model & behavior",
    icon: "sliders",
    group: "models",
  },
  // Data & privacy
  {
    id: "privacy",
    label: "Privacy & retention",
    icon: "shield",
    group: "data",
  },
  // Notifications
  {
    id: "notifications",
    label: "Notifications",
    icon: "bell",
    group: "notifications",
  },
  // Advanced
  {
    id: "app-lock",
    label: "Key storage & app lock",
    icon: "lock",
    group: "advanced",
  },
  {
    id: "developer-tokens",
    label: "Developer tokens",
    icon: "bolt",
    group: "advanced",
  },
  // Team-gated admin
  {
    id: "workspace",
    label: "General",
    icon: "gear",
    group: "workspace",
    profileGate: "team",
  },
  {
    id: "members",
    label: "Members",
    icon: "user",
    group: "workspace",
    profileGate: "team",
  },
  {
    id: "billing",
    label: "Billing",
    icon: "coin",
    group: "workspace",
    profileGate: "team",
  },
  {
    id: "audit",
    label: "Audit log",
    icon: "activity",
    group: "workspace",
    profileGate: "team",
  },
];

/** The section shown when nothing (or an unknown/gated slug) is requested. */
export const DEFAULT_SETTINGS_SLUG: SettingsSectionSlug = "profile";

/** Footer copy shown only on the solo profile (DESIGN-SPEC §4 [DECISION]). */
export const SOLO_FOOTER_COPY =
  "Solo desktop mode. Workspace, members & billing appear only when 0xCopilot is deployed for a team.";

// ---------------------------------------------------------------------------
// Derivation helpers — the surface + palette read the nav through these so the
// profile gate lives in exactly one place.
// ---------------------------------------------------------------------------

function itemAllowed(
  item: SettingsNavItem,
  profile: DeploymentProfile,
): boolean {
  // Only an explicit matching profile unlocks a gated item; everything else
  // (incl. the solo desktop) keeps team-admin sections hidden, so they can
  // never leak into a single-user build.
  return item.profileGate === undefined || item.profileGate === profile;
}

export interface SettingsNavGroupView {
  readonly id: SettingsNavGroupId;
  readonly label: string;
  readonly collapsible: boolean;
  readonly items: readonly SettingsNavItem[];
}

/**
 * The nav groups (in display order) with their profile-visible items. Groups
 * whose items are all gated off — or that are themselves gated — are omitted.
 */
export function settingsNavForProfile(
  profile: DeploymentProfile,
): readonly SettingsNavGroupView[] {
  const views: SettingsNavGroupView[] = [];
  for (const group of SETTINGS_NAV_GROUPS) {
    if (group.profileGate !== undefined && group.profileGate !== profile) {
      continue;
    }
    const items = SETTINGS_NAV_ITEMS.filter(
      (item) => item.group === group.id && itemAllowed(item, profile),
    );
    if (items.length === 0) continue;
    views.push({
      id: group.id,
      label: group.label,
      collapsible: group.collapsible ?? false,
      items,
    });
  }
  return views;
}

/** Flat list of section slugs visible under a profile, in nav order. */
export function visibleSettingsSlugs(
  profile: DeploymentProfile,
): readonly SettingsSectionSlug[] {
  return SETTINGS_NAV_ITEMS.filter((item) => itemAllowed(item, profile)).map(
    (item) => item.id,
  );
}

/** True when `slug` is a real, profile-visible section. */
export function isSettingsSlugVisible(
  slug: string | null | undefined,
  profile: DeploymentProfile,
): slug is SettingsSectionSlug {
  if (slug === null || slug === undefined) return false;
  return visibleSettingsSlugs(profile).some((visible) => visible === slug);
}

/**
 * Resolve a requested slug to a real, profile-visible section. An unknown slug,
 * a slug gated off for this profile, or a null/undefined request all fall back
 * to {@link DEFAULT_SETTINGS_SLUG} rather than erroring (FR-5.5).
 */
export function resolveSettingsSlug(
  slug: string | null | undefined,
  profile: DeploymentProfile,
): SettingsSectionSlug {
  return isSettingsSlugVisible(slug, profile) ? slug : DEFAULT_SETTINGS_SLUG;
}

/** The solo footer shows on every non-team profile. */
export function showSoloFooter(profile: DeploymentProfile): boolean {
  return profile !== "team";
}

/** Look up a nav item by slug (labels/icons for placeholders, palette, etc.). */
export function settingsNavItem(
  slug: SettingsSectionSlug,
): SettingsNavItem | undefined {
  return SETTINGS_NAV_ITEMS.find((item) => item.id === slug);
}
