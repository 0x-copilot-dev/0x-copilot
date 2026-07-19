import "@0x-copilot/design-system/styles.css";
import "./desktop.css";

import { StrictMode, useMemo, useState, type ReactElement } from "react";
import { createRoot } from "react-dom/client";

import {
  ChatShell,
  DeploymentProfileProvider,
  DocumentPresenceSignal,
  HashRouter,
  LocalStorageKeyValueStore,
  NotificationCenterProvider,
  ToastStack,
  defaultDestinationForProfile,
  destinationsForProfile,
  registerGenericStructuredDiff,
  useShellShortcuts,
  type DeploymentProfile,
  type SettingsSectionSlug,
  type ShellDestinationSlug,
  type ShellShortcutCallbacks,
} from "@0x-copilot/chat-surface";
import { IpcTransport, type RendererSession } from "@0x-copilot/chat-transport";
import { registerAll as registerSurfaceRenderers } from "@0x-copilot/surface-renderers";

import { BootGate } from "./BootProgress";
import { DestinationOutlet } from "./DestinationOutlet";
import { PaletteHost } from "./PaletteHost";
import { SettingsMount } from "./SettingsMount";
import { DEFAULT_WORKSPACE_ID, SignInGate } from "./SignInGate";
import { Tier2Bridge } from "./Tier2Bridge";

import "../preload/window-bridge-types";

registerGenericStructuredDiff();
registerSurfaceRenderers();

// Phase 6C tier-2 lifecycle: listen for install/uninstall/mark-broken
// pushes from main and forward live boundary errors back. The bridge is
// idempotent — re-mounts under StrictMode receive the same handlers.
let tier2BridgeAttached = false;
function attachTier2BridgeOnce(): void {
  if (tier2BridgeAttached) return;
  if (typeof window === "undefined") return;
  const win = window as unknown as { bridge?: unknown };
  if (!win.bridge) return;
  new Tier2Bridge({ bridge: window.bridge }).attach();
  tier2BridgeAttached = true;
}
attachTier2BridgeOnce();

