// PR 4.3 — hash-based section routing for the Settings page.
//
// Two consumers in the design today:
//   * `App.tsx` resolves `/settings#<section>` on first paint and
//     keeps the URL hash in sync with the active section.
//   * "Manage" links from the chat connectors popover (PR 3.4) and the
//     topbar settings cog (PR 2.1) deep-link to a section.
//
// Native `hashchange` + `popstate` is sufficient — adopting React
// Router (or wouter) for one routing surface is over-engineering. See
// pr-4.3-settings-ai-and-data.md §3.3.
//
// The hook reads the current hash, exposes the active section + a
// `navigate` function that updates the hash, and listens to browser
// `hashchange` / `popstate` so back/forward + URL pastes round-trip.

import { useCallback, useEffect, useState } from "react";

import type { SettingsSection } from "./SettingsScreen";

/**
 * The complete list of valid hash slugs the router understands. Other
 * sections (PR 4.1 / 4.2 / legacy) keep their own slug so a paste of
 * `/settings#profile` works the same way `/settings#privacy-data`
 * does.
 *
 * Kept here (not in `App.tsx`) so PR 4.1 / 4.2 / 4.3 can each grow
 * the union without merge collisions in the route table.
 */
export const SETTINGS_SECTIONS = [
  // PR 4.1 — "You" group
  "profile",
  "appearance",
  "shortcuts",
  "notifications",
  // PR 4.2 — "Workspace" group
  "workspace",
  "members",
  "billing",
  // PR 7.1 — admin audit log under the Workspace group
  "audit-log",
  // PR 4.3 — "AI & data" group
  "model-and-behavior",
  "connectors",
  "privacy-data",
  // Legacy / misc
  "general",
  "account",
  "capabilities",
  "skills",
  "claude-code",
] as const satisfies readonly SettingsSection[];

const VALID = new Set<string>(SETTINGS_SECTIONS);

/** Slug rendered when `/settings` carries no hash. */
export const DEFAULT_SETTINGS_SECTION: SettingsSection = "profile";

function readHash(): SettingsSection {
  if (typeof window === "undefined") {
    return DEFAULT_SETTINGS_SECTION;
  }
  const raw = window.location.hash.replace(/^#/, "");
  return VALID.has(raw) ? (raw as SettingsSection) : DEFAULT_SETTINGS_SECTION;
}

/**
 * Returns the current Settings section and a navigator that updates
 * the URL hash. The hook is **idempotent** — navigating to the
 * already-active section is a no-op (no extra `pushState`).
 *
 * Round-trips with browser back/forward via `popstate`; a paste of a
 * hashed URL fires `hashchange` on the `replaceState` migrator that
 * the App owns.
 */
export function useSettingsSection(): [
  SettingsSection,
  (next: SettingsSection) => void,
] {
  const [section, setSection] = useState<SettingsSection>(readHash);

  useEffect(() => {
    const sync = (): void => {
      setSection(readHash());
    };
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
    };
  }, []);

  const navigate = useCallback(
    (next: SettingsSection) => {
      if (next === section) {
        return;
      }
      const url = `${window.location.pathname}${window.location.search}#${next}`;
      window.history.pushState(null, "", url);
      setSection(next);
    },
    [section],
  );

  return [section, navigate];
}

/**
 * One-shot migrator for old `/settings/<section>` paths into the new
 * `/settings#<section>` form. Called from `App.tsx` on mount; rewrites
 * the URL via `history.replaceState` and returns the section so the
 * first paint already lands on the right tab.
 *
 * Returns `null` when the URL is not a legacy settings path; the
 * caller falls through to its normal route-from-location logic.
 */
export function migrateLegacySettingsPath(): SettingsSection | null {
  if (typeof window === "undefined") {
    return null;
  }
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (!path.startsWith("/settings/")) {
    return null;
  }
  const slug = decodeURIComponent(path.slice("/settings/".length));
  const valid = VALID.has(slug)
    ? (slug as SettingsSection)
    : DEFAULT_SETTINGS_SECTION;
  const next = `/settings${valid === DEFAULT_SETTINGS_SECTION ? "" : `#${valid}`}`;
  window.history.replaceState(null, "", next);
  return valid;
}
