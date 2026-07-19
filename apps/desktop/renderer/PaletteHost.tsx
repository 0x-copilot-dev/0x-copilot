// PaletteHost — desktop-substrate adapter for the global ⌘K palette (PR-6.4).
//
// Mirrors the web host (`apps/frontend/src/features/palette/PaletteHost.tsx`):
// it injects the desktop's LOCAL static registry port instead of the facade HTTP
// port and mounts exactly ONE `<CommandPalette>`. It is modal-only — the single
// search affordance is the shell topbar's `CommandPaletteTrigger`, wired via
// `ChatShell`'s `onOpenCommandPalette` (bootstrap → `setPaletteOpen(true)`). It no
// longer renders its own trigger (that duplicated the shell's and put two search
// boxes on the placeholder destinations).
//
// Wires (FR-6.6 / FR-6.7):
//   * one `<CommandPalette>` with `searchPort = createDesktopPaletteSearchPort()`
//     and `starterActions = PALETTE_COMMANDS` (PR-6.3);
//   * `open` state is CONTROLLED by bootstrap (`open` / `onOpenChange`); `⌘K` is
//     single-sourced through bootstrap's `useShellShortcuts` (FR-6.14) — exactly
//     one `⌘K` listener;
//   * dispatch of the palette's non-entity hits back to the host:
//       - `navigation` → a Settings deep-link (`isSettingsRoute`) opens Settings
//         at that section, otherwise the shell navigates to the destination slug;
//       - `action` → the matching host flow launcher.

import { useCallback, useMemo, type ReactElement } from "react";

import {
  CommandPalette,
  type SettingsSectionSlug,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";

import { createDesktopPaletteSearchPort } from "./DesktopPaletteSearchPort";
import {
  PALETTE_COMMANDS,
  isSettingsRoute,
  settingsSectionFromRoute,
} from "./palette-commands";

/**
 * Host flow launchers for the palette's four `action` hits (DESIGN-SPEC §6).
 * The host owns the actual flows; PaletteHost only routes `action_token` →
 * launcher. Bootstrap decides which flows are real vs interim (see its
 * `// PR-6.4:` wiring).
 */
export interface PaletteHostActionHandlers {
  /** `new-chat` — "Start a fresh run". */
  readonly onNewChat: () => void;
  /** `add-provider-key` — "Bring your own OpenAI/Anthropic/Gemini key". */
  readonly onAddProviderKey: () => void;
  /** `download-local-model` — "Pull an Ollama model to run offline". */
  readonly onDownloadLocalModel: () => void;
  /** `connect-tool` — "Add an MCP connector". */
  readonly onConnectTool: () => void;
}

export interface PaletteHostProps {
  /**
   * PR-6.6: whether the palette is open. Controlled by bootstrap so `⌘K`
   * (single-sourced through `useShellShortcuts`, FR-6.14) and the shell topbar
   * trigger both route through one state.
   */
  readonly open: boolean;
  /** PR-6.6: request a new open state (shell trigger, close, connect-tool). */
  readonly onOpenChange: (open: boolean) => void;
  /** Navigate the shell rail to a destination slug (host translates). */
  readonly onNavigateDestination: (slug: ShellDestinationSlug) => void;
  /** Open Settings, optionally focused on a section (`undefined` → default). */
  readonly onOpenSettings: (section?: SettingsSectionSlug) => void;
  /** Launchers for the four `action` hits. */
  readonly actions: PaletteHostActionHandlers;
}

export function PaletteHost({
  open,
  onOpenChange,
  onNavigateDestination,
  onOpenSettings,
  actions,
}: PaletteHostProps): ReactElement {
  const searchPort = useMemo(() => createDesktopPaletteSearchPort(), []);

  const handleClose = useCallback(() => onOpenChange(false), [onOpenChange]);

  const handleNavigate = useCallback(
    (route: string): void => {
      if (isSettingsRoute(route)) {
        // `settings` (→ undefined → default section) or `settings/<section>`.
        onOpenSettings(settingsSectionFromRoute(route));
        return;
      }
      // Destination hits carry a bare `ShellDestinationSlug` route (derived from
      // `destinationsForProfile` in palette-commands), so this cast is safe.
      onNavigateDestination(route as ShellDestinationSlug);
    },
    [onNavigateDestination, onOpenSettings],
  );

  const handleRunAction = useCallback(
    (token: string): void => {
      switch (token) {
        case "new-chat":
          actions.onNewChat();
          break;
        case "add-provider-key":
          actions.onAddProviderKey();
          break;
        case "download-local-model":
          actions.onDownloadLocalModel();
          break;
        case "connect-tool":
          actions.onConnectTool();
          break;
        default:
          // Unknown token — no-op. The static registry is the SSOT for the token
          // set; a stray token is not a fake success, just nothing to launch.
          break;
      }
    },
    [actions],
  );

  // The palette's "No results → Connect a tool →" hint reuses the connect-tool
  // flow; unlike an action hit it does not auto-close, so close it here.
  const handleConnectToolHint = useCallback(() => {
    actions.onConnectTool();
    onOpenChange(false);
  }, [actions, onOpenChange]);

  return (
    <div
      data-testid="desktop-palette-host"
      data-palette-open={open ? "true" : "false"}
    >
      <CommandPalette
        open={open}
        onRequestClose={handleClose}
        searchPort={searchPort}
        starterActions={PALETTE_COMMANDS}
        onNavigate={handleNavigate}
        onRunAction={handleRunAction}
        onConnectToolHint={handleConnectToolHint}
      />
    </div>
  );
}
