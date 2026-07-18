import {
  SHELL_DESTINATIONS,
  destinationsForProfile,
  type NavigateOptions,
  type Router,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";

import type { SettingsSection } from "../features/settings/SettingsScreen";
import {
  DEFAULT_SETTINGS_SECTION,
  SETTINGS_SECTIONS,
} from "../features/settings/sections";

import { ROOT_DESTINATION, type AppRoute } from "./routes";

const VALID_SETTINGS_SECTIONS = new Set<string>(SETTINGS_SECTIONS);
// Every slug the URL layer accepts. This is the union of the legacy web rail
// (`SHELL_DESTINATIONS`) and the profile-gated solo/team rail
// (`destinationsForProfile("team")` is a superset of the solo set). The union
// is important post-IA-fold (PR-4.11): the six live solo slugs
// (`run`/`activity` are NOT in the legacy 12) MUST resolve to their route, and
// the seven folded slugs (`home`/`library`/`inbox`/`todos`/`routines`/`agents`/
// `memory`) MUST still resolve here so App.tsx can redirect them (FR-4.31)
// rather than the router silently collapsing a folded deep-link to the root.
const VALID_DESTINATIONS = new Set<string>(
  [...SHELL_DESTINATIONS, ...destinationsForProfile("team")].map((d) => d.slug),
);

// HashRouter is the web app's substrate-side implementation of the
// Router port. Every browser-history / URL-parsing concern in the web
// app lives here; App.tsx (and the rest of apps/frontend) treats routing
// as a black box behind this class.
//
// URL conventions:
//   /                           → { screen: "chat", destination: ROOT_DESTINATION }
//                                 (ROOT_DESTINATION is `run` post-IA-fold)
//   /<destination>              → { screen: "chat", destination }
//     where <destination> is any known ShellDestinationSlug (legacy ∪ solo ∪
//     team). Folded slugs still parse here; App.tsx redirects them (FR-4.31).
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
  // P12-C — new Phase 12 settings pages (sub-PRD
  // `team-memory-cmdk-prd.md` §7.4 / §4.4). These slugs land on the
  // dedicated `settings-p12` screen so the legacy `SettingsScreen`
  // shell (and its `/settings#<section>` hash form) is unaffected.
  // `/settings/security/webhooks` carries a slash inside the path so it
  // must match before the generic `/settings/<section>` legacy
  // migration branch below.
  if (path === "/settings/security/webhooks") {
    return { screen: "settings-p12", subPath: "security-webhooks" };
  }
  if (path === "/settings/notification-defaults") {
    return { screen: "settings-p12", subPath: "notification-defaults" };
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
  // Phase 7C — admin tier-2 adapter review. The route is matched
  // unconditionally here; App.tsx renders the chat surface as a
  // fallback for non-admin callers so a bookmarked link never crashes.
  if (path === "/admin/adapter-review") {
    return { screen: "admin-adapter-review-queue" };
  }
  if (path.startsWith("/admin/adapter-review/")) {
    const candidateId = decodeURIComponent(
      path.slice("/admin/adapter-review/".length),
    );
    if (candidateId) {
      return { screen: "admin-adapter-review-detail", candidateId };
    }
    return { screen: "admin-adapter-review-queue" };
  }
  // Phase 6.5 — Project Templates gallery + editor (sub-PRD §7.6).
  // `/project-templates`           → gallery
  // `/project-templates/<id>/edit` → editor for a specific template
  // `/project-templates/<id>`      → falls back to the gallery (the editor
  //                                  is the only detail surface today; the
  //                                  PRD does not specify a read-only
  //                                  detail page).
  if (path === "/project-templates") {
    return { screen: "project-templates-gallery" };
  }
  if (path.startsWith("/project-templates/")) {
    const rest = path.slice("/project-templates/".length);
    if (rest.endsWith("/edit")) {
      const templateId = decodeURIComponent(
        rest.slice(0, rest.length - "/edit".length),
      );
      if (templateId) {
        return { screen: "project-templates-editor", templateId };
      }
    }
    return { screen: "project-templates-gallery" };
  }
  if (path === "/") {
    return { screen: "chat", destination: ROOT_DESTINATION };
  }
  // /<destination>[/<subPath>] for the known destination slugs. P12-C —
  // Team + Memory destinations carry an in-destination subPath in the
  // URL today (`/team/<id>`, `/memory/<id>`, `/memory/proposals`);
  // other destinations ignore the trailing segment. Unknown paths fall
  // through to the root destination so /typo behaves like a 404 → chats
  // rather than crashing the route union.
  const segments = path.replace(/^\//, "").split("/");
  const head = decodeURIComponent(segments[0] ?? "");
  if (isShellDestinationSlug(head)) {
    const rest = segments
      .slice(1)
      .map((s) => decodeURIComponent(s))
      .filter((s) => s.length > 0)
      .join("/");
    return {
      screen: "chat",
      destination: head,
      subPath: rest.length > 0 ? rest : null,
    };
  }
  return { screen: "chat", destination: ROOT_DESTINATION };
}

function pathForRoute(route: AppRoute): { path: string; hash: string } {
  if (route.screen === "chat") {
    // Round-trip the root destination as `/` so external bookmarks /
    // copy-paste keep the legacy entry URL. Every other destination
    // lives at `/<slug>` and may carry an in-destination subPath
    // (P12-C — `/team/<id>`, `/memory/<id>`, `/memory/proposals`).
    if (route.destination === ROOT_DESTINATION) {
      return { path: "/", hash: "" };
    }
    const sub = route.subPath ?? null;
    if (sub === null || sub.length === 0) {
      return { path: `/${route.destination}`, hash: "" };
    }
    // Preserve the structural `/` so `proposals` and `<id>` and even
    // path-like ids round-trip without double-encoding the separator.
    const encoded = sub
      .split("/")
      .map((s) => encodeURIComponent(s))
      .join("/");
    return { path: `/${route.destination}/${encoded}`, hash: "" };
  }
  if (route.screen === "settings-p12") {
    if (route.subPath === "security-webhooks") {
      return { path: "/settings/security/webhooks", hash: "" };
    }
    return { path: "/settings/notification-defaults", hash: "" };
  }
  if (route.screen === "share") {
    return { path: `/share/${encodeURIComponent(route.token)}`, hash: "" };
  }
  if (route.screen === "admin-adapter-review-queue") {
    return { path: "/admin/adapter-review", hash: "" };
  }
  if (route.screen === "admin-adapter-review-detail") {
    return {
      path: `/admin/adapter-review/${encodeURIComponent(route.candidateId)}`,
      hash: "",
    };
  }
  if (route.screen === "project-templates-gallery") {
    return { path: "/project-templates", hash: "" };
  }
  if (route.screen === "project-templates-editor") {
    return {
      path: `/project-templates/${encodeURIComponent(route.templateId)}/edit`,
      hash: "",
    };
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
