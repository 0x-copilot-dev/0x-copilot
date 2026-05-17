import type { Transport } from "@enterprise-search/chat-transport";
import type { ReactNode } from "react";

import type { PresenceSignal } from "../presence/presence-signal";
import { KeyValueStoreProvider } from "../providers/KeyValueStoreProvider";
import { PresenceSignalProvider } from "../providers/PresenceSignalProvider";
import { RouterProvider } from "../providers/RouterProvider";
import { TransportProvider } from "../providers/TransportProvider";
import type { Router } from "../routing/router";
import type { KeyValueStore } from "../storage/key-value-store";

// The mount point for the chat surface. Wraps descendants in the four
// substrate-injection providers so any chat-surface component can call
// `useTransport()` / `useRouter()` / `useKeyValueStore()` / `usePresenceSignal()`
// without knowing which substrate is live underneath.
//
// In Phase 0 the host app (apps/frontend) renders ChatShell at the root
// and continues to use its existing component tree as children. Components
// migrate into chat-surface bottom-up in subsequent PRs; each migration
// switches the component from singleton/window access (`getAppTransport()`,
// `window.localStorage`, `document.visibilityState`) to hook access, and
// the providers wired here are the substitution point.
export function ChatShell<TRoute>({
  transport,
  router,
  keyValueStore,
  presenceSignal,
  children,
}: {
  transport: Transport;
  router: Router<TRoute>;
  keyValueStore: KeyValueStore;
  presenceSignal: PresenceSignal;
  children: ReactNode;
}): ReactNode {
  return (
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <KeyValueStoreProvider store={keyValueStore}>
          <PresenceSignalProvider signal={presenceSignal}>
            {children}
          </PresenceSignalProvider>
        </KeyValueStoreProvider>
      </RouterProvider>
    </TransportProvider>
  );
}
