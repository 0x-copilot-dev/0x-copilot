// Web host — the single `ItemKind → AppRoute` table (PRD-04 Seam B).
//
// Cross-destination ROUTE resolution is a HOST fact: only the host knows its
// own route union. chat-surface's `<ItemLink>` no longer invents a route (the
// old registry emitted `ArtifactRoute`s the web router had no screen for, so
// every click landed on `/settings#undefined`). Instead each host owns one
// table, imported at boot, that maps a kind+id to a route in ITS OWN union.
//
// This function returns `AppRoute | null` — so `tsc` proves every emitted route
// is a real web screen, making the `/settings#undefined` bug unreachable by
// construction. A kind with no mounted web destination returns `null`; the
// `<ItemLink>` then renders inert text (navigable-nowhere is honest, not a
// broken link).

import { hasItemRoute, registerItemRoute } from "@0x-copilot/chat-surface";
import type { ItemKind } from "@0x-copilot/api-types";

import type { AppRoute } from "./routes";

/**
 * The web route (if any) an `ItemRef` of `kind` opens. Returns `null` for kinds
 * with no mounted web destination — those render as inert text. The return type
 * is `AppRoute | null`, so a route that isn't a real web screen cannot compile.
 */
export function webItemRoute(kind: ItemKind, id: string): AppRoute | null {
  switch (kind) {
    // Conversation-bound surfaces open the Run cockpit on the conversation id.
    case "chat":
    case "run":
      return { screen: "chat", destination: "run", subPath: id };
    case "project":
      return { screen: "chat", destination: "projects", subPath: id };
    case "tool":
    case "connector":
      return { screen: "chat", destination: "tools", subPath: id };
    // Agents + Inbox folded into Activity (routes.ts FOLDED_DESTINATION_REDIRECTS).
    case "agent":
    case "inbox_item":
      return { screen: "chat", destination: "activity" };
    // No mounted web destination — render inert text rather than a broken route.
    // (`skill` has no chat-surface destination slug on the web rail.)
    case "skill":
    case "subagent":
    case "tool_result":
    case "todo":
    case "library_file":
    case "library_page":
    case "library_dataset":
    case "person":
    case "memory":
    case "routine":
    case "approval":
    case "meeting_external":
      return null;
  }
}

/**
 * The kinds the web surfaces navigate to. `registerWebItemRoutes` registers a
 * resolver for exactly these; the conformance test (itemRoutes.test.ts) asserts
 * each returns a non-null `AppRoute`.
 */
export const WEB_NAVIGABLE_KINDS: ReadonlyArray<ItemKind> = [
  "chat",
  "run",
  "project",
  "tool",
  "connector",
  "agent",
  "inbox_item",
];

/**
 * Register the web route table into the shared `<ItemLink>` route registry.
 * Called once at App boot. Idempotent (guards each kind) so a re-mount / test
 * re-import doesn't throw `ItemRouteAlreadyRegistered`.
 */
export function registerWebItemRoutes(): void {
  for (const kind of WEB_NAVIGABLE_KINDS) {
    if (!hasItemRoute(kind)) {
      registerItemRoute(kind, (id) => webItemRoute(kind, id));
    }
  }
}
