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
  type ShellCommandIntent,
} from "@0x-copilot/chat-surface";
import type {
  PaletteSearchRequest,
  PaletteSearchResponse,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { searchPalette } from "../../api/paletteApi";
import type { PaletteSearchPort } from "./paletteSearchPort";

interface PaletteHostProps {
  readonly identity: RequestIdentity;
  /**
   * Optional controlled open-state so the shell topbar's ⌘K trigger
   * (`ChatShell.onOpenCommandPalette`) can open the same palette. Falls back to
   * internal state when omitted, keeping ⌘K-only mounts working.
   */
  readonly open?: boolean;
  readonly onOpenChange?: (open: boolean) => void;
  /**
   * Maps a ⌘K command's intent to real navigation. App.tsx owns the router, so
   * it supplies this. When omitted, commands are close-only (PRD-D).
   */
  readonly onCommand?: (intent: ShellCommandIntent) => void;
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

export function PaletteHost({
  identity,
  open: controlledOpen,
  onOpenChange,
  onCommand,
}: PaletteHostProps): ReactElement {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = onOpenChange ?? setInternalOpen;

  const port = useMemo<PaletteSearchPort>(
    () => createWebPaletteSearchPort(identity),
    [identity],
  );

  const handleOpen = useCallback(() => setOpen(true), [setOpen]);
  const handleClose = useCallback(() => setOpen(false), [setOpen]);

  useCommandPaletteHotkey({ onOpen: handleOpen });

  return (
    <div data-testid="palette-host" data-palette-open={open ? "true" : "false"}>
      <CommandPalette
        open={open}
        onRequestClose={handleClose}
        searchPort={port}
        onCommand={onCommand}
      />
    </div>
  );
}
