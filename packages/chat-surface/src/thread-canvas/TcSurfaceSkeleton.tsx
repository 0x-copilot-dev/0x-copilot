// Surface skeleton / "assembling" state (Generative Surfaces v2, PRD-B2 D4 /
// FR-A4 / NFR-1). Shown the moment `surface.created` lands, before any
// `view.derived` — shaping never delays it (FR-D1). Token-built shimmer bars;
// no host state, no timers.
//
// The shimmer alone was the complaint: it draws the SHAPE of a surface that is
// not there and says nothing about why, so a slow generation and a dead one look
// identical for as long as the reader is willing to wait. `generation` is the
// runtime's own `surface_spec_requested` signal, folded by `eventProjector`, and
// it is the only thing here that can name what is running. When it is absent —
// an older session replayed from disk, a runtime that never emits the event —
// this renders exactly the line it always did, so nothing depends on it arriving.

import type { CSSProperties, ReactElement } from "react";

import { humanizeConnector } from "../citations/connectorLabel";
import type { SurfaceSpecGeneration } from "./eventProjector";

export interface TcSurfaceSkeletonProps {
  readonly connector: string;
  readonly kind: string;
  /** In-flight spec generation for THIS surface; `null`/omitted ⇒ none known. */
  readonly generation?: SurfaceSpecGeneration | null;
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

// The model id is runtime text of unbounded length. It sits in a block-level
// caption rather than a control row, so wrapping — not truncation — is the right
// give: it keeps the id readable instead of hiding the half that identifies it.
const detailStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  marginBottom: "var(--space-xs)",
  overflowWrap: "anywhere",
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

/**
 * The second line, in the tone of the status strip: short, factual, and only
 * ever claiming what the event actually said. A generation with no `model_id`
 * still gets a sentence — "a layout is being chosen" is true and useful even
 * when the runtime declined to name who is choosing it.
 */
function generationDetail(
  generation: SurfaceSpecGeneration,
  subject: string,
): string {
  return generation.modelId === null || generation.modelId === ""
    ? `Choosing a layout for this ${subject}.`
    : `Asking ${generation.modelId} to lay out this ${subject}.`;
}

export function TcSurfaceSkeleton({
  connector,
  kind,
  generation = null,
}: TcSurfaceSkeletonProps): ReactElement {
  const subject = kind || "surface";
  const label = `${humanizeConnector(connector)} · assembling ${subject} view…`;
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
      {generation === null ? null : (
        <span
          className="ui-caption"
          style={detailStyle}
          data-testid="tc-surface-skeleton-detail"
        >
          {generationDetail(generation, subject)}
        </span>
      )}
      <div style={barStyle(90)} aria-hidden="true" />
      <div style={barStyle(72)} aria-hidden="true" />
      <div style={barStyle(58)} aria-hidden="true" />
    </div>
  );
}
