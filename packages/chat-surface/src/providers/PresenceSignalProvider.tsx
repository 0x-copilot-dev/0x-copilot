import { createContext, useContext, type ReactNode } from "react";

import type {
  PresenceSignal,
  PresenceState,
} from "../presence/presence-signal";

// Substrate-agnostic access to the configured PresenceSignal. Same
// pattern as KeyValueStoreProvider — host app constructs the concrete
// impl, descendants consume via the hook.
//
// Tolerant default (always "visible", no subscribers fire) mirrors the
// KeyValueStore decision: PresenceSignal is non-essential for unit-test
// rendering, and forcing every test to wire a provider adds friction
// without surfacing real bugs. Production always wires the real signal
// through ChatShell.

const ALWAYS_VISIBLE: PresenceSignal = {
  current(): PresenceState {
    return "visible";
  },
  subscribe(): () => void {
    return () => {
      /* no subscribers in the default — nothing to unsubscribe */
    };
  },
};

const PresenceSignalContext = createContext<PresenceSignal>(ALWAYS_VISIBLE);
PresenceSignalContext.displayName = "PresenceSignalContext";

export function PresenceSignalProvider({
  signal,
  children,
}: {
  signal: PresenceSignal;
  children: ReactNode;
}): ReactNode {
  return (
    <PresenceSignalContext.Provider value={signal}>
      {children}
    </PresenceSignalContext.Provider>
  );
}

export function usePresenceSignal(): PresenceSignal {
  return useContext(PresenceSignalContext);
}
