import { createContext, useContext, type ReactNode } from "react";

import type { SecretStorage } from "../storage/secret-storage";

// Substrate-agnostic access to the configured SecretStorage. Same
// pattern as KeyValueStoreProvider — host app constructs the concrete
// impl (web: WebSecretStorage; desktop: an extension-RPC-backed store
// that talks to the OS keychain via VS Code's SecretStorage API),
// descendants consume via the hook.
//
// Mirrors KeyValueStoreProvider's tolerant default: a no-op store rather
// than throwing on missing provider. Auth tests routinely render
// AuthProvider with `persistBearer={false}`, in which case AuthContext
// never reads or writes the store; forcing every such test to wrap with
// SecretStorageProvider adds friction without surfacing real bugs
// (production always wires the real store through App.tsx). Strict
// Transport / Router hooks stay strict because silent failures there
// would be much harder to diagnose; for storage, "no persistence" is a
// coherent state.

const NO_PERSISTENCE_SECRETS: SecretStorage = {
  get: () => null,
  set: () => {
    /* no-op */
  },
  keys: () => [],
};

const SecretStorageContext = createContext<SecretStorage>(
  NO_PERSISTENCE_SECRETS,
);
SecretStorageContext.displayName = "SecretStorageContext";

export function SecretStorageProvider({
  store,
  children,
}: {
  store: SecretStorage;
  children: ReactNode;
}): ReactNode {
  return (
    <SecretStorageContext.Provider value={store}>
      {children}
    </SecretStorageContext.Provider>
  );
}

export function useSecretStorage(): SecretStorage {
  return useContext(SecretStorageContext);
}
