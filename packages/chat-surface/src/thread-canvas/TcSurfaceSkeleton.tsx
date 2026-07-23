// Surface skeleton / "assembling" state (Generative Surfaces v2, PRD-B2 D4 /
// FR-A4 / NFR-1). Shown the moment `surface.created` lands, before any
// `view.derived` — shaping never delays it (FR-D1). Token-built shimmer bars;
// no host state, no timers.

import type { CSSProperties, ReactElement } from "react";

import { humanizeConnector } from "../citations/connectorLabel";

export interface TcSurfaceSkeletonProps {
  readonly connector: string;
  readonly kind: string;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  padding: "var(--space-md)",
  flex: "1 1 auto",
};

const lineStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  marginBottom: "var(--space-xs)",
};

function barStyle(widthPct: number): CSSProperties {
  return {
    height: 12,
    width: `${widthPct}%`,
    borderRadius: "var(--radius-sm)",
    background:
      "linear-gradient(90deg, var(--color-surface-2) 0%, var(--color-surface-raised) 50%, var(--color-surface-2) 100%)",
    opacity: 0.7,
  };
}

export function TcSurfaceSkeleton({
  connector,
  kind,
}: TcSurfaceSkeletonProps): ReactElement {
  const label = `${humanizeConnector(connector)} · assembling ${kind || "surface"} view…`;
  return (
    <div
      role="status"
      aria-live="polite"
      style={rootStyle}
      data-testid="tc-surface-skeleton"
    >
      <span className="ui-caption" style={lineStyle}>
        {label}
      </span>
      <div style={barStyle(90)} aria-hidden="true" />
      <div style={barStyle(72)} aria-hidden="true" />
      <div style={barStyle(58)} aria-hidden="true" />
    </div>
  );
}
