import {
  SHELL_DESTINATIONS,
  type NavigateOptions,
  type Router,
  type ShellDestinationSlug,
} from "@enterprise-search/chat-surface";

import type { SettingsSection } from "../features/settings/SettingsScreen";
import {
  DEFAULT_SETTINGS_SECTION,
  SETTINGS_SECTIONS,
} from "../features/settings/sections";

import { ROOT_DESTINATION, type AppRoute } from "./routes";

const VALID_SETTINGS_SECTIONS = new Set<string>(SETTINGS_SECTIONS);
const VALID_DESTINATIONS = new Set<string>(
  SHELL_DESTINATIONS.map((d) => d.slug),
);

// HashRouter is the web app's substrate-side implementation of the
// Router port. Every browser-history / URL-parsing concern in the web
// app lives here; App.tsx (and the rest of apps/frontend) treats routing
// as a black box behind this class.
//
// URL conventions:
//   /                           → { screen: "chat", destination: "chats" }
//   /<destination>              → { screen: "chat", destination }
//     where <destination> is one of the 11 ShellDestinationSlug values.
//   /settings#<section>         → { screen: "settings", section }
//   /share/<token>              → { screen: "share", token }
// Legacy /settings/<section> is migrated once on mount via
// migrateLegacySettingsPath (still called from App.tsx) so old bookmarks
// survive without a 404; this router treats both forms identically until
// the migrator's replaceState lands.
//
// Notification contract: subscribers fire for `navigate()` *and* external
// changes (popstate, hashchange). Implementations elsewhere should rely
// on this so they don't have to update local state separately after a
// navigate.
//
// Listener lifecycle is tied to subscriber count — not to construction —
// so React StrictMode's double-invoked `useState` initializers don't leak
// orphaned listeners on the window.
export class HashRouter implements Router<AppRoute> {
  readonly #subscribers = new Set<(route: AppRoute) => void>();
  readonly #onLocationChange = (): void => this.#emit(this.current());

  current(): AppRoute {
    return routeFromLocation();
  }

  navigate(route: AppRoute, opts?: NavigateOptions): void {
    const { path, hash } = pathForRoute(route);
    const target = `${path}${hash}`;
    const current = `${window.location.pathname}${window.location.hash}`;
    if (current !== target || window.location.search) {
      const method = opts?.replace ? "replaceState" : "pushState";
      window.history[method]({}, "", target);
    }
    this.#emit(route);
  }

  subscribe(handler: (route: AppRoute) => void): () => void {
    const becameActive = this.#subscribers.size === 0;
    this.#subscribers.add(handler);
    if (becameActive) {
      window.addEventListener("popstate", this.#onLocationChange);
      window.addEventListener("hashchange", this.#onLocationChange);
    }
    return () => {
      this.#subscribers.delete(handler);
      if (this.#subscribers.size === 0) {
        window.removeEventListener("popstate", this.#onLocationChange);
        window.removeEventListener("hashchange", this.#onLocationChange);
      }
    };
  }

  #emit(route: AppRoute): void {
    for (const handler of this.#subscribers) {
      handler(route);
    }
  }
}

function routeFromLocation(): AppRoute {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/settings") {
    const hash = window.location.hash.replace(/^#/, "");
    if (hash && isSettingsSection(hash)) {
      return { screen: "settings", section: hash };
    }
    return { screen: "settings", section: DEFAULT_SETTINGS_SECTION };
  }
  // Legacy /settings/<section> falls through here only briefly between
  // the migrator's replaceState and React's first paint. Treat it the
  // same as the modern hash form so the first paint is correct even if
  // the migrator hasn't run yet (e.g. SSR-style hydration).
  if (path.startsWith("/settings/")) {
    const section = decodeURIComponent(path.slice("/settings/".length));
    return {
      screen: "settings",
      section: isSettingsSection(section) ? section : DEFAULT_SETTINGS_SECTION,
    };
  }
  if (path.startsWith("/share/")) {
    // Token in the URL is the access grant; pass through verbatim. The
    // recipient endpoint validates it server-side. Empty path segment
    // falls through to chat (defensive — should not happen because we
    // always emit /share/<token> from the share popover).
    const token = decodeURIComponent(path.slice("/share/".length));
    if (token) {
      return { screen: "share", token };
    }
  }
  if (path === "/") {
    return { screen: "chat", destination: ROOT_DESTINATION };
  }
  // /<destination> for the 11 known slugs. Unknown paths fall through
  // to the root destination so /typo behaves like a 404 → chats rather
  // than crashing the route union.
  const segment = decodeURIComponent(path.replace(/^\//, ""));
  if (isShellDestinationSlug(segment)) {
    return { screen: "chat", destination: segment };
  }
  return { screen: "chat", destination: ROOT_DESTINATION };
}

function pathForRoute(route: AppRoute): { path: string; hash: string } {
  if (route.screen === "chat") {
    // Round-trip the root destination as `/` so external bookmarks /
    // copy-paste keep the legacy entry URL. Every other destination
    // lives at `/<slug>`.
    if (route.destination === ROOT_DESTINATION) {
      return { path: "/", hash: "" };
    }
    return { path: `/${route.destination}`, hash: "" };
  }
  if (route.screen === "share") {
    return { path: `/share/${encodeURIComponent(route.token)}`, hash: "" };
  }
  return {
    path: "/settings",
    hash: route.section === DEFAULT_SETTINGS_SECTION ? "" : `#${route.section}`,
  };
}

function isSettingsSection(value: string): value is SettingsSection {
  return VALID_SETTINGS_SECTIONS.has(value);
}

function isShellDestinationSlug(value: string): value is ShellDestinationSlug {
  return VALID_DESTINATIONS.has(value);
}

/**
 * One-shot migrator for old `/settings/<section>` paths into the new
 * `/settings#<section>` form. Called from App.tsx on mount; rewrites
 * the URL via `history.replaceState` and returns the section so the
 * first paint already lands on the right tab.
 *
 * Lives here (not in features/) so every window.history / pathname /
 * hash touchpoint in the web app stays in apps/frontend/src/app/. Old
 * bookmarks survive a release without a 404.
 *
 * Returns `null` when the URL is not a legacy settings path; the caller
 * falls through to its normal route-from-location logic.
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
  const valid = isSettingsSection(slug) ? slug : DEFAULT_SETTINGS_SECTION;
  const next = `/settings${valid === DEFAULT_SETTINGS_SECTION ? "" : `#${valid}`}`;
  window.history.replaceState(null, "", next);
  return valid;
}
