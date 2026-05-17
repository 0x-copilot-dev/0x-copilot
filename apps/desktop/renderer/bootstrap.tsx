import { StrictMode, useMemo, type ReactElement } from "react";
import { createRoot } from "react-dom/client";

import {
  ChatShell,
  DocumentPresenceSignal,
  HashRouter,
  LocalStorageKeyValueStore,
  registerGenericStructuredDiff,
} from "@enterprise-search/chat-surface";
import {
  IpcTransport,
  type RendererSession,
} from "@enterprise-search/chat-transport";
import { registerAll as registerSurfaceRenderers } from "@enterprise-search/surface-renderers";

import { DesktopPlaceholder } from "./DesktopPlaceholder";
import { DEFAULT_WORKSPACE_ID, SignInGate } from "./SignInGate";

import "../preload/window-bridge-types";

registerGenericStructuredDiff();
registerSurfaceRenderers();

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
  return (
    <ChatShell
      transport={transport}
      router={props.router}
      keyValueStore={props.keyValueStore}
      presenceSignal={props.presenceSignal}
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
