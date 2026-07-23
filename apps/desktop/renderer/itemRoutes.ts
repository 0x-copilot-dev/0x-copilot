// Desktop host — the single `ItemKind → ArtifactRoute` table (PRD-04 Seam B).
//
// Cross-destination ROUTE resolution is a HOST fact. On desktop the host route
// union is `ArtifactRoute` (the editor-area URI scheme). This table maps a
// kind+id to an `ArtifactRoute`; `<ItemLink>` hands it straight to the desktop
// Router. A kind with no meaningful desktop target returns `null` → inert text.
//
// The return type is `ArtifactRoute | null`, so `tsc` proves every emitted
// route is a real desktop route (the desktop analogue of the web table's
// `/settings#undefined`-is-unreachable guarantee).

import {
  hasItemRoute,
  registerItemRoute,
  type ArtifactRoute,
} from "@0x-copilot/chat-surface";
import type { ItemKind } from "@0x-copilot/api-types";

/**
 * The desktop route (if any) an `ItemRef` of `kind` opens. Returns `null` for
 * kinds with no desktop target — those render as inert text.
 */
export function desktopItemRoute(
  kind: ItemKind,
  id: string,
): ArtifactRoute | null {
  switch (kind) {
    // A chat opens the Run cockpit bound to the conversation (the cockpit binds
    // by conversation id — the desktop shell reacts to `conversation`/`chat`).
    case "chat":
      return { kind: "conversation", conversationId: id };
    case "run":
      return { kind: "run", runId: id };
    case "skill":
      return { kind: "skill", skillId: id };
    // Everything else routes into the artifact workspace pane keyed by its id —
    // ArtifactRoute's stable catch-all (`workspace`), so the link is navigable
    // rather than a broken route.
    case "project":
    case "tool":
    case "connector":
    case "library_file":
    case "library_page":
    case "library_dataset":
    case "agent":
    case "person":
    case "memory":
    case "routine":
    case "inbox_item":
    case "todo":
      return { kind: "workspace", workspaceId: id };
    // Sub-run refs + ephemeral kinds have no standalone desktop artifact.
    case "subagent":
    case "tool_result":
    case "approval":
    case "meeting_external":
      return null;
  }
}

/**
 * The kinds the desktop surfaces navigate to. `registerDesktopItemRoutes`
 * registers exactly these; the conformance test asserts each returns a non-null
 * `ArtifactRoute`.
 */
export const DESKTOP_NAVIGABLE_KINDS: ReadonlyArray<ItemKind> = [
  "chat",
  "run",
  "skill",
  "project",
  "tool",
  "connector",
  "library_file",
  "library_page",
  "library_dataset",
  "agent",
  "person",
  "memory",
  "routine",
  "inbox_item",
  "todo",
];

/**
 * Register the desktop route table into the shared `<ItemLink>` route registry.
 * Called once at renderer boot. Idempotent (guards each kind) so a re-mount /
 * test re-import doesn't throw `ItemRouteAlreadyRegistered`.
 */
export function registerDesktopItemRoutes(): void {
  for (const kind of DESKTOP_NAVIGABLE_KINDS) {
    if (!hasItemRoute(kind)) {
      registerItemRoute(kind, (id) => desktopItemRoute(kind, id));
    }
  }
}
