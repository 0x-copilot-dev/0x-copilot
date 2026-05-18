// PaletteHost — web-substrate adapter for the ⌘K palette
// (sub-PRD §7.3 / §7.5).
//
// Wires:
//   * a `PaletteSearchPort` implementation that calls
//     `paletteApi.search` through the facade;
//   * the chat-surface `<CommandPalette>` shell (which already owns
//     ⌘K / Esc / ↑↓ / Enter keyboard handling — sub-PRD §7.3 keyboard
//     contract).
//
// Until the P12-B3 chat-surface palette revamp lands, the search port is
// pre-flighted once on mount (and re-fetched on identity change) and the
// hits are passed as `extraEntries` to the existing `<CommandPalette>`.
// When the chat-surface palette gains real search-as-you-type wiring,
// PaletteHost will hand the port through instead of pre-loading.
//
// MUST be mounted exactly once at the App.tsx root so the ⌘K hotkey is
// global and the underlying CommandPalette renders one modal scrim per
// page.

import { useEffect, useMemo, useState, type ReactElement } from "react";

import { CommandPalette } from "@enterprise-search/chat-surface";
import type {
  PaletteSearchRequest,
  PaletteSearchResponse,
} from "@enterprise-search/api-types";

import type { RequestIdentity } from "../../api/config";
import { searchPalette } from "../../api/paletteApi";
import { paletteHitsToEntries, type CommandPaletteEntry } from "./adapters";
import type { PaletteSearchPort } from "./paletteSearchPort";

interface PaletteHostProps {
  readonly identity: RequestIdentity;
}

/**
 * Build the web `PaletteSearchPort` — a thin wrapper over the facade
 * HTTP endpoint. Pure factory; the closed-over identity goes through
 * the canonical `paletteApi.search` adapter.
 */
export function createWebPaletteSearchPort(
  identity: RequestIdentity,
): PaletteSearchPort {
  return {
    search(req: PaletteSearchRequest): Promise<PaletteSearchResponse> {
      return searchPalette(identity, req);
    },
  };
}

export function PaletteHost({ identity }: PaletteHostProps): ReactElement {
  const [hits, setHits] = useState<ReadonlyArray<CommandPaletteEntry>>([]);

  const port = useMemo<PaletteSearchPort>(
    () => createWebPaletteSearchPort(identity),
    [identity],
  );

  // Pre-flight an empty-query search so the palette shows server-ranked
  // suggestions the moment the user opens it (sub-PRD §3.3 — empty `q`
  // is allowed; the server returns its default "no-query" hits). Re-runs
  // whenever the active identity changes.
  useEffect(() => {
    let cancelled = false;
    port
      .search({ q: "", limit: 25 })
      .then((res) => {
        if (cancelled) return;
        setHits(paletteHitsToEntries(res.hits));
      })
      .catch(() => {
        // Palette is best-effort — a 503 / network blip should not
        // crash the host. The user still gets the chat-surface default
        // entries (destinations + chats).
        if (!cancelled) setHits([]);
      });
    return () => {
      cancelled = true;
    };
  }, [port]);

  return (
    <div data-testid="palette-host" data-hit-count={hits.length}>
      <CommandPalette extraEntries={hits} />
    </div>
  );
}
