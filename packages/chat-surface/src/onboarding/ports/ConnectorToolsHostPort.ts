// ConnectorToolsHostPort — how a HOST performs a 1-click connect.
//
// `FirstRunConnectorsPort` (next door) is the DATA surface: list, install,
// delete. This port is the one genuinely substrate-specific verb on top of it —
// actually getting the user through the provider's consent screen — and the two
// are separate because they have different implementers. Every host binds the
// data port the same way (HTTP through its Transport); no two hosts perform a
// connect alike:
//
//   • desktop — main brokers the flow: it binds a loopback, opens the SYSTEM
//     browser, and the renderer is denied `window.open` entirely.
//   • web     — a full-page redirect (`location.href = auth_url`); the document
//     navigates away and the app remounts on return.
//
// The contract that matters is WHEN `connect` resolves.

import type { FirstRunInstallableConnector } from "../projectFirstRunConnectors";

/** What a completed connect tells the surface. */
export interface ConnectorConnectOutcome {
  /**
   * The server row that is now connected, when the host knows it. Optional
   * because the two topologies learn it at different moments — desktop's
   * install mints the id up front, while a host that only redirects never sees
   * one. The surface falls back to the `seed:<slug>` catalog convention.
   */
  readonly serverId?: string;
}

export interface ConnectorToolsHostPort {
  /**
   * Run the connect for one catalog entry.
   *
   * MUST resolve only once the round-trip has COMPLETED — not when the browser
   * was handed the URL. This is the entire contract: the surface refetches its
   * connector list off this promise, so resolving early means the popover
   * re-reads a world where the connector does not exist yet and goes on showing
   * "Connect" until the app restarts. That was a real shipped bug.
   *
   * A host that genuinely cannot observe completion (web, whose redirect
   * unloads the document) should still return its promise; the remount is what
   * refreshes it there, and resolving early is harmless because nothing is left
   * mounted to mislead.
   */
  connect(
    entry: FirstRunInstallableConnector,
  ): Promise<ConnectorConnectOutcome | void>;

  /**
   * Abort the connect currently in flight, if this host can.
   *
   * OPTIONAL on purpose — the capability is expressed in the type rather than
   * assumed, and the surface renders a Cancel affordance only when a host
   * supplies one. Web omits it: its connect is a full-page redirect, so by the
   * time there is anything to cancel the document is gone.
   *
   * Must actually stop the flow, not just tidy the UI. A renderer-only cancel
   * leaves the provider's tab live, so a user who cancels and then approves
   * anyway ends up connected — worse than no button at all, because they were
   * told it stopped.
   *
   * Resolving does not guarantee the connector is disconnected: an
   * authorization the provider already completed cannot be un-granted from
   * here. The surface re-reads its list afterwards and lets the server win.
   */
  cancel?(): Promise<void>;
}
