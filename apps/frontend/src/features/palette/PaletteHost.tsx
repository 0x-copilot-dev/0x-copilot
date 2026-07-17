// PaletteHost — web-substrate adapter for the global ⌘K palette.
//
// Wires:
//   * a `PaletteSearchPort` implementation that calls
//     `paletteApi.search` through the facade;
//   * the canonical chat-surface `<CommandPalette>` (P12-B3) — host owns
//     the `open` state and the ⌘K / Esc hotkey via
//     `useCommandPaletteHotkey`.
//
// MUST be mounted exactly once at the App.tsx root so the ⌘K hotkey is
// global and the underlying CommandPalette renders one modal scrim per
// page.

import { useCallback, useMemo, useState, type ReactElement } from "react";

import {
  CommandPalette,
  useCommandPaletteHotkey,
} from "@0x-copilot/chat-surface";
import type {
  PaletteHit,
  PaletteSearchRequest,
  PaletteSearchResponse,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { searchPalette } from "../../api/paletteApi";
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

/**
 * Default starter actions shown when the input is empty. The host
 * owns these — chat-surface renders them; the server is consulted
 * once the user types.
 */
const STARTER_ACTIONS: ReadonlyArray<PaletteHit> = [
  {
    id: "starter_search_team",
    kind: "navigation",
    title: "Search the team",
    subtitle: "Find a person, role, or owner",
    route: "/team",
    score: 1,
  },
  {
    id: "starter_open_todos",
    kind: "navigation",
    title: "Open my todos",
    subtitle: "Today, overdue, and upcoming",
    route: "/todos",
    score: 1,
  },
  {
    id: "starter_new_chat",
    kind: "action",
    title: "Start a chat",
    subtitle: "Open the composer on a fresh conversation",
    action_token: "start_new_chat",
    score: 1,
  },
  {
    id: "starter_open_settings",
    kind: "navigation",
    title: "Open settings",
    subtitle: "Notifications, security, profile",
    route: "/settings",
    score: 1,
  },
];

export function PaletteHost({ identity }: PaletteHostProps): ReactElement {
  const [open, setOpen] = useState(false);

  const port = useMemo<PaletteSearchPort>(
    () => createWebPaletteSearchPort(identity),
    [identity],
  );

  const handleOpen = useCallback(() => setOpen(true), []);
  const handleClose = useCallback(() => setOpen(false), []);

  useCommandPaletteHotkey({ onOpen: handleOpen });

  return (
    <div data-testid="palette-host" data-palette-open={open ? "true" : "false"}>
      <CommandPalette
        open={open}
        onRequestClose={handleClose}
        searchPort={port}
        starterActions={STARTER_ACTIONS}
      />
    </div>
  );
}
