// Web first-run gate — mirrors the desktop `FirstRunGate` (renderer/FirstRunGate.tsx)
// at the post–sign-in seam. A returning user (flag set for this identity) drops
// straight through to `children` (the workspace shell); a first-time user sees
// the onboarding surface (`renderFirstRun`) until they finish or skip.
//
// The gate is HOST-owned (like the desktop's) — only the onboarding *surface*
// (passed via `renderFirstRun`) is the shared chat-surface component. Unlike the
// desktop's async IPC read, the web `WebFirstRunStore` is synchronous, so there
// is no loading phase: the decision is made on the first paint with no flash.

import {
  useCallback,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import type { WebFirstRunStore } from "./firstRunStore";

export interface FirstRunGateProps {
  /** Per-identity completion store (localStorage-backed, namespaced by org+user). */
  readonly store: WebFirstRunStore;
  /**
   * The onboarding surface. Receives `onComplete` — call it when the user
   * finishes setup, sends their first run, or skips. The gate persists the
   * per-identity flag and swaps to `children` (the workspace shell).
   */
  readonly renderFirstRun: (onComplete: () => void) => ReactNode;
  /** The signed-in workspace shell, mounted once onboarding is complete. */
  readonly children: ReactNode;
}

/**
 * Gates the workspace shell behind first-run onboarding, mirroring the desktop
 * gate. Sits between the authenticated boundary and the shell in `App.tsx`.
 */
export function FirstRunGate({
  store,
  renderFirstRun,
  children,
}: FirstRunGateProps): ReactElement {
  // Synchronous read in the lazy initializer → the correct surface paints
  // first, with no onboarding flash for returning users.
  const [complete, setComplete] = useState<boolean>(() => store.isComplete());

  const onComplete = useCallback(() => {
    // Advance the UI immediately; persist is best-effort — a write failure only
    // means onboarding may show once more next visit (non-fatal). Skip and
    // finish both land here; the reason is not gating-relevant.
    setComplete(true);
    try {
      store.markComplete("sent");
    } catch {
      // KeyValueStore writes can throw (private-mode localStorage); swallow —
      // the UI already advanced, and re-showing onboarding is the safe failure.
    }
  }, [store]);

  return <>{complete ? children : renderFirstRun(onComplete)}</>;
}
