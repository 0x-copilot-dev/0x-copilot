import { createContext, useContext, type ReactNode } from "react";

import type { KeyValueStore } from "../storage/key-value-store";

// Substrate-agnostic access to the configured KeyValueStore. Same
// pattern as TransportProvider / RouterProvider — host app constructs
// the concrete impl (web: LocalStorageKeyValueStore; desktop: an
// extension-RPC-backed store), descendants consume via the hook.
//
// Unlike TransportProvider / RouterProvider, this context defaults to a
// no-op store rather than throwing on missing provider. The KV store is
// non-essential for unit-test rendering — components that read pinned
// conversations or similar prefs work correctly with an empty store —
// and forcing every test to wire a provider adds friction without
// surfacing real bugs (production always wires the real store through
// ChatShell). Transport / Router stay strict because silently 404'ing
// every request or losing every navigation would mask real production
// issues.

const NO_PERSISTENCE_STORE: KeyValueStore = {
  get: () => null,
  set: () => {
    /* no-op */
  },
  keys: () => [],
};

const KeyValueStoreContext = createContext<KeyValueStore>(NO_PERSISTENCE_STORE);
KeyValueStoreContext.displayName = "KeyValueStoreContext";

export function KeyValueStoreProvider({
  store,
  children,
}: {
  store: KeyValueStore;
  children: ReactNode;
}): ReactNode {
  return (
    <KeyValueStoreContext.Provider value={store}>
      {children}
    </KeyValueStoreContext.Provider>
  );
}

export function useKeyValueStore(): KeyValueStore {
  return useContext(KeyValueStoreContext);
}
