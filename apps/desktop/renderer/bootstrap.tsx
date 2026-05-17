import { useMemo } from "react";
import { createRoot } from "react-dom/client";

import { ChatShell } from "@enterprise-search/chat-surface";
import { MockTransport } from "@enterprise-search/chat-transport";

import { DesktopPlaceholder } from "./DesktopPlaceholder";
import { MemoryKeyValueStore } from "./MemoryKeyValueStore";
import { StubPresenceSignal } from "./StubPresenceSignal";
import { StubRouter } from "./StubRouter";

import "../preload/window-bridge-types";

// No <StrictMode> wrapper — see PRD §S2 friction note 5 and the sub-PRD
// Open question 4. The spike-prep EmailRenderer's hasMounted ref
// interacts badly with StrictMode's effect double-invoke; Phase 4-a is
// the renderer-side cleanup. Re-enable here after that lands.
export function App(): React.ReactElement {
  const transport = useMemo(() => new MockTransport(), []);
  const router = useMemo(() => new StubRouter(), []);
  const keyValueStore = useMemo(() => new MemoryKeyValueStore(), []);
  const presenceSignal = useMemo(() => new StubPresenceSignal(), []);
  return (
    <ChatShell
      transport={transport}
      router={router}
      keyValueStore={keyValueStore}
      presenceSignal={presenceSignal}
    >
      <DesktopPlaceholder />
    </ChatShell>
  );
}

export function mountApp(container: HTMLElement): () => void {
  const root = createRoot(container);
  root.render(<App />);
  return () => {
    root.unmount();
  };
}

// Auto-mount when bundled as the renderer entrypoint (out/renderer/
// bootstrap.js loaded by index.html). Tests import { App, mountApp }
// directly; the auto-mount block runs against the live document only,
// so tests that construct their own container are unaffected because
// they never call this module's top level with a #root element present
// (the jsdom default document has no #root).
const autoMountTarget =
  typeof document === "undefined" ? null : document.getElementById("root");
if (autoMountTarget !== null) {
  mountApp(autoMountTarget);
}
