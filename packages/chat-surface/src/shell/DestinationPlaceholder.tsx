import { type CSSProperties, type ReactElement, type ReactNode } from "react";

import { useRouter } from "../providers/RouterProvider";
import { type ShellDestinationSlug } from "./destinations";

// Wave-0 "not built yet" placeholder for top-level destinations that
// don't have a backend yet. This is a permanent SP-1 shell primitive:
// every wave that introduces a new destination but doesn't ship it in
// the same release will mount this until the real surface lands. The
// component is deliberately honest — no fake data, no Retry button, no
// fetches. It tells the user exactly what the destination WILL be,
// names the phase, and offers bridges to working surfaces that
// approximate the intent today.
//
// Cross-destination navigation flows through the host app's wider
// route type ({ screen: "chat"; destination: slug }) because slug
// navigation is not part of ArtifactRoute. The router context is
// generic, so we re-type at the hook site — the host (web today,
// desktop later) supplies a Router that accepts this shape.

export type DestinationPlaceholderBridge = {
  readonly label: string;
  readonly slug: ShellDestinationSlug;
};

export interface DestinationPlaceholderProps {
  /** Big hero icon — re-use the destination's own glyph for continuity
   *  with the AppRail. Falls back to a neutral square if omitted. */
  readonly icon?: ReactNode;
  /** Headline that names the destination's intent. Sentence case. */
  readonly title: string;
  /** One or two sentences describing what the destination WILL be when
   *  it ships. Honest about scope; no marketing language. */
  readonly description: string;
  /** Phase chip text — e.g. "Coming in Phase 8" or "Next release".
   *  Pick whatever is truthful for the consumer; this primitive does
   *  not infer the phase from the slug because the master PRD is the
   *  source of truth and changes faster than the placeholder would. */
  readonly phaseLabel: string;
  /** Optional list of "bridges" to working surfaces that approximate
   *  the destination's intent today. Each renders as a clickable card
   *  with an arrow; clicking calls router.navigate to that destination
   *  via the host's { screen: "chat"; destination: slug } shape. */
  readonly bridges?: ReadonlyArray<DestinationPlaceholderBridge>;
  /** Optional roadmap URL. Renders as a small text link below the
   *  bridges. Omit it if there's no public roadmap to point at. */
  readonly roadmapHref?: string;
}

// Host-route shape used for bridge navigation. The web app's AppRoute
// is a structural superset of this; the desktop substrate's eventual
// route union will need the same shape (or this primitive accepts a
// bridge handler from its consumer instead). For Wave 0 the web is
// the only substrate so the assumption is safe.
type BridgeRoute = {
  readonly screen: "chat";
  readonly destination: ShellDestinationSlug;
};

// Design tokens (values resolve at use-site so Settings → Appearance
// theme/accent changes flow through automatically).
const COLOR_BG = "var(--color-bg)";
const COLOR_SURFACE = "var(--color-surface)";
const COLOR_BORDER = "var(--color-border)";
const COLOR_BORDER_STRONG = "var(--color-border-strong, var(--color-border))";
const COLOR_TEXT = "var(--color-text)";
const COLOR_TEXT_MUTED = "var(--color-text-muted)";
const COLOR_TEXT_SUBTLE = "var(--color-text-subtle)";
const COLOR_ACCENT = "var(--color-accent)";
const COLOR_ACCENT_SOFT =
  "color-mix(in srgb, var(--color-accent) 12%, transparent)";

function FallbackIcon(): ReactElement {
  // Neutral rounded square — only used when a consumer didn't supply
  // a hero icon. Deliberately abstract so it doesn't masquerade as
  // any specific destination.
  return (
    <svg
      aria-hidden
      focusable={false}
      width={48}
      height={48}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.25}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M9 10h6M9 14h4" />
    </svg>
  );
}

function BridgeArrow(): ReactElement {
  return (
    <svg
      aria-hidden
      focusable={false}
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14" />
      <path d="M13 6l6 6-6 6" />
    </svg>
  );
}

