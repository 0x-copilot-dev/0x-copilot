import type { CSSProperties, ReactElement } from "react";

export interface SlideBullet {
  readonly text: string;
}

export interface Slide {
  readonly slideId: string;
  readonly deckId: string;
  readonly slideNumber: number;
  readonly title: string;
  readonly bullets: readonly SlideBullet[];
  readonly thumbnailUrl?: string;
}

export interface SlideRendererProps {
  readonly slide: Slide;
}

const PALETTE = {
  pageBg: "#101113",
  surface: "#181a1c",
  surfaceMute: "#1f2226",
  border: "#2a2d31",
  textHi: "#f4f5f6",
  textMid: "#c8ccd1",
  textLo: "#9aa0a6",
  lime: "#c2ff5a",
} as const;

export function SlideRenderer(props: SlideRendererProps): ReactElement {
  const { slide } = props;
  return (
    <section
      style={cardStyle}
      data-testid="slide-renderer"
      data-slide-id={slide.slideId}
      aria-label={`Slide ${slide.slideNumber}: ${slide.title}`}
    >
      <header style={headerRowStyle}>
        <span style={slideNumberPillStyle} data-testid="slide-number">
          {`Slide ${slide.slideNumber}`}
        </span>
        <h2 style={titleStyle} data-testid="slide-title">
          {slide.title}
        </h2>
      </header>
      <div style={bodyRowStyle}>
        <ul style={bulletListStyle} data-testid="slide-bullets">
          {slide.bullets.length === 0 ? (
            <li
              style={emptyBulletStyle}
              data-testid="slide-bullets-empty"
              aria-label="No bullets on this slide"
            >
              No bullets on this slide
            </li>
          ) : (
            slide.bullets.map((bullet, index) => (
              <li
                key={`${slide.slideId}-bullet-${index}`}
                style={bulletItemStyle}
                data-testid={`slide-bullet-${index}`}
              >
                {bullet.text}
              </li>
            ))
          )}
        </ul>
        {slide.thumbnailUrl ? (
          <img
            src={slide.thumbnailUrl}
            alt={`Thumbnail for slide ${slide.slideNumber}`}
            style={thumbnailImageStyle}
            data-testid="slide-thumbnail"
          />
        ) : (
          <div
            style={thumbnailPlaceholderStyle}
            data-testid="slide-thumbnail-placeholder"
            aria-label="Thumbnail unavailable"
          >
            <span style={thumbnailPlaceholderLabelStyle}>
              Thumbnail unavailable
            </span>
          </div>
        )}
      </div>
    </section>
  );
}

const cardStyle: CSSProperties = {
  background: PALETTE.surface,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 12,
  padding: 18,
  color: PALETTE.textHi,
  display: "flex",
  flexDirection: "column",
  gap: 14,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  width: "min(620px, 100%)",
  boxShadow: "0 8px 28px rgba(0,0,0,0.35)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  borderBottom: `1px solid ${PALETTE.border}`,
  paddingBottom: 10,
};

const slideNumberPillStyle: CSSProperties = {
  display: "inline-block",
  padding: "3px 9px",
  borderRadius: 999,
  background: PALETTE.surfaceMute,
  border: `1px solid ${PALETTE.border}`,
  color: PALETTE.textMid,
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: 0.5,
  textTransform: "uppercase",
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: 16,
  fontWeight: 600,
  lineHeight: 1.35,
  color: PALETTE.textHi,
};

const bodyRowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 200px",
  gap: 16,
  alignItems: "start",
};

const bulletListStyle: CSSProperties = {
  margin: 0,
  paddingLeft: 18,
  display: "flex",
  flexDirection: "column",
  gap: 6,
  color: PALETTE.textMid,
  fontSize: 13.5,
  lineHeight: 1.5,
};

const bulletItemStyle: CSSProperties = {
  margin: 0,
};

const emptyBulletStyle: CSSProperties = {
  margin: 0,
  listStyle: "none",
  marginLeft: -18,
  color: PALETTE.textLo,
  fontStyle: "italic",
  fontSize: 13,
};

const thumbnailImageStyle: CSSProperties = {
  width: "100%",
  height: 130,
  objectFit: "cover",
  borderRadius: 8,
  border: `1px solid ${PALETTE.border}`,
  background: PALETTE.surfaceMute,
};

const thumbnailPlaceholderStyle: CSSProperties = {
  width: "100%",
  height: 130,
  borderRadius: 8,
  border: `1px dashed ${PALETTE.border}`,
  background: PALETTE.surfaceMute,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

const thumbnailPlaceholderLabelStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: 11,
  letterSpacing: 0.5,
  textTransform: "uppercase",
};
