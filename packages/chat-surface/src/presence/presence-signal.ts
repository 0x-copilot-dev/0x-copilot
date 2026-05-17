// Substrate-agnostic "is the user actively looking at this view?" signal.
//
// Consumers use it to know when to revalidate stale state — pull-to-
// refresh-on-focus, throttle background activity while hidden, etc.
// Currently driven by tab visibility on web (`document.visibilityState`
// + `visibilitychange`); on desktop it'll be backed by the VS Code
// window/editor focus events (or a permanent "visible" no-op if the
// substrate has no equivalent).
//
// Why a port instead of consumers reading `document.visibilityState`
// directly: the desktop substrate has no `document`. Routing through a
// port keeps every consumer on one signal and lets each substrate map
// it to whatever attention primitive it actually has.

export type PresenceState = "visible" | "hidden";

export interface PresenceSignal {
  /** Current presence state. Synchronously available. */
  current(): PresenceState;

  /**
   * Subscribe to changes. Returns an unsubscribe function. Implementations
   * fire on actual transitions (visible → hidden or back) — subscribers
   * can rely on each callback meaning "something just changed", not "an
   * underlying event happened."
   */
  subscribe(handler: (state: PresenceState) => void): () => void;
}

/**
 * Web-substrate implementation backed by `document.visibilityState` and
 * the `visibilitychange` event. Wraps the substrate primitive with no
 * additional state — every read goes through `globalThis.document` so
 * jsdom test stubs and the real DOM both work without configuration.
 *
 * Uses `globalThis.document` (member access) rather than the bare
 * `document` global to make substrate access honest and to satisfy the
 * package's no-restricted-globals lint rule. Same convention as
 * LocalStorageKeyValueStore.
 */
export class DocumentPresenceSignal implements PresenceSignal {
  current(): PresenceState {
    const doc = globalThis.document;
    if (doc === undefined) {
      // SSR or pre-DOM substrate: optimistically treat as visible so
      // pull-on-focus revalidations don't get suppressed forever.
      return "visible";
    }
    return doc.visibilityState === "visible" ? "visible" : "hidden";
  }

  subscribe(handler: (state: PresenceState) => void): () => void {
    const doc = globalThis.document;
    if (doc === undefined) {
      return () => {
        /* no-op */
      };
    }
    const listener = (): void => {
      handler(this.current());
    };
    doc.addEventListener("visibilitychange", listener);
    return () => {
      doc.removeEventListener("visibilitychange", listener);
    };
  }
}
