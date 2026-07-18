// Desktop command-palette registry (DESIGN-SPEC §6, PRD PR-6.3 / FR-6.5).
//
// The static list of `PaletteHit`s the solo desktop ⌘K palette offers. It
// is consumed by `DesktopPaletteSearchPort` (this phase) and — once PR-6.4
// lands — passed to the canonical `<CommandPalette>` as both the search
// source and the empty-query starter list.
//
// Two dispatch conventions the host (PR-6.4) relies on:
//
//   * Rail navigation — `kind: "navigation"`, `route` = a bare
//     `ShellDestinationSlug`. The host forwards it straight to the shell's
//     `onNavigate(slug)`.
//   * Settings navigation — `kind: "navigation"`, `route` = `"settings"`
//     (default section) or `"settings/<section>"`. The host detects the
//     prefix (see `isSettingsRoute` / `settingsSectionFromRoute`) and opens
//     the Settings surface at that section instead of navigating the rail.
//   * Actions — `kind: "action"`, `action_token` = an opaque token the
//     host's action registry knows how to launch.
//
// Single source of truth: the 6 navigation entries are DERIVED from
// `destinationsForProfile("single_user_desktop")` rather than re-hardcoded,
// so the solo relabel (`connectors` → "Tools", `tools` → "Skills") and the
// slug identity stay byte-identical to the rail. Re-typing them here would
// be a second source that could silently drift.
//
// Substrate-agnostic: pure data + pure functions. No React, no browser
// globals, no network.

import type { PaletteHit } from "@0x-copilot/api-types";
import {
  destinationsForProfile,
  type DeploymentProfile,
  type SettingsSectionSlug,
  type ShellDestination,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";

// ---------------------------------------------------------------------------
// Settings-route convention (shared with the host that dispatches it)
// ---------------------------------------------------------------------------

/** Route stem for settings navigation hits: `settings` or `settings/<slug>`. */
export const SETTINGS_ROUTE_PREFIX = "settings";

/** Build a settings-nav route. Omitting the section → default section. */
function settingsRoute(section?: SettingsSectionSlug): string {
  return section === undefined
    ? SETTINGS_ROUTE_PREFIX
    : `${SETTINGS_ROUTE_PREFIX}/${section}`;
}

/** True when a navigation hit's `route` targets the Settings surface. */
export function isSettingsRoute(route: string): boolean {
  return (
    route === SETTINGS_ROUTE_PREFIX ||
    route.startsWith(`${SETTINGS_ROUTE_PREFIX}/`)
  );
}

/**
 * The settings section a settings-nav route targets, or `undefined` for the
 * bare `settings` route (host opens the default section, `profile`). Returns
 * `undefined` for any non-settings route.
 */
export function settingsSectionFromRoute(
  route: string,
): SettingsSectionSlug | undefined {
  if (!isSettingsRoute(route) || route === SETTINGS_ROUTE_PREFIX) {
    return undefined;
  }
  return route.slice(SETTINGS_ROUTE_PREFIX.length + 1) as SettingsSectionSlug;
}

// ---------------------------------------------------------------------------
// Navigation entries — derived from the solo rail (SSOT: destinations.ts)
// ---------------------------------------------------------------------------

/** The desktop palette mirrors the single-user solo rail (DESIGN-SPEC §6). */
const DESKTOP_PROFILE: DeploymentProfile = "single_user_desktop";

// DESIGN-SPEC §7 stroke-icon tokens, keyed by rail slug. Optional hints the
// host renders; a missing entry simply omits the icon.
const RAIL_ICON_BY_SLUG: Partial<Record<ShellDestinationSlug, string>> = {
  run: "run",
  chats: "chats",
  projects: "folder",
  activity: "activity",
  connectors: "plug",
  tools: "skill",
};

function navigationHit(destination: ShellDestination): PaletteHit {
  return {
    id: `nav-${destination.slug}`,
    kind: "navigation",
    title: `Go to ${destination.label}`,
    route: destination.slug,
    icon_hint: RAIL_ICON_BY_SLUG[destination.slug],
    score: 1,
  };
}

// The 6 solo destinations in rail order: Run, Chats, Projects, Activity,
// Tools (slug `connectors`), Skills (slug `tools`).
const NAVIGATION_ENTRIES: readonly PaletteHit[] =
  destinationsForProfile(DESKTOP_PROFILE).map(navigationHit);

// ---------------------------------------------------------------------------
// Settings-navigation entries (DESIGN-SPEC §6: 3 entries)
// ---------------------------------------------------------------------------

const SETTINGS_ENTRIES: readonly PaletteHit[] = [
  {
    id: "settings-model-behavior",
    kind: "navigation",
    title: "Model & behavior",
    subtitle: "Default model, temperature, and approvals",
    route: settingsRoute("model-behavior"),
    icon_hint: "sliders",
    score: 1,
  },
  {
    id: "settings-appearance",
    kind: "navigation",
    title: "Appearance",
    subtitle: "Theme and density",
    route: settingsRoute("appearance"),
    icon_hint: "sun",
    score: 1,
  },
  {
    id: "settings-open",
    kind: "navigation",
    title: "Open Settings",
    subtitle: "Profile, keys, privacy, and more",
    // Bare `settings` route → host opens the default section (`profile`).
    route: settingsRoute(),
    icon_hint: "gear",
    score: 1,
  },
];

// ---------------------------------------------------------------------------
// Action entries (DESIGN-SPEC §6: 4 entries)
// ---------------------------------------------------------------------------

const ACTION_ENTRIES: readonly PaletteHit[] = [
  {
    id: "action-new-chat",
    kind: "action",
    title: "New chat",
    subtitle: "Start a fresh run",
    action_token: "new-chat",
    icon_hint: "plus",
    score: 1,
  },
  {
    id: "action-add-provider-key",
    kind: "action",
    title: "Add a provider key",
    subtitle: "Bring your own OpenAI, Anthropic, or Gemini key",
    action_token: "add-provider-key",
    icon_hint: "key",
    score: 1,
  },
  {
    id: "action-download-local-model",
    kind: "action",
    title: "Download a local model",
    subtitle: "Pull an Ollama model to run offline",
    action_token: "download-local-model",
    icon_hint: "download",
    score: 1,
  },
  {
    id: "action-connect-tool",
    kind: "action",
    title: "Connect a tool",
    subtitle: "Add an MCP connector",
    action_token: "connect-tool",
    icon_hint: "plug",
    score: 1,
  },
];

// ---------------------------------------------------------------------------
// The registry
// ---------------------------------------------------------------------------

/**
 * The full static command registry, in display order: rail navigation (6),
 * then settings navigation (3), then actions (4). The canonical palette
 * regroups by `kind` for rendering, so the interleaving here only fixes the
 * within-group order.
 */
export const PALETTE_COMMANDS: readonly PaletteHit[] = [
  ...NAVIGATION_ENTRIES,
  ...SETTINGS_ENTRIES,
  ...ACTION_ENTRIES,
];
