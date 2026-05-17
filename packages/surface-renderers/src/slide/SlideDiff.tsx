import type { CSSProperties, ReactElement } from "react";

import {
  TcInlineDiff,
  type SaaSRendererAdapter,
} from "@enterprise-search/chat-surface";

import { SlideRenderer, type Slide } from "./SlideRenderer";

export interface SlideDiffPayload {
  readonly diffId: string;
  readonly before: Slide;
  readonly after: Slide;
  readonly summary?: string;
  readonly provenance?: string;
}

export interface SlideDiffProps {
  readonly diff: SlideDiffPayload;
}

const PALETTE = {
  surfaceMute: "#1f2226",
  border: "#2a2d31",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
  beforeAccent: "#9aa0a6",
  afterAccent: "#c2ff5a",
} as const;

export function SlideDiff(props: SlideDiffProps): ReactElement {
  const { diff } = props;
  return (
    <div
      style={containerStyle}
      data-testid="slide-diff"
      data-diff-id={diff.diffId}
      aria-label={`Slide diff ${diff.diffId}`}
    >
      {diff.summary ? (
        <div data-testid="slide-diff-annotation">
          <TcInlineDiff
            state="idle"
            provenance={diff.provenance}
            title={diff.summary}
          />
        </div>
      ) : null}
      <div style={regionRowStyle}>
        <figure
          style={beforeRegionStyle}
          data-testid="slide-diff-before"
          data-region="before"
        >
          <figcaption
            style={labelStyle(PALETTE.beforeAccent)}
            data-testid="slide-diff-before-label"
          >
            Before
          </figcaption>
          <SlideRenderer slide={diff.before} />
        </figure>
        <figure
          style={afterRegionStyle}
          data-testid="slide-diff-after"
          data-region="after"
        >
          <figcaption
            style={labelStyle(PALETTE.afterAccent)}
            data-testid="slide-diff-after-label"
          >
            After
          </figcaption>
          <SlideRenderer slide={diff.after} />
        </figure>
      </div>
    </div>
  );
}

export const slideAdapter: SaaSRendererAdapter<Slide, SlideDiffPayload> = {
  scheme: "slide",
  matches: (uri: string): boolean =>
    typeof uri === "string" && uri.startsWith("slide://"),
  renderCurrent: (state: Slide): ReactElement => (
    <SlideRenderer slide={state} />
  ),
  renderDiff: (diff: SlideDiffPayload): ReactElement => (
    <SlideDiff diff={diff} />
  ),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  color: PALETTE.textHi,
};

const regionRowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 16,
  alignItems: "start",
};

const beforeRegionStyle: CSSProperties = {
  margin: 0,
  display: "flex",
  flexDirection: "column",
  gap: 6,
  opacity: 0.6,
  filter: "saturate(0.85)",
};

const afterRegionStyle: CSSProperties = {
  margin: 0,
  display: "flex",
  flexDirection: "column",
  gap: 6,
  opacity: 1,
};

const labelStyle = (accent: string): CSSProperties => ({
  alignSelf: "flex-start",
  display: "inline-block",
  padding: "3px 9px",
  borderRadius: 999,
  background: PALETTE.surfaceMute,
  border: `1px solid ${PALETTE.border}`,
  color: accent,
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: 0.7,
  textTransform: "uppercase",
});
