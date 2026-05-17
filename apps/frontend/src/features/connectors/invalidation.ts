import { useEffect } from "react";

/**
 * Tiny pub/sub for workspace-level connector mutations.
 *
 * Why this exists: `useConnectors` (workspace-installed servers) and
 * `useConversationConnectors` (per-chat scope) are intentionally
 * separate hooks — each owns one endpoint. Before this module, a
 * workspace-level "disable connector X" had no way to invalidate the
 * per-chat popover that was still offering X's tools. The chat refetched
 * only on tab visibility change, so an in-tab admin action drifted.
 *
 * Anyone mutating workspace connectors calls
 * `notifyWorkspaceConnectorsChanged()` after a successful API call.
 * Anyone whose state depends on the workspace connector list
 * subscribes via `useWorkspaceConnectorsChanged(refresh)`.
 *
 * Module-level pub/sub is deliberate — the channel needs to span React
 * trees (workspace settings panel ↔ chat popover) without threading a
 * shared context through both. Cross-tab sync is out of scope.
 */

type Listener = () => void;

const listeners = new Set<Listener>();

export function notifyWorkspaceConnectorsChanged(): void {
  for (const listener of listeners) {
    try {
      listener();
    } catch {
      // Listener errors must not block other subscribers from firing.
    }
  }
}

export function useWorkspaceConnectorsChanged(listener: Listener): void {
  useEffect(() => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, [listener]);
}
