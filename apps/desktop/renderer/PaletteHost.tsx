// PaletteHost — desktop-substrate adapter for the global ⌘K palette (PR-6.4).
//
// Mirrors the web host (`apps/frontend/src/features/palette/PaletteHost.tsx`)
// but injects the desktop's LOCAL static registry port instead of the facade
// HTTP port, and — unlike web — also renders the topbar `CommandPaletteTrigger`
// (the web surface owns its own topbar trigger elsewhere).
//
// Wires (FR-6.6 / FR-6.7):
//   * exactly ONE `<CommandPalette>` (the canonical chat-surface shell palette),
//     with `searchPort = createDesktopPaletteSearchPort()` and
//     `starterActions = PALETTE_COMMANDS` (PR-6.3);
//   * the `open` state (host-owned) + the interim `⌘K` opener via
//     `useCommandPaletteHotkey`. PR-6.6 later single-sources `⌘K` through
//     `useShellShortcuts` and drops this interim listener (FR-6.14);
//   * dispatch of the palette's non-entity hits back to the host:
//       - `navigation` → a Settings deep-link (`isSettingsRoute`) opens Settings
//         at that section, otherwise the shell navigates to the destination slug;
//       - `action` → the matching host flow launcher.
//
// Substrate note on the trigger: the `ChatShell` owns its own topbar and already
// renders a `CommandPaletteTrigger` there, but that internal trigger is a
// deferred no-op (the shell does not thread an `onOpenCommandPalette`). The shell
// lives in `@0x-copilot/chat-surface`, which is out of scope for this
// apps/desktop-only PR, so PaletteHost renders its OWN functional trigger,
// fixed to the DESIGN-SPEC §1 topbar slot (right-aligned, 250×28, centred in the
// 46px band). It is suppressed on Run and Settings — both full-bleed, where the
// shell renders no topbar (FR-6.7). On the placeholder destinations that DO show
// the shell topbar (Projects/Activity/Tools/Skills) this functional trigger
// sits exactly over the shell's dead one so the user still sees a single
// affordance; PR-6.6/6.7 reconcile the two (wire the shell trigger, or suppress
// it) once the shell can accept the opener.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import {
  CommandPalette,
  CommandPaletteTrigger,
  useCommandPaletteHotkey,
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
  /** The shell's active destination — drives topbar-trigger suppression. */
  readonly activeDestination: ShellDestinationSlug;
  /** Whether the Settings surface is open — suppresses the topbar trigger. */
  readonly settingsActive: boolean;
  /** Navigate the shell rail to a destination slug (host translates). */
  readonly onNavigateDestination: (slug: ShellDestinationSlug) => void;
  /** Open Settings, optionally focused on a section (`undefined` → default). */
  readonly onOpenSettings: (section?: SettingsSectionSlug) => void;
  /** Launchers for the four `action` hits. */
  readonly actions: PaletteHostActionHandlers;
}

// DESIGN-SPEC §1 topbar trigger geometry. The shared `CommandPaletteTrigger`
// declares only `min-width: 200` inline (never `width`), so a class rule setting
// `width` wins without a specificity fight — same trick the shell topbar uses.
const TRIGGER_CLASS = "cs-desktop-cmd-trigger";
const TRIGGER_WIDTH_CSS = `.${TRIGGER_CLASS}{width:250px;flex:none;}`;

// Fixed to the topbar band: `top` centres the 28px trigger in the 46px topbar
// ((46-28)/2 = 9), `right` matches the shell topbar's `0 16px` padding. Below
// the palette modal's z-index (1000) so the scrim covers it when open.
const triggerSlotStyle: CSSProperties = {
  position: "fixed",
  top: 9,
  right: 16,
  zIndex: 900,
};

export function PaletteHost({
  activeDestination,
  settingsActive,
  onNavigateDestination,
  onOpenSettings,
  actions,
}: PaletteHostProps): ReactElement {
  const [open, setOpen] = useState(false);
  const searchPort = useMemo(() => createDesktopPaletteSearchPort(), []);

  const handleOpen = useCallback(() => setOpen(true), []);
  const handleClose = useCallback(() => setOpen(false), []);

  // Interim ⌘K opener (PR-6.6 single-sources this through useShellShortcuts).
  useCommandPaletteHotkey({ onOpen: handleOpen });

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
    setOpen(false);
  }, [actions]);

  // FR-6.7 / DESIGN-SPEC §1: suppressed on Run and Settings (both full-bleed).
  const triggerSuppressed = settingsActive || activeDestination === "run";

  return (
    <div
      data-testid="desktop-palette-host"
      data-palette-open={open ? "true" : "false"}
    >
      {triggerSuppressed ? null : (
        <div style={triggerSlotStyle}>
          {/* Sizes the shared trigger to 250px via its className (DESIGN-SPEC §1)
              without touching the shared component. */}
          <style>{TRIGGER_WIDTH_CSS}</style>
          <CommandPaletteTrigger className={TRIGGER_CLASS} onOpen={handleOpen} />
        </div>
      )}
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
