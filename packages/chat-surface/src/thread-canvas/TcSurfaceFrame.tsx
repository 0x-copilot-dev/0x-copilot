// Surface frame (Generative Surfaces v2, PRD-B2 D4).
//
// Wraps B1's active-surface node with its accountability chrome: dispatch by the
// surface's view tier into the skeleton / raw fallback / rendered content, and
// pin the provenance footer at the bottom edge in every state. Mounted ONLY
// inside the v2 canvas subtree — the legacy (flag-off) path never renders it, so
// the cockpit stays byte-identical with the flag off.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import { RawFallbackView } from "../surfaces/raw/RawFallbackView";
import { TcProvenanceFooter } from "./TcProvenanceFooter";
import { TcSurfaceSkeleton } from "./TcSurfaceSkeleton";
import type { SurfaceSpecGeneration } from "./eventProjector";
import { resolveSurfaceOpenIn, type SurfaceProvenance } from "./provenance";

export interface TcSurfaceFrameProps {
  /** `null` ⇒ no provenance yet (no surface.created): render children bare. */
  readonly provenance: SurfaceProvenance | null;
  /** Surface payload — used for tier `"raw"` and for deep-link resolution. */
  readonly rawPayload?: unknown;
  /**
   * This surface's entry in `project().surfaceSpecGeneration` — present exactly
   * while the runtime says a spec-generation model call is in flight for it.
   * Omitted / `null` ⇒ no such signal, which is what a runtime that never emits
   * `surface_spec_requested` (and every session replayed from disk) supplies.
   */
  readonly specGeneration?: SurfaceSpecGeneration | null;
  readonly onCopyText?: (text: string) => Promise<void>;
  readonly onSaveFile?: (text: string, filename: string) => Promise<void>;
  readonly frameActionsSlot?: ReactNode; // reserved: B3 toggle, B4 entry point
  readonly children: ReactNode; // B1's rendered surface content
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: "1 1 auto",
  minHeight: 0,
};

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: "1 1 auto",
  minHeight: 0,
  overflow: "hidden",
};

const footerSlotStyle: CSSProperties = { flex: "0 0 auto" };

/** `r7f3·042` → `r7f3-042-raw.json` — the frame owns the download filename. */
function rawFilename(ledgerId: string): string {
  const safe = ledgerId.replaceAll("·", "-").replace(/[^A-Za-z0-9._-]/g, "-");
  return `${safe}-raw.json`;
}

export function TcSurfaceFrame({
  provenance,
  rawPayload,
  specGeneration = null,
  onCopyText,
  onSaveFile,
  frameActionsSlot,
  children,
}: TcSurfaceFrameProps): ReactElement {
  // Compat: no provenance ⇒ render B1's pane bare (no frame chrome).
  if (provenance === null) {
    return <>{children}</>;
  }

  // Deep link needs the hydrated payload, so resolve it here (not in the pure
  // event selector) before handing a fully-formed provenance to the footer.
  const resolved = resolveSurfaceOpenIn(provenance, rawPayload);

  // WHAT DRAWS THE SKELETON IS UNCHANGED: `tier === "pending"`, exactly as
  // before. `specGeneration` only decides what the skeleton SAYS.
  //
  // It is tempting to OR the two — "the runtime says a model call is in flight,
  // so draw the skeleton" — and that is wrong, because the signal is not a
  // matched pair. `_emit_requested` fires unconditionally at the top of
  // `_generate`, but only the SUCCESS exit emits `surface_spec_generated`; a
  // raise and a `GenFailure` (a normal outcome, not a crash) both return without
  // a terminal. Generation is also fire-and-forget (`asyncio.create_task`, never
  // awaited), so `surface_spec_requested` can even land after `run_completed`.
  //
  // Under an OR, every one of those leaves the entry set and the frame replaces
  // an ALREADY-RENDERED surface with a shimmer reading "Asking … to lay out this
  // table" for the rest of the run. A progress hint that can hide drawn content
  // is worse than no hint: the failure mode is indistinguishable from the
  // "chrome vanishing unbidden" bug this frame exists to avoid.
  //
  // Gating on `pending` makes unpaired presence harmless by construction — the
  // signal can only ever ADD a line to a skeleton that was already going to be
  // drawn, and a lost terminal costs nothing. The deliberate trade: a
  // regenerate over an already-rendered surface no longer shows a skeleton.
  // That feedback is worth having, but not at the price of a stuck one, and it
  // should come from an event that actually closes on every exit.
  const generating = specGeneration !== null && provenance.tier === "pending";
  let body: ReactNode;
  if (provenance.tier === "pending") {
    body = (
      <TcSurfaceSkeleton
        connector={provenance.connector}
        kind={provenance.kind}
        generation={specGeneration}
      />
    );
  } else if (provenance.tier === "raw") {
    body = (
      <RawFallbackView
        payload={rawPayload}
        filename={rawFilename(provenance.ledgerId)}
        onCopy={onCopyText}
        onDownload={onSaveFile}
      />
    );
  } else {
    // "generic" / "shaped" — render B1's surface node unchanged.
    body = children;
  }

  return (
    <div style={rootStyle} data-testid="tc-surface-frame">
      {frameActionsSlot}
      <div style={bodyStyle}>{body}</div>
      <div style={footerSlotStyle}>
        <TcProvenanceFooter provenance={resolved} />
      </div>
    </div>
  );
}
