// <Icon> — the one component that renders a canonical glyph from `ICON_PATHS`.
//
// Substrate-agnostic, colour via `currentColor`, size-parameterised. Decorative
// by default (`aria-hidden`); pass `title` to make it an accessible image
// (`role="img"` + `aria-label`). Never hand-draw an <svg> in a surface — call
// <Icon name="…" /> so geometry and frame stay in lock-step.
//
// PRD: docs/plan/frontend-parity-v3/PRD-A-icon-system.md (FR-A.1).

import type { CSSProperties, ReactElement } from "react";

import { ICON_PATHS, type IconName } from "./paths";

export interface IconProps {
  /** Which glyph to render. See `IconName` for the full set. */
  readonly name: IconName;
  /** Square px size for width & height. Default 16 (rail passes 17, nav 14). */
  readonly size?: number;
  /** Stroke width. Default 1.7 — the v3 design's canonical line weight. */
  readonly strokeWidth?: number;
  readonly className?: string;
  readonly style?: CSSProperties;
  /**
   * Accessible label. When set, the icon becomes `role="img"` with this name;
   * when absent, the icon is decorative (`aria-hidden`) and the surrounding
   * control supplies the label.
   */
  readonly title?: string;
}

export function Icon({
  name,
  size = 16,
  strokeWidth = 1.7,
  className,
  style,
  title,
}: IconProps): ReactElement {
  const labelled = title !== undefined && title !== "";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      focusable={false}
      role={labelled ? "img" : undefined}
      aria-label={labelled ? title : undefined}
      aria-hidden={labelled ? undefined : true}
    >
      {ICON_PATHS[name]}
    </svg>
  );
}
