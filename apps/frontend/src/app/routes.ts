import type { ShellDestinationSlug } from "@0x-copilot/chat-surface";

import type { SettingsSection } from "../features/settings/settingsSections";

// Web-app route union. Settings + share are web-only concepts; the desktop
// substrate's editor-area model (ArtifactRoute in @0x-copilot/
// chat-surface) doesn't include them. HashRouter implements Router<AppRoute>
// so the web app's wider route shape rides through the same port that the
// desktop substrate will use for ArtifactRoute.
//
// The `chat` screen carries a `destination` slug so the AppRail rail can
// drive between the destinations without expanding the screen union.
//
// PR-4.11 (IA fold) — the web shell now renders the six-destination
// `single_user_desktop` rail (Run / Chats / Projects / Activity / Tools /
// Skills). `/` maps to the Run cockpit (`ROOT_DESTINATION`), which is where
// the working conversation surface (`ChatScreen`) now lives — so the legacy
// `/` bookmark keeps showing the chat cockpit. The seven folded slugs
// (home / library / inbox / todos / routines / agents / memory) are no longer
// on the rail; deep-links to them redirect (see `foldedRedirectFor`) instead
// of dead-ending.
// P12-C — `subPath` is an optional in-destination URL slug. Today only
// the Team and Memory destinations consume it (`/team/<id>` and
// `/memory/<id>` / `/memory/proposals` — sub-PRD
// `team-memory-cmdk-prd.md` §7.1 / §7.2); every other destination
// renders the same view regardless of `subPath`, so the field is
// `null`/absent for them. The discriminator stays on `destination` so
// existing routing code keeps its narrowing.
export type AppRoute =
  | {
      readonly screen: "chat";
      readonly destination: ShellDestinationSlug;
      readonly subPath?: string | null;
    }
  | { readonly screen: "settings"; readonly section: SettingsSection }
  // P12-C — Phase 12 settings pages (`/settings/notification-defaults`,
  // `/settings/security/webhooks`, `/settings/profile`). The new pages
  // live behind a dedicated screen kind so the legacy `SettingsScreen`
  // (which owns `/settings#<section>`) keeps its shape — sub-PRD §7.4
  // is a polish surface off the profile menu, not a destination.
  | {
      readonly screen: "settings-p12";
      readonly subPath: "notification-defaults" | "security-webhooks";
    }
  // PR 6.1/6.2 — recipient view of a shared conversation. The token is the
  // access grant; AuthGate still requires a logged-in session because v1
  // keeps shares same-org-only.
  | { readonly screen: "share"; readonly token: string }
  // Phase 7C — admin-only tier-2 adapter review queue. Mounted at
  // ``/admin/adapter-review`` (queue) and ``/admin/adapter-review/<id>``
  // (detail). The web router exposes the route shape unconditionally; the
  // admin role gate lives in App.tsx + on the backend.
  | { readonly screen: "admin-adapter-review-queue" }
  | {
      readonly screen: "admin-adapter-review-detail";
      readonly candidateId: string;
    }
  // Phase 6.5 — Project Templates gallery + editor (sub-PRD
  // `docs/atlas-new-design/destinations/projects-extensions-prd.md` §7.6).
  // Modelled as its own top-level screen (not a chat-surface
  // `ShellDestinationSlug`) because §7.6 + §12 Q1 explicitly note this is
  // NOT a top-level rail destination; the gallery is reached from the
  // Projects destination's `[Save as template]` / `[Manage templates]`
  // CTAs. Modelling it as a screen keeps the destination union owned by
  // the chat-surface package and free of host-app-only routes.
  | { readonly screen: "project-templates-gallery" }
  | {
      readonly screen: "project-templates-editor";
      readonly templateId: string;
    };

/**
 * The destination `/` maps to. PR-4.11 (IA fold) points the root at the Run
 * cockpit: `ChatScreen` (the working conversation surface) now renders under
 * the `run` slug, so keeping `/` → `run` preserves the legacy behaviour where
 * `/` opened the chat cockpit. The old Chats surface is now the archive at
 * `/chats`.
 */
export const ROOT_DESTINATION: ShellDestinationSlug = "run";

/**
 * FR-4.31 — folded-slug redirect map. Seven destinations were folded out of
 * the six-destination IA; a deep-link (typed URL / bookmark / stale link) to
 * one of them must resolve to the destination that absorbed it rather than
 * render a dead outlet:
 *
 *   - `agents`, `inbox`            → Activity (the recast run/audit feed).
 *   - `memory`                     → Settings → Privacy & data (memory review).
 *   - `home`, `library`, `todos`,
 *     `routines`                   → Run (the flagship cockpit).
 *
 * The values are stable object references so `foldedRedirectFor` can be used
 * directly as a `useEffect` dependency without churning on every render.
 */
export const FOLDED_DESTINATION_REDIRECTS = {
  home: { screen: "chat", destination: "run" },
  library: { screen: "chat", destination: "run" },
  todos: { screen: "chat", destination: "run" },
  routines: { screen: "chat", destination: "run" },
  agents: { screen: "chat", destination: "activity" },
  inbox: { screen: "chat", destination: "activity" },
  memory: { screen: "settings", section: "privacy-data" },
} as const satisfies Partial<Record<ShellDestinationSlug, AppRoute>>;

/**
 * Resolve the redirect target for a route that lands on a folded destination
 * slug, or `null` when the route is not a folded destination (the six live
 * slugs, and every non-`chat` screen, return `null`). Pure so `App.tsx` and
 * its tests share one source of truth for the fold map (FR-4.31).
 */
export function foldedRedirectFor(route: AppRoute): AppRoute | null {
  if (route.screen !== "chat") {
    return null;
  }
  const slug = route.destination as keyof typeof FOLDED_DESTINATION_REDIRECTS;
  return FOLDED_DESTINATION_REDIRECTS[slug] ?? null;
}

/**
 * PRD-12 D2/D3 — is the app on a Settings screen? The web app renders Settings
 * as its OWN route (`settings` — the legacy `/settings#<section>` surface — and
 * `settings-p12` — the Phase-12 polish pages). One predicate is the single
 * source for BOTH the rail's `settingsActive` highlight (D2) and ChatShell's
 * chrome suppression (D3, via `buildWebShellBinding`), so the two can never
 * disagree. It replaces the old `ROOT_DESTINATION` collapse, whose comment
 * wrongly claimed the rail was hidden on non-chat screens — it is not; the rail
 * renders on every screen inside `ChatShell`.
 */
export function isSettingsScreen(route: AppRoute): boolean {
  return route.screen === "settings" || route.screen === "settings-p12";
}
