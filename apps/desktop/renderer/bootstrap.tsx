import { StrictMode, useMemo, useState, type ReactElement } from "react";
import { createRoot } from "react-dom/client";

import {
  ChatShell,
  DEFAULT_SHELL_DESTINATION,
  DocumentPresenceSignal,
  HashRouter,
  LocalStorageKeyValueStore,
  registerGenericStructuredDiff,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";
import { IpcTransport, type RendererSession } from "@0x-copilot/chat-transport";
import { registerAll as registerSurfaceRenderers } from "@0x-copilot/surface-renderers";

import { BootGate } from "./BootProgress";
import { DesktopPlaceholder } from "./DesktopPlaceholder";
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

export function App(): ReactElement {
  const router = useMemo(() => new HashRouter(), []);
  const keyValueStore = useMemo(() => new LocalStorageKeyValueStore(), []);
  const presenceSignal = useMemo(() => new DocumentPresenceSignal(), []);
  return (
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
  // yet, so the minimal correct wiring is controlled local state.
  const [activeDestination, setActiveDestination] =
    useState<ShellDestinationSlug>(DEFAULT_SHELL_DESTINATION);
  return (
    <ChatShell
      transport={transport}
      router={props.router}
      keyValueStore={props.keyValueStore}
      presenceSignal={props.presenceSignal}
      activeDestination={activeDestination}
      onNavigate={setActiveDestination}
    >
      <DesktopPlaceholder />
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
