import { createContext, useContext, type ReactNode } from "react";

import type { Router } from "../routing/router";

// Substrate-agnostic access to the configured Router. Type parameter is
// the host's route union (web instantiates Router<AppRoute>, desktop
// instantiates Router<ArtifactRoute>). Components inside chat-surface
// that only need to navigate within ArtifactRoute can call
// `useRouter<ArtifactRoute>()` regardless of substrate — the host's wider
// union covers it.
//
// The Router instance flows through React context as an opaque value
// (Router<unknown>) and is re-typed at the hook boundary; this is the
// usual workaround for the React context API not supporting generic
// providers. Misuse is caught at the hook call site, not at runtime.

const RouterContext = createContext<Router<unknown> | null>(null);
RouterContext.displayName = "RouterContext";

export function RouterProvider<TRoute>({
  router,
  children,
}: {
  router: Router<TRoute>;
  children: ReactNode;
}): ReactNode {
  return (
    <RouterContext.Provider value={router as Router<unknown>}>
      {children}
    </RouterContext.Provider>
  );
}

export function useRouter<TRoute>(): Router<TRoute> {
  const value = useContext(RouterContext);
  if (value === null) {
    throw new Error(
      "useRouter: RouterProvider missing in the tree above this component",
    );
  }
  return value as Router<TRoute>;
}
