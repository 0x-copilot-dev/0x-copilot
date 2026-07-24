// Suggest-a-shape button (Generative Surfaces v2, PRD-B4 / FR-D4).
//
// The user-invited escape hatch on a raw or generic fallback surface: "Suggest a
// shape for this tool →" runs an immediate, higher-effort shaping attempt (bigger
// budget than the automatic pass). It sits in the surface chrome beside B2's
// provenance footer + B3's tier toggle, and — like every v2 control — is a pure
// projection of the folded `shapeRequest` state that fires an injected callback
// (the host's Transport port POSTs to the shape-request endpoint); no
// `window`/`fetch`/clock reads.
//
//   * idle    → "Suggest a shape for this tool →" (ghost, enabled).
//   * requested → disabled, "Attempting a shape…" (B2's assembling idiom).
//   * no_fit  → the honest line (FR-D3, requirement-grade) + the button re-enabled.
//
// The parent (`ThreadCanvas`) renders this only when the surface tier is `raw` or
// `generic` and the v2 canvas flag is on — a shaped surface hides it.

import type { CSSProperties, ReactElement } from "react";

import { Button } from "@0x-copilot/design-system";

import type { LedgerShapeRequestState } from "./ledgerProjection";

/** FR-D3 honest, requirement-grade no-fit line (verbatim; the rest is draft
 *  microcopy). Nothing the fallback showed is hidden. */
export const SHAPE_NO_FIT_LINE =
  "No confident fit — keeping the raw/generic view. Nothing is hidden.";

const SHAPE_IDLE_LABEL = "Suggest a shape for this tool →";
const SHAPE_REQUESTED_LABEL = "Attempting a shape…";

export interface SuggestShapeButtonProps {
  readonly surfaceId: string;
  /** Folded per-surface state (PRD-B4): idle / requested / no_fit. */
  readonly shapeRequest: LedgerShapeRequestState;
  /** Fires the invited attempt; the host POSTs to the shape-request endpoint. */
  readonly onShapeRequest: (surfaceId: string) => void;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: "var(--space-2xs, 4px)",
  padding: "4px 12px",
};

const noFitStyle: CSSProperties = {
  color: "var(--text-tertiary, var(--text-secondary))",
  fontSize: "var(--font-size-xs, 12px)",
  lineHeight: 1.4,
};

export function SuggestShapeButton({
  surfaceId,
  shapeRequest,
  onShapeRequest,
}: SuggestShapeButtonProps): ReactElement {
  const isRequested = shapeRequest === "requested";
  const isNoFit = shapeRequest === "no_fit";

  return (
    <div style={rootStyle} data-testid="tc-suggest-shape">
      {isNoFit ? (
        <span style={noFitStyle} data-testid="tc-suggest-shape-no-fit">
          {SHAPE_NO_FIT_LINE}
        </span>
      ) : null}
      <Button
        variant="ghost"
        size="sm"
        disabled={isRequested}
        aria-busy={isRequested}
        onClick={() => onShapeRequest(surfaceId)}
        data-testid="tc-suggest-shape-button"
      >
        {isRequested ? SHAPE_REQUESTED_LABEL : SHAPE_IDLE_LABEL}
      </Button>
    </div>
  );
}