const DESKTOP_CAPABILITIES = {
  substrate: "desktop-webview" as const,
  nativeSecretStorage: true,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

// The deployment profile the desktop build ships with. Desktop is always the
// solo single-user product; `team` only ever renders on a hosted/web
// deployment. `ENTERPRISE_DEPLOYMENT_PROFILE` lives on the main process
// (service-env.ts) as env for the spawned Python services and is NOT bridged to
// the renderer — so Phase 2 seeds the profile provider with this static default
// (PRD FR-2.24). The value flows through the `DeploymentProfile` port, so when a
// `team` desktop build eventually needs a real value a preload bridge can
// supply it here without touching chat-surface.
const DESKTOP_DEPLOYMENT_PROFILE: DeploymentProfile = "single_user_desktop";

export function App(): ReactElement {
  const router = useMemo(() => new HashRouter(), []);
  const keyValueStore = useMemo(() => new LocalStorageKeyValueStore(), []);
  const presenceSignal = useMemo(() => new DocumentPresenceSignal(), []);
  return (
    <NotificationCenterProvider>
      <DeploymentProfileProvider profile={DESKTOP_DEPLOYMENT_PROFILE}>
        <BootGate bridge={window.bridge}>
          <SignInGate bridge={window.bridge} workspaceId={DEFAULT_WORKSPACE_ID}>
            {(session) => (
              <ChatShellForSession
                session={session}
                router={router}
                keyValueStore={keyValueStore}
                presenceSignal={presenceSignal}
              />
            )}
          </SignInGate>
        </BootGate>
      </DeploymentProfileProvider>
      {/* One toast surface for the whole app; floats above full-bleed surfaces. */}
      <ToastStack />
    </NotificationCenterProvider>
  );
}

interface ChatShellForSessionProps {
  readonly session: RendererSession;
  readonly router: HashRouter;
  readonly keyValueStore: LocalStorageKeyValueStore;
  readonly presenceSignal: DocumentPresenceSignal;
}

function ChatShellForSession(props: ChatShellForSessionProps): ReactElement {
  const transport = useMemo(
    () =>
      new IpcTransport({
        bridge: window.bridge,
        bootstrapSession: { bearer: null },
        bootstrapCapabilities: DESKTOP_CAPABILITIES,
      }),
    // The Transport contract's session is bearer-shaped. The actual bearer
    // is attached in main on every outbound HTTP request (PRD §6.7 / D24).
    // The renderer holds an opaque "session for workspace X" handle only.
    [props.session.workspaceId],
  );
  // The shell never derives the destination itself — the host owns the
  // slug ↔ route mapping (see ChatShellProps). The web host (App.tsx)
  // maps rail clicks onto its route type; the desktop has no route type
  // yet, so the minimal correct wiring is controlled local state. The solo
  // profile lands on Run — the flagship cockpit is the front door, not an
  // archive list (PRD US-2.3 / FR-2.21).
  const [activeDestination, setActiveDestination] =
    useState<ShellDestinationSlug>(() =>
      defaultDestinationForProfile(DESKTOP_DEPLOYMENT_PROFILE),
    );
  // Settings is not a rail destination — it opens from the rail foot and owns
  // full height (ChatShell suppresses the topbar/context/right-rail while it's
  // active). Navigating to any destination closes it.
  const [settingsActive, setSettingsActive] = useState(false);
  // PR-6.4: the Settings section the surface is focused on. `null` = the profile
  // default. The ⌘K palette can deep-link a section (FR-6.6/6.8); the surface
  // stays mounted (no remount) and switches in place. `onSectionChange` reflects
  // the user's in-surface tab clicks back here.
  const [settingsSection, setSettingsSection] =
    useState<SettingsSectionSlug | null>(null);
  // PR-6.6: the ⌘K command palette open state is lifted here so ⌘K flows through
  // a SINGLE listener (bootstrap's `useShellShortcuts`, FR-6.14). PaletteHost is
  // now controlled (`open`/`onOpenChange`) and no longer mounts its own
  // `useCommandPaletteHotkey` — exactly one ⌘K listener remains.
  const [paletteOpen, setPaletteOpen] = useState(false);

  const destinations = useMemo(
    () => destinationsForProfile(DESKTOP_DEPLOYMENT_PROFILE),
    [],
  );

  const handleNavigate = (slug: ShellDestinationSlug): void => {
    setSettingsActive(false);
    setActiveDestination(slug);
  };

  // PR-6.4: open Settings, optionally focused on a section (undefined → default).
  const handleOpenSettings = (section?: SettingsSectionSlug): void => {
    setSettingsSection(section ?? null);
    setSettingsActive(true);
  };

  // PR-6.6: wire the DESIGN-SPEC §6 GLOBAL chords through the single SSOT hook
  // (`useShellShortcuts`). Only the five global intents are provided here; every
  // callback closes over React setState functions (all stable), so the options
  // object is memoized with no deps — the hook attaches its listener once.
  //
  // Run-scoped chords (⌘M switch-mode, ⌘←/⌘→ rewind/step, ⌘L jump-live,
  // ⌘. pause, ⌘↵ approve, ⌘⌫ reject) are DELIBERATELY omitted: the Run cockpit
  // owns them internally (useRunMode / TcMiniTimeline / TcSwimlanes / approvals),
  // each with its own keydown listener scoped to the live run. Providing them
  // here too would double-wire — two listeners firing per press. Left undefined,
  // the hook no-ops them at the shell level and the cockpit stays the single
  // owner (FR-6.13 is satisfied by the cockpit's own handlers, not by bootstrap).
  const shortcutCallbacks = useMemo<ShellShortcutCallbacks>(
    () => ({
      // ⌘N — start/open a new run. Honest interim: routes to the Run cockpit
      // (the front door for starting a run), matching PR-6.4's new-chat path.
      onNewRun: () => {
        setSettingsActive(false);
        setActiveDestination("run");
      },
      // ⌘K — toggle the palette. A single toggle per press proves single
      // sourcing; a duplicate listener would toggle twice (net no-op).
      onOpenPalette: () => setPaletteOpen((prev) => !prev),
      // ⌘, — open Settings at the profile-default section.
      onOpenSettings: () => {
        setSettingsSection(null);
        setSettingsActive(true);
      },
      // ⌘⇧M — open Settings focused on the local-models section (the model
      // picker lives there today).
      onOpenLocalModelPicker: () => {
        setSettingsSection("local-models");
        setSettingsActive(true);
      },
      // ⌘⇧F — search activity. Honest interim: navigate to the Activity
      // destination (its in-surface search lands with the real surface).
      onSearchActivity: () => {
        setSettingsActive(false);
        setActiveDestination("activity");
      },
    }),
    [],
  );
  useShellShortcuts(shortcutCallbacks);

  return (
    <>
      <ChatShell
        transport={transport}
        router={props.router}
        keyValueStore={props.keyValueStore}
        presenceSignal={props.presenceSignal}
        activeDestination={activeDestination}
        destinations={destinations}
        onNavigate={handleNavigate}
        // PR-6.4: rail-foot Settings opens at the default section.
        onOpenSettings={() => handleOpenSettings()}
        settingsActive={settingsActive}
      >
        {settingsActive ? (
          // Phase 5 (PR-5.9): the real Settings surface — the profile-gated nav
          // plus every section body wired through `renderSection`. The team
          // sections stay gated off on the solo desktop profile and the solo
          // footer shows (DESIGN-SPEC §4 / FR-5.3).
          <SettingsMount
            transport={transport}
            session={props.session}
            // PR-6.4: controlled section so the palette can deep-link Settings.
            activeSection={settingsSection}
            onSectionChange={setSettingsSection}
          />
        ) : (
          <DestinationOutlet
            destination={activeDestination}
            // Reopen / open-run / run-skill land on the Run cockpit (the
            // desktop has no per-conversation run binding yet — honest
            // interim, matching ⌘N / new-chat above).
            onOpenRun={() => handleNavigate("run")}
            // Activity's retention link + Tools' approval-policy note deep-link
            // into the real Settings sections (reachable today, PR-5.9 / 6.4).
            onOpenRetentionSettings={() => handleOpenSettings("privacy")}
            onOpenApprovalSettings={() => handleOpenSettings("model-behavior")}
          />
        )}
      </ChatShell>
      {/* PR-6.4: the global ⌘K palette + its topbar trigger. Mounted once at the
          shell root so ⌘K is global and the trigger overlays the topbar band.
          Dispatch: destination hits → the shell's `handleNavigate`; Settings
          hits → `handleOpenSettings(section)`; action hits → the flow launchers
          below. `add-provider-key` / `download-local-model` open the real
          Settings sections and `connect-tool` opens the Tools destination — all
          reachable today. `new-chat` routes to the Run cockpit (the front door
          for starting a run) as an honest interim; the dedicated new-run trigger
          (⌘N → onNewRun) is wired in PR-6.6 via `useShellShortcuts` above.
          PR-6.6: `open`/`onOpenChange` make the palette CONTROLLED so ⌘K is
          single-sourced through that hook (FR-6.14) — one ⌘K listener. */}
      <PaletteHost
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        activeDestination={activeDestination}
        settingsActive={settingsActive}
        onNavigateDestination={handleNavigate}
        onOpenSettings={handleOpenSettings}
        actions={{
          onNewChat: () => handleNavigate("run"),
          onAddProviderKey: () => handleOpenSettings("provider-keys"),
          onDownloadLocalModel: () => handleOpenSettings("local-models"),
          onConnectTool: () => handleNavigate("connectors"),
        }}
      />
    </>
  );
}

export function mountApp(container: HTMLElement): () => void {
  const root = createRoot(container);
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  return () => {
    root.unmount();
  };
}

const autoMountTarget =
  typeof document === "undefined" ? null : document.getElementById("root");
if (autoMountTarget !== null) {
  mountApp(autoMountTarget);
}
