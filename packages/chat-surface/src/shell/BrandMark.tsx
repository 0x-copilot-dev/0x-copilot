import { useId, type ReactElement } from "react";

// The 0xCopilot brand mark — the turbine ("six swept rays + hub"). Geometry is
// the source-of-truth `0xCopilot-kit/brand/favicon.svg`: six sky-gradient blades
// (#9bd4ff → #4593d8) swept around a dark hub with a sky centre dot. This is
// the SINGLE brand mark — the rail, the login, and any other brand moment all
// render THIS component, never a hand-rolled glyph. It is intentionally a fixed
// sky gradient (the brand identity does not recolour with the accent switcher)
// and renders the turbine only, with no app-icon container, so it sits cleanly
// on any surface.

const BLADE = "M200 96q46 10 54 60-28-8-54-24Z";
const BLADE_ANGLES = [0, 60, 120, 180, 240, 300] as const;

export interface BrandMarkProps {
  /** Rendered width/height in px. Default 24. */
  readonly size?: number;
  /** Accessible label; when omitted the mark is decorative (`aria-hidden`). */
  readonly title?: string;
}

export function BrandMark({ size = 24, title }: BrandMarkProps): ReactElement {
  // Unique per instance so multiple marks on a page don't share a gradient id.
  const gradientId = useId();
  return (
    <svg
      viewBox="0 0 400 400"
      width={size}
      height={size}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable={false}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#9bd4ff" />
          <stop offset="1" stopColor="#4593d8" />
        </linearGradient>
      </defs>
      <g fill={`url(#${gradientId})`}>
        {BLADE_ANGLES.map((angle) => (
          <path key={angle} d={BLADE} transform={`rotate(${angle} 200 200)`} />
        ))}
      </g>
      <circle cx="200" cy="200" r="30" fill="#0d0c10" />
      <circle cx="200" cy="200" r="15" fill="#5fb2ec" />
    </svg>
  );
}
