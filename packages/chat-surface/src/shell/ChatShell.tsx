import type { Transport } from "@enterprise-search/chat-transport";
import type { ReactNode } from "react";

import { RouterProvider } from "../providers/RouterProvider";
import { TransportProvider } from "../providers/TransportProvider";
import type { Router } from "../routing/router";

// The mount point for the chat surface. Wraps descendants in the two
// substrate-injection providers so any chat-surface component can call
// `useTransport()` / `useRouter()` without knowing which substrate is
// live underneath.
//
// In Phase 0 the host app (apps/frontend) renders ChatShell at the root
// and continues to use its existing component tree as children. Components
// migrate into chat-surface bottom-up in subsequent PRs; each migration
// switches the component from singleton access (`getAppTransport()`) to
// hook access (`useTransport()`) and the providers wired here are the
// substitution point.
export function ChatShell<TRoute>({
  transport,
  router,
  children,
}: {
  transport: Transport;
  router: Router<TRoute>;
  children: ReactNode;
}): ReactNode {
  return (
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>{children}</RouterProvider>
    </TransportProvider>
  );
}
