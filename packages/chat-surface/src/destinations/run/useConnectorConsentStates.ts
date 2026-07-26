// Who owns the connector consent card's four states.
//
// `ConnectorConsentCard` has always drawn `pending | connecting | connected |
// denied`, and `TcChat` hardcoded `pending`, with an honest note explaining
// why: the run stream can report that a gate opened, but connecting, connected
// and denied all happen *after* the host launches OAuth, in a popup or a system
// browser the stream cannot see. So three of four states were unreachable and a
// user who pressed Connect watched the card sit still.
//
// This is the missing owner, and it lives here rather than in each host for the
// same reason the rest of the cockpit does: a state machine duplicated per
// substrate is a state machine that drifts. The hook wraps the host's
// `McpAuthPort` and derives what it can from the calls passing through it —
// `beginAuth` means connecting, `skipAuth` means denied — so a host that
// already implements the port gets the transitions for free.
//
// The one thing it cannot infer is success: whether the vendor granted consent
// is known only to whatever handles the OAuth return (web: the
// `/mcp/oauth/callback` route; desktop: the IPC bridge). Hosts report that
// through `markConnected`. Until they do, a connector sits at `connecting`,
// which is the honest state — "we opened the consent screen and have not heard
// back" — rather than a `connected` we would be guessing at.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ConnectorConsentState } from "../../approvals/ConnectorConsentCard";
import type { McpAuthPort } from "./mcpAuthPort";

export type ConnectorConsentStates = Readonly<
  Record<string, ConnectorConsentState>
>;

export interface ConnectorConsentStateController {
  /** Per-`server_id` state; absent means `pending` (nothing has happened). */
  readonly states: ConnectorConsentStates;
  /**
   * The host port with `beginAuth` / `skipAuth` instrumented. Pass THIS to the
   * canvas rather than the original, or the card's actions bypass the machine
   * and the states never move.
   */
  readonly port: McpAuthPort | undefined;
  /**
   * The OAuth return succeeded for this server (host-observed). There is no
   * `markDenied` twin: a vendor-side failure lands the user back on a card they
   * never decided against, so `pending` is the truthful state there, and the
   * decision itself already flows through the wrapped `skipAuth`.
   */
  readonly markConnected: (serverId: string) => void;
  /**
   * Back to square one — the card's Cancel while connecting. Deliberately NOT
   * a port verb: `beginAuth` has already handed the user to the vendor (web
   * full-page-redirects, desktop opens the system browser) and the port has no
   * abort. Cancel means "I am not doing this now", so the honest effect is
   * local: stop claiming a consent screen is in flight and offer the choice
   * again.
   */
  readonly markPending: (serverId: string) => void;
}

export function useConnectorConsentStates(
  port: McpAuthPort | undefined,
  /**
   * A connector the HOST observed finish OAuth, or `null`. On web this is
   * unavoidable rather than merely convenient: `beginAuth` full-page-redirects,
   * so by the time consent is granted this component has been torn down and
   * remounted with an empty map. The host's callback route is the only thing
   * that survives the round-trip, and it hands the result back here.
   */
  connectedServerId?: string | null,
): ConnectorConsentStateController {
  const [states, setStates] = useState<ConnectorConsentStates>({});

  // Ref so the wrapped port keeps a stable identity across renders; a fresh
  // port each render would remount nothing but does churn every memo below it.
  const portRef = useRef(port);
  portRef.current = port;

  const set = useCallback((serverId: string, next: ConnectorConsentState) => {
    setStates((prev) =>
      prev[serverId] === next ? prev : { ...prev, [serverId]: next },
    );
  }, []);

  const markConnected = useCallback(
    (serverId: string) => set(serverId, "connected"),
    [set],
  );
  const markPending = useCallback(
    (serverId: string) => set(serverId, "pending"),
    [set],
  );

  // Present-vs-absent is the only thing the memo needs from `port`; the live
  // reference is read from the ref. Hoisting it to a boolean keeps the dep array
  // honest instead of suppressing the exhaustive-deps rule.
  const hasPort = port !== undefined;

  const wrapped = useMemo<McpAuthPort | undefined>(() => {
    if (!hasPort) return undefined;
    // Each verb is delegated explicitly rather than spread over the original.
    // `...port` copies own enumerable properties only, so a host that ever
    // passes a class instance (methods on the prototype) would hand the canvas
    // a port whose third verb is missing — a spread is a silent trap here.
    return {
      installFromCatalog(slug: string) {
        // Slug-keyed, not server-keyed: the server row does not exist yet, so
        // there is nothing to move. The host's own OAuth return calls
        // `markConnected` once it has minted an id.
        portRef.current?.installFromCatalog(slug);
      },
      beginAuth(serverId: string) {
        // Optimistic on purpose. The browser is about to leave for the vendor's
        // consent screen, and a card that only reacts on return would read as
        // a dead button for the whole round-trip.
        set(serverId, "connecting");
        portRef.current?.beginAuth(serverId);
      },
      skipAuth(serverId: string) {
        // Terminal, and deliberately reversible on the card: `denied` is the
        // state whose whole point is that the user can change their mind.
        set(serverId, "denied");
        portRef.current?.skipAuth(serverId);
      },
    };
    // A host passing a new object each render therefore does not rebuild this.
  }, [hasPort, set]);

  useEffect(() => {
    if (connectedServerId === undefined || connectedServerId === null) return;
    markConnected(connectedServerId);
    // Idempotent by construction — `set` no-ops when the value is unchanged, so
    // a host that holds the same completion across renders does not loop.
  }, [connectedServerId, markConnected]);

  return { states, port: wrapped, markConnected, markPending };
}
