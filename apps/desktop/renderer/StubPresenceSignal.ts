import type {
  PresenceSignal,
  PresenceState,
} from "@enterprise-search/chat-surface";

// Phase 1-A placeholder. Desktop's real presence signal will plumb
// through Electron's BrowserWindow focus/blur events via IPC — the
// renderer tab in a single-window app is always visible to Chromium, so
// DocumentPresenceSignal (web's impl) would always report "visible" and
// hide the real signal we want (window focus). Phase 5 owns the real
// implementation.
export class StubPresenceSignal implements PresenceSignal {
  current(): PresenceState {
    return "visible";
  }

  subscribe(_handler: (state: PresenceState) => void): () => void {
    return () => {
      /* no-op */
    };
  }
}
