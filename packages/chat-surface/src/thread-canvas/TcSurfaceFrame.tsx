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
import { resolveSurfaceOpenIn, type SurfaceProvenance } from "./provenance";

export interface TcSurfaceFrameProps {
  /** `null` ⇒ no provenance yet (no surface.created): render children bare. */
  readonly provenance: SurfaceProvenance | null;
  /** Surface payload — used for tier `"raw"` and for deep-link resolution. */
  readonly rawPayload?: unknown;
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

  let body: ReactNode;
  if (provenance.tier === "pending") {
    body = (
      <TcSurfaceSkeleton
        connector={provenance.connector}
        kind={provenance.kind}
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
