import type { ShellDestinationSlug } from "@enterprise-search/chat-surface";

import type { SettingsSection } from "../features/settings/SettingsScreen";

// Web-app route union. Settings + share are web-only concepts; the desktop
// substrate's editor-area model (ArtifactRoute in @enterprise-search/
// chat-surface) doesn't include them. HashRouter implements Router<AppRoute>
// so the web app's wider route shape rides through the same port that the
// desktop substrate will use for ArtifactRoute.
//
// The `chat` screen carries a `destination` slug so the AppRail rail can
// drive between the 11 destinations (home / chats / inbox / todos /
// projects / library / agents / tools / connectors / team / memory)
// without expanding the screen union. `/` is the legacy entry point and
// maps to the chats destination (the original web app surface).
export type AppRoute =
  | { readonly screen: "chat"; readonly destination: ShellDestinationSlug }
  | { readonly screen: "settings"; readonly section: SettingsSection }
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

/** The destination `/` maps to. Chats is the legacy landing page. */
export const ROOT_DESTINATION: ShellDestinationSlug = "chats";
