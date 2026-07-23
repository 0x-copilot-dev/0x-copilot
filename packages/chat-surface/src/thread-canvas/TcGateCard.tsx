// Tool-access gate card (Generative Surfaces v2, PRD-C2 / FR-B3). 🎨
//
// The canvas card a run parks on when a connector's auth is not usable right
// now. Pure presentational: it renders directly from a `LedgerGate` (folded from
// the `gate.opened` ledger event) and calls host callbacks — it never reads a
// port, clock, or browser primitive, and never posts a `/decision` itself.
// Kit-only styling (design-system recipes + tokens); no raw font-size /
// letter-spacing.
//
// FR-B3 contents: connector name + host + auth method (the connector line);
// purpose in task terms; scopes as plain chips; the read-only pledge ONLY when
// `opClass === "read"`; the write-policy radio (default `ask_first`) ONLY when
// `opClass !== "read"`; a provenance footer with the `r<short>·<seq>` ledger id;
// and the parked copy. Connect / Skip fire the host's `McpAuthPort` verbs.

import type { CSSProperties, ReactElement } from "react";

import { Badge } from "@0x-copilot/design-system";

import type { LedgerGate, LedgerGateWritePolicy } from "./ledgerProjection";

export interface TcGateCardProps {
  readonly gate: LedgerGate;
  /** Begin OAuth for the parked connector (host `McpAuthPort.beginAuth`). */
  readonly onConnect: (serverId: string) => void;
  /** Skip / dismiss the gate without connecting (host `McpAuthPort.skipAuth`). */
  readonly onSkip: (serverId: string) => void;
  /** The reviewer's write-policy choice changed (non-read gates only). */
  readonly onPolicyChange: (policy: LedgerGateWritePolicy) => void;
  /** Currently-selected write policy (defaults to `ask_first`). */
  readonly writePolicy: LedgerGateWritePolicy;
  /** Disables the actions while a connect/skip is in flight. */
  readonly busy?: boolean;
}

const PARKED_COPY =
  "The run is parked here until you connect — nothing runs without it.";

const AUTH_STATE_LABEL: Record<string, string> = {
  missing: "Not connected",
  expired: "Connection expired",
  insufficient: "More access needed",
};

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  padding: "var(--space-md)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
};

const scopeRowStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-xs)",
  flexWrap: "wrap",
};

const actionsRowStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm)",
  alignItems: "center",
};

const spacerStyle: CSSProperties = { flex: "1 1 auto" };

const policyRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-xs)",
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  borderTop: "1px solid var(--color-border-subtle)",
  paddingTop: "6px",
};

export function TcGateCard({
  gate,
  onConnect,
  onSkip,
  onPolicyChange,
  writePolicy,
  busy = false,
}: TcGateCardProps): ReactElement {
  const isRead = gate.opClass === "read";
  const authLabel = AUTH_STATE_LABEL[gate.authState] ?? "Not connected";

  return (
    <div className="ui-card" style={rootStyle} data-testid="tc-gate-card">
      <div style={headerStyle}>
        <span className="ui-section-label" data-testid="tc-gate-connector">
          {gate.connector || "Connector"}
        </span>
        <Badge
          tone={gate.authState === "expired" ? "warning" : "neutral"}
          data-testid="tc-gate-auth-state"
        >
          {authLabel}
        </Badge>
      </div>

      {gate.purpose !== "" ? (
        <p className="ui-body" data-testid="tc-gate-purpose">
          {gate.purpose}
        </p>
      ) : null}

      {gate.scopes.length > 0 ? (
        <div style={scopeRowStyle} data-testid="tc-gate-scopes">
          {gate.scopes.map((scope) => (
            <span key={scope} className="ui-pill">
              {scope}
            </span>
          ))}
        </div>
      ) : null}

      {isRead ? (
        <p className="ui-caption" data-testid="tc-gate-readonly-pledge">
          Read-only — this connection only reads; it never writes on your
          behalf.
        </p>
      ) : (
        <div
          style={policyRowStyle}
          role="radiogroup"
          aria-label="Write policy"
          data-testid="tc-gate-policy"
        >
          <span className="ui-caption">When the agent wants to write:</span>
          <label className="ui-caption">
            <input
              type="radio"
              name={`gate-policy-${gate.gateId}`}
              value="ask_first"
              checked={writePolicy === "ask_first"}
              disabled={busy}
              onChange={() => onPolicyChange("ask_first")}
              data-testid="tc-gate-policy-ask"
            />{" "}
            Ask me first
          </label>
          <label className="ui-caption">
            <input
              type="radio"
              name={`gate-policy-${gate.gateId}`}
              value="allow_always"
              checked={writePolicy === "allow_always"}
              disabled={busy}
              onChange={() => onPolicyChange("allow_always")}
              data-testid="tc-gate-policy-allow"
            />{" "}
            Allow always
          </label>
        </div>
      )}

      <p className="ui-caption" data-testid="tc-gate-parked">
        {PARKED_COPY}
      </p>

      <div style={actionsRowStyle}>
        <button
          type="button"
          className="ui-button ui-button--primary"
          disabled={busy}
          onClick={() => onConnect(gate.serverId)}
          data-testid="tc-gate-connect"
        >
          Connect
        </button>
        <button
          type="button"
          className="ui-button"
          disabled={busy}
          onClick={() => onSkip(gate.serverId)}
          data-testid="tc-gate-skip"
        >
          Skip
        </button>
        <span style={spacerStyle} aria-hidden="true" />
      </div>

      <div style={footerStyle}>
        <span className="ui-mono-caps" data-testid="tc-gate-ledger-id">
          {gate.ledgerId}
        </span>
      </div>
    </div>
  );
}
