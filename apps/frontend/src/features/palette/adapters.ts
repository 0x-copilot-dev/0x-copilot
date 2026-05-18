// Pure adapter functions for the Palette destination data binder.
// `PaletteHit` wire rows → `CommandPaletteEntry` rows the existing
// chat-surface `<CommandPalette>` renders today (via its `extraEntries`
// prop). When the P12-B3 `<CommandPalette>` revamp lands with the
// canonical `PaletteHit` row type, drop this adapter and pass the
// wire rows straight through.

import type { ArtifactRoute } from "@enterprise-search/chat-surface";
import type { PaletteHit } from "@enterprise-search/api-types";

/**
 * Structurally compatible with chat-surface's `CommandPaletteEntry`
 * (declared in `packages/chat-surface/src/palette/CommandPalette.tsx`).
 * The chat-surface package only re-exports the `CommandPalette`
 * component itself, not the entry type — keeping a local mirror here
 * lets the host pass typed entries through `extraEntries` without
 * touching the package barrel. Drop this in favour of the canonical
 * re-export when P12-B3 lands.
 */
export interface CommandPaletteEntry {
  readonly id: string;
  readonly label: string;
  readonly hint?: string;
  readonly route: ArtifactRoute;
}

const HINT_BY_KIND: Record<PaletteHit["kind"], string> = {
  navigation: "Destination",
  entity: "Open",
  action: "Action",
  command: "Command",
};

/**
 * Convert a `PaletteHit` to a `CommandPaletteEntry` the existing
 * chat-surface command palette can render. Returns `null` when the hit
 * is not navigable from the current chat-surface route surface
 * (caller filters those out).
 *
 *   * `navigation` hits with a `route` map to a chat destination if the
 *     route is a known top-level path (`/home`, `/inbox`, …); otherwise
 *     fall back to the workspace stub so the palette still surfaces the
 *     row but Enter is a no-op until host routing catches up.
 *   * `entity` hits map to the corresponding `ArtifactRoute` (chat /
 *     conversation / run / …) based on the `ItemRef`'s kind.
 *   * `action` / `command` hits get a workspace stub today; full action
 *     dispatch lands when the chat-surface palette gains an action
 *     registry (P12-B3 sibling).
 */
export function paletteHitToEntry(hit: PaletteHit): CommandPaletteEntry | null {
  const route = artifactRouteForHit(hit);
  if (route === null) return null;
  return {
    id: hit.id,
    label: hit.title,
    hint: hit.subtitle ?? HINT_BY_KIND[hit.kind],
    route,
  };
}

function artifactRouteForHit(hit: PaletteHit): ArtifactRoute | null {
  if (hit.kind === "entity" && hit.target !== undefined) {
    switch (hit.target.kind) {
      case "chat":
        return { kind: "chat", conversationId: hit.target.id };
      case "run":
        return { kind: "run", runId: hit.target.id };
      case "subagent":
        return {
          kind: "subagent",
          runId: hit.target.id,
          subagentId: "root",
        };
      case "tool_result":
        return {
          kind: "tool-result",
          runId: hit.target.id,
          stepId: "1",
        };
      case "skill":
        return { kind: "skill", skillId: hit.target.id };
      case "project":
        return { kind: "workspace", workspaceId: hit.target.id };
      // Other ItemRef kinds (todo / inbox_item / library_* / agent /
      // tool / connector / person / memory / routine / approval /
      // meeting_external) don't have a stable ArtifactRoute mapping yet
      // — the workspace stub is the safe fallback so the palette still
      // surfaces the row.
      default:
        return { kind: "workspace", workspaceId: hit.target.id as string };
    }
  }
  // Navigation hits whose `route` slugs map to known destinations: the
  // existing CommandPalette renders ROUTE_TABLE entries by default, so
  // we don't need to re-emit those here — but we can still expose them
  // as workspace stubs to keep the row clickable.
  return { kind: "workspace", workspaceId: hit.id };
}

/**
 * Convert a list of hits to a list of entries, dropping unmappable
 * rows. Pure.
 */
export function paletteHitsToEntries(
  hits: ReadonlyArray<PaletteHit>,
): ReadonlyArray<CommandPaletteEntry> {
  const out: CommandPaletteEntry[] = [];
  for (const hit of hits) {
    const entry = paletteHitToEntry(hit);
    if (entry !== null) {
      out.push(entry);
    }
  }
  return out;
}