export function DestinationPlaceholder(
  props: DestinationPlaceholderProps,
): ReactElement {
  const { icon, title, description, phaseLabel, bridges, roadmapHref } = props;
  const router = useRouter<BridgeRoute>();

  const handleBridgeClick = (slug: ShellDestinationSlug): void => {
    router.navigate({ screen: "chat", destination: slug });
  };

  const rootStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: COLOR_BG,
    color: COLOR_TEXT,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    overflow: "auto",
  };
  // The inner column is constrained so the hero stays centered and
  // generous-whitespace even on ultrawide displays — the rest of the
  // chat-surface uses ~960-1000px max-widths for the same reason.
  const innerStyle: CSSProperties = {
    width: "100%",
    maxWidth: 640,
    margin: "0 auto",
    padding: "64px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    textAlign: "center",
    gap: 20,
  };
  const iconWrapStyle: CSSProperties = {
    width: 88,
    height: 88,
    borderRadius: 20,
    backgroundColor: COLOR_ACCENT_SOFT,
    color: COLOR_ACCENT,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  };
  const titleStyle: CSSProperties = {
    margin: 0,
    fontSize: "var(--font-size-2xl, 1.4rem)",
    fontWeight: "var(--font-weight-semibold, 600)",
    color: COLOR_TEXT,
    lineHeight: "var(--line-height-tight, 1.2)",
  };
  const descriptionStyle: CSSProperties = {
    margin: 0,
    maxWidth: 520,
    color: COLOR_TEXT_MUTED,
    fontSize: "var(--font-size-md, 0.875rem)",
    lineHeight: "var(--line-height-base, 1.5)",
  };
  const phaseChipStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 10px",
    borderRadius: 999,
    backgroundColor: COLOR_ACCENT_SOFT,
    color: COLOR_ACCENT,
    fontSize: "var(--font-size-xs, 0.78rem)",
    fontWeight: "var(--font-weight-medium, 500)",
    letterSpacing: "0.01em",
    border: `1px solid ${COLOR_BORDER}`,
  };
  const bridgesWrapStyle: CSSProperties = {
    width: "100%",
    marginTop: 12,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  };
  const bridgesLabelStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 0.78rem)",
    color: COLOR_TEXT_SUBTLE,
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    textAlign: "left",
    marginBottom: 4,
  };
  const bridgeButtonStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    width: "100%",
    padding: "12px 14px",
    borderRadius: 10,
    border: `1px solid ${COLOR_BORDER}`,
    backgroundColor: COLOR_SURFACE,
    color: COLOR_TEXT,
    fontFamily: "inherit",
    fontSize: "var(--font-size-md, 0.875rem)",
    fontWeight: "var(--font-weight-medium, 500)",
    cursor: "pointer",
    textAlign: "left",
    boxSizing: "border-box",
  };
  const bridgeArrowStyle: CSSProperties = {
    color: COLOR_TEXT_SUBTLE,
    flexShrink: 0,
  };
  const roadmapLinkStyle: CSSProperties = {
    marginTop: 8,
    color: COLOR_TEXT_SUBTLE,
    fontSize: "var(--font-size-xs, 0.78rem)",
    textDecoration: "underline",
    textUnderlineOffset: 3,
  };

  return (
    <div style={rootStyle} data-testid="destination-placeholder">
      <section
        role="region"
        aria-label={title}
        data-component="destination-placeholder"
        style={innerStyle}
      >
        <div
          style={iconWrapStyle}
          data-testid="destination-placeholder-icon"
          aria-hidden="true"
        >
          {icon ?? <FallbackIcon />}
        </div>
        <h1 style={titleStyle} data-testid="destination-placeholder-title">
          {title}
        </h1>
        <p
          style={descriptionStyle}
          data-testid="destination-placeholder-description"
        >
          {description}
        </p>
        <div
          style={phaseChipStyle}
          data-testid="destination-placeholder-phase"
          aria-label={`Status: ${phaseLabel}`}
        >
          {phaseLabel}
        </div>
        {bridges && bridges.length > 0 ? (
          <div
            style={bridgesWrapStyle}
            data-testid="destination-placeholder-bridges"
          >
            <div style={bridgesLabelStyle}>In the meantime</div>
            {bridges.map((bridge) => (
              <button
                key={bridge.slug}
                type="button"
                onClick={() => handleBridgeClick(bridge.slug)}
                style={bridgeButtonStyle}
                data-testid={`destination-placeholder-bridge-${bridge.slug}`}
                data-bridge-slug={bridge.slug}
                // Defensive against the parent forcing a stronger
                // border on hover; consumers can override via theme.
                onMouseEnter={(event) => {
                  event.currentTarget.style.borderColor = COLOR_BORDER_STRONG;
                }}
                onMouseLeave={(event) => {
                  event.currentTarget.style.borderColor = COLOR_BORDER;
                }}
              >
                <span>{bridge.label}</span>
                <span style={bridgeArrowStyle}>
                  <BridgeArrow />
                </span>
              </button>
            ))}
          </div>
        ) : null}
        {roadmapHref ? (
          <a
            href={roadmapHref}
            target="_blank"
            rel="noopener noreferrer"
            style={roadmapLinkStyle}
            data-testid="destination-placeholder-roadmap"
          >
            View roadmap
          </a>
        ) : null}
      </section>
    </div>
  );
}
