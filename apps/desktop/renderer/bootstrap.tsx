import "@0x-copilot/design-system/styles.css";

import { StrictMode, useMemo, useState, type ReactElement } from "react";
import { createRoot } from "react-dom/client";

import {
  ChatShell,
  DeploymentProfileProvider,
  DocumentPresenceSignal,
  HashRouter,
  LocalStorageKeyValueStore,
  SettingsSurface,
  defaultDestinationForProfile,
  destinationsForProfile,
  registerGenericStructuredDiff,
  type DeploymentProfile,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";
import { IpcTransport, type RendererSession } from "@0x-copilot/chat-transport";
import { registerAll as registerSurfaceRenderers } from "@0x-copilot/surface-renderers";

import { BootGate } from "./BootProgress";
import { DestinationOutlet } from "./DestinationOutlet";
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

  const destinations = useMemo(
    () => destinationsForProfile(DESKTOP_DEPLOYMENT_PROFILE),
    [],
  );

  const handleNavigate = (slug: ShellDestinationSlug): void => {
    setSettingsActive(false);
    setActiveDestination(slug);
  };

  return (
    <ChatShell
      transport={transport}
      router={props.router}
      keyValueStore={props.keyValueStore}
      presenceSignal={props.presenceSignal}
      activeDestination={activeDestination}
      destinations={destinations}
      onNavigate={handleNavigate}
      onOpenSettings={() => setSettingsActive(true)}
      settingsActive={settingsActive}
    >
      {settingsActive ? (
        // Phase 5 fills the section bodies via `renderSection`; until then the
        // merged SettingsSurface renders the profile-gated nav with honest
        // titled placeholders (PRD FR-2.22, R7 — a visible stub, not a no-op).
        <SettingsSurface />
      ) : (
        <DestinationOutlet destination={activeDestination} />
      )}
    </ChatShell>
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
