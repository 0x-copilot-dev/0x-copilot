// Desktop palette search port (PRD PR-6.3 / FR-6.4).
//
// A local, in-memory `PaletteSearchPort` over the static `PALETTE_COMMANDS`
// registry. The solo desktop palette makes NO network call — it filters the
// static list by a case-insensitive substring over each hit's title +
// subtitle.
//
// Behavior contract:
//   * Empty (or whitespace-only) query → the full registry, in registry
//     order. This is the palette's "starter list".
//   * Non-empty query → the entries whose title or subtitle contain the
//     query as a case-insensitive substring, preserving registry order.
//   * `limit` (when set) clamps the result length after filtering.
//   * NEVER throws / rejects. The registry is read through a guarded call;
//     if it throws, the port resolves with an EMPTY hit list (FR-6.4/6.5).
//     This is a second safety net beneath the palette's own `.catch`.
//
// Substrate-agnostic: pure, in-memory, no browser globals beyond `Date.now`.

import type {
  PaletteHit,
  PaletteSearchRequest,
  PaletteSearchResponse,
} from "@0x-copilot/api-types";
import type { PaletteSearchPort } from "@0x-copilot/chat-surface";

import { PALETTE_COMMANDS } from "./palette-commands";

/**
 * Supplies the command list. Injectable so tests can prove the never-throw
 * guarantee with a registry that throws. Defaults to the static
 * `PALETTE_COMMANDS`.
 */
export type PaletteCommandRegistry = () => readonly PaletteHit[];

/** The searchable text of a hit: its title plus subtitle, lower-cased. */
function searchableText(hit: PaletteHit): string {
  const subtitle = hit.subtitle ?? "";
  return `${hit.title} ${subtitle}`.toLowerCase();
}

/**
 * Read + filter the registry, guaranteed not to throw. A registry that
 * throws (or any error while filtering) surfaces as an empty list.
 */
function safeSearch(
  registry: PaletteCommandRegistry,
  query: string,
  limit: number | undefined,
): readonly PaletteHit[] {
  let matched: readonly PaletteHit[];
  try {
    const all = registry();
    const needle = query.trim().toLowerCase();
    matched =
      needle.length === 0
        ? all
        : all.filter((hit) => searchableText(hit).includes(needle));
  } catch {
    // FR-6.4/6.5: a thrown registry is not a hard failure — it degrades to
    // an empty result, never a rejected promise.
    return [];
  }
  if (typeof limit === "number" && limit >= 0 && limit < matched.length) {
    return matched.slice(0, limit);
  }
  return matched;
}

/**
 * Build the desktop `PaletteSearchPort`. Pure factory; the returned port
 * closes over the (defaulted) registry and holds no other state.
 */
export function createDesktopPaletteSearchPort(
  registry: PaletteCommandRegistry = () => PALETTE_COMMANDS,
): PaletteSearchPort {
  return {
    search(req: PaletteSearchRequest): Promise<PaletteSearchResponse> {
      const started = Date.now();
      const hits = safeSearch(registry, req.q, req.limit);
      const response: PaletteSearchResponse = {
        hits,
        took_ms: Math.max(0, Date.now() - started),
      };
      return Promise.resolve(response);
    },
  };
}
