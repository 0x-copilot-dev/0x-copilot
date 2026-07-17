// PortProvider — substrate-agnostic injection point for the four
// Phase 0.5 substrate ports (Badge, Notification, FilePicker, Clipboard).
//
// Source: cross-audit.md §5.4 — "Every port is provided by the host
// (frontend or desktop) via a React provider at the top of the app,
// mirroring the existing `TransportProvider` / `RouterProvider` shape."
//
// The chat-surface package exports the port interfaces but does not own
// the providers — that lets the host pick the impl (web no-op vs.
// desktop native) without chat-surface knowing about either substrate.
// Destinations consume via `usePort("badge")` etc.
//
// Single source of truth: there is exactly one `<PortProvider>` mounted
// at App.tsx. Tests that need a stubbed impl pass `value` overrides
// directly; production code never instantiates a second provider.

import { createContext, useContext, type ReactNode } from "react";

import type {
  BadgePort,
  ClipboardPort,
  FilePickerPort,
  NotificationPort,
} from "@0x-copilot/chat-surface";

export interface PortBundle {
  readonly badge: BadgePort;
  readonly notification: NotificationPort;
  readonly filePicker: FilePickerPort;
  readonly clipboard: ClipboardPort;
}

type PortName = keyof PortBundle;

const PortContext = createContext<PortBundle | null>(null);
PortContext.displayName = "PortContext";

export function PortProvider({
  ports,
  children,
}: {
  ports: PortBundle;
  children: ReactNode;
}): ReactNode {
  return <PortContext.Provider value={ports}>{children}</PortContext.Provider>;
}

/**
 * Returns the named port. Throws if no provider is mounted above — same
 * strict default as `useTransport` / `useRouter`. Tests that exercise
 * port-consuming components must wrap them in `<PortProvider ports={...} />`.
 */
export function usePort<K extends PortName>(name: K): PortBundle[K] {
  const value = useContext(PortContext);
  if (value === null) {
    throw new Error(
      `usePort(${String(name)}): PortProvider missing in the tree above this component`,
    );
  }
  return value[name];
}

/**
 * Returns the entire port bundle. Used by the few call sites that need
 * more than one port at a time (e.g. NotificationPort + Router glue).
 * Most consumers should prefer `usePort(name)` for single-port reads.
 */
export function usePorts(): PortBundle {
  const value = useContext(PortContext);
  if (value === null) {
    throw new Error(
      "usePorts: PortProvider missing in the tree above this component",
    );
  }
  return value;
}
