// View-tier toggle + Regenerate (Generative Surfaces v2, PRD-B3 / FR-D2 / FR-A6).
//
// The persistent way between the honest generic view and the shaped view, plus
// the "Looks wrong? Regenerate" affordance — the cluster that sits beside B2's
// provenance footer in the surface chrome. Every control is a pure projection of
// the folded `LedgerSurfaceViewState` and fires an injected callback (Transport
// port in the host) — no `window`/`fetch`/clock reads.
//
//   * Generic ⇄ Shaped: fires `onSetViewPreference(surfaceId, keep)`. The Shaped
//     side is disabled until a shaped derivation exists (`shapedAvailable`).
//   * Regenerate: fires `onRegenerateView(surfaceId)`; disabled at the client
//     regen cap (server cap is authoritative).

import type { CSSProperties, ReactElement } from "react";

import { Button } from "@0x-copilot/design-system";

import type {
  LedgerSurfaceViewState,
  LedgerViewKeep,
} from "./ledgerProjection";

/** Client mirror of the server per-surface regenerate cap (server authoritative). */
export const MAX_REGEN_PER_SURFACE = 3;

export interface ViewTierToggleProps {
  readonly surfaceId: string;
  readonly viewState: LedgerSurfaceViewState;
  readonly onSetViewPreference: (
    surfaceId: string,
    keep: LedgerViewKeep,
  ) => void;
  readonly onRegenerateView: (surfaceId: string) => void;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "4px 12px",
};

const segStyle: CSSProperties = {
  display: "inline-flex",
  gap: "var(--space-2xs, 2px)",
};

const spacerStyle: CSSProperties = { flex: "1 1 auto" };

export function ViewTierToggle({
  surfaceId,
  viewState,
  onSetViewPreference,
  onRegenerateView,
}: ViewTierToggleProps): ReactElement {
  const { effectiveTier, shapedAvailable, regenCount } = viewState;
  const isGeneric = effectiveTier === "generic";
  const isShaped = effectiveTier === "shaped";
  const regenExhausted = regenCount >= MAX_REGEN_PER_SURFACE;

  return (
    <div style={rootStyle} data-testid="tc-view-tier-toggle">
      <div style={segStyle} role="group" aria-label="View tier">
        <Button
          variant={isGeneric ? "secondary" : "ghost"}
          size="sm"
          aria-pressed={isGeneric}
          onClick={() => onSetViewPreference(surfaceId, "generic")}
          data-testid="tc-view-tier-generic"
        >
          Generic
        </Button>
        <Button
          variant={isShaped ? "secondary" : "ghost"}
          size="sm"
          aria-pressed={isShaped}
          disabled={!shapedAvailable}
          onClick={() => onSetViewPreference(surfaceId, "shaped")}
          data-testid="tc-view-tier-shaped"
        >
          Shaped
        </Button>
      </div>
      <span style={spacerStyle} aria-hidden="true" />
      <Button
        variant="ghost"
        size="sm"
        disabled={regenExhausted}
        onClick={() => onRegenerateView(surfaceId)}
        data-testid="tc-view-regenerate"
      >
        {regenExhausted
          ? "Regenerate (limit reached)"
          : "Looks wrong? Regenerate"}
      </Button>
    </div>
  );
}
