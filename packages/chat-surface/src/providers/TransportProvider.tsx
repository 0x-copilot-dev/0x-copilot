import type { Transport } from "@0x-copilot/chat-transport";
import { createContext, useContext, type ReactNode } from "react";

// Substrate-agnostic access to the configured Transport. The host app
// (web today, desktop tomorrow) constructs the concrete implementation
// and feeds it in via the provider; descendants use the hook.
//
// Why a React context and not a module singleton: a singleton couples
// the chat surface to a single substrate. Moving the chat surface into
// a webview where the Transport is an extension-RPC bridge instead of
// a fetch wrapper means swapping the value, not rewriting imports.

const TransportContext = createContext<Transport | null>(null);
TransportContext.displayName = "TransportContext";

export function TransportProvider({
  transport,
  children,
}: {
  transport: Transport;
  children: ReactNode;
}): ReactNode {
  return (
    <TransportContext.Provider value={transport}>
      {children}
    </TransportContext.Provider>
  );
}

/**
 * The non-throwing read: `null` when no provider sits above the caller.
 *
 * `useTransport` throws by design — a component that fetches and finds no
 * transport is a wiring bug, and failing loudly is right for it. But a HOST
 * binder that builds its own transport and mounts the provider itself is
 * legitimately outside that provider in its own body, and has a transport to
 * hand over explicitly. It needs to ask without being taken down for asking
 * (the desktop FTUE binder did exactly this and blanked the first-run screen).
 */
export function useOptionalTransport(): Transport | null {
  return useContext(TransportContext);
}

export function useTransport(): Transport {
  const value = useContext(TransportContext);
  if (value === null) {
    throw new Error(
      "useTransport: TransportProvider missing in the tree above this component",
    );
  }
  return value;
}
