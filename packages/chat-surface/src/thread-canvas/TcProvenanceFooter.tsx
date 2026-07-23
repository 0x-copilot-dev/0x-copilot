// Provenance footer (Generative Surfaces v2, PRD-B2 D3 / FR-A5).
//
// A one-line accountability bar pinned to the bottom edge of every v2 surface:
// producing op (`connector.op`), latency, access class, stable ledger id, and a
// deep link into the native app. Every field is a plain projection of the
// `SurfaceProvenance` the pure selector produced — no port/context/clock reads.
// Kit-only styling (design-system recipes + tokens); the deep link is plain
// anchor markup (substrate-legal, no bare `window`).

import type { CSSProperties, ReactElement } from "react";

import { Badge } from "@0x-copilot/design-system";

import { humanizeConnector } from "../citations/connectorLabel";
import {
  formatAccessClass,
  formatLatency,
  type SurfaceProvenance,
} from "./provenance";

export interface TcProvenanceFooterProps {
  readonly provenance: SurfaceProvenance;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
  padding: "6px 12px",
  borderTop: "1px solid var(--color-border-subtle)",
  background: "var(--color-surface)",
  minHeight: 28,
};

const opStyle: CSSProperties = {
  color: "var(--color-text)",
};

const spacerStyle: CSSProperties = { flex: "1 1 auto" };

const linkStyle: CSSProperties = {
  color: "var(--color-accent)",
  textDecoration: "none",
  whiteSpace: "nowrap",
};

export function TcProvenanceFooter({
  provenance,
}: TcProvenanceFooterProps): ReactElement {
  const { connector, op, latencyMs, accessClass, ledgerId, openIn } =
    provenance;
  const opLabel =
    connector !== "" && op !== "" ? `${connector}.${op}` : connector || op;
  const linkLabel =
    openIn !== null
      ? (openIn.label ?? `Open in ${humanizeConnector(connector)}`)
      : null;

  return (
    <div style={rootStyle} data-testid="tc-provenance-footer">
      {opLabel !== "" ? (
        <span
          className="ui-mono-caps ui-mono-caps--9"
          style={opStyle}
          data-testid="tc-provenance-op"
        >
          {opLabel}
        </span>
      ) : null}
      {latencyMs !== null ? (
        <span className="ui-caption" data-testid="tc-provenance-latency">
          {formatLatency(latencyMs)}
        </span>
      ) : null}
      <Badge
        tone={accessClass === "read" ? "neutral" : "warning"}
        data-testid="tc-provenance-access"
      >
        {formatAccessClass(accessClass)}
      </Badge>
      <span className="ui-mono-caps" data-testid="tc-provenance-ledger-id">
        {ledgerId}
      </span>
      <span style={spacerStyle} aria-hidden="true" />
      {openIn !== null ? (
        <a
          href={openIn.url}
          target="_blank"
          rel="noreferrer noopener"
          style={linkStyle}
          className="ui-caption"
          data-testid="tc-provenance-open-in"
        >
          {linkLabel} ↗
        </a>
      ) : null}
    </div>
  );
}
