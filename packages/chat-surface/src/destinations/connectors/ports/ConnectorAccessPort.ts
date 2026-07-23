// ConnectorAccessPort — the host-injected per-connector access-mode writer
// (PRD-06 D4). The optimistic-apply / revert-on-failure / error-banner state
// machine that used to live twice (once per host) now lives ONCE inside
// `ConnectorsDestination`; the host supplies only this single I/O method.
//
// chat-surface stays substrate-clean: it never calls `fetch`/IPC/`window`
// directly. Each host implements this port over its own transport:
//   • web     → `connectorsApi.setConnectorAccessMode(identity, id, body)`
//   • desktop → `transport.request({ method: "PATCH",
//                 path: "/v1/connectors/{id}/access-mode", body })`
// Both return the reconciled server `Connector` row so the destination can
// settle its optimistic overlay against server truth.
//
// Follows the `FirstRunConnectorsPort` precedent (one narrow method, no
// identity — the facade derives org/user from the verified session).

import type {
  Connector,
  ConnectorAccessMode,
  ConnectorId,
} from "@0x-copilot/api-types";

export interface ConnectorAccessPort {
  /**
   * `PATCH /v1/connectors/{id}/access-mode` → the reconciled server row.
   * Rejects on failure so the destination reverts the optimistic overlay and
   * renders its error banner.
   */
  setAccessMode(id: ConnectorId, mode: ConnectorAccessMode): Promise<Connector>;
}
