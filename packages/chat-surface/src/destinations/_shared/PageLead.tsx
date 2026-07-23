// <PageLead> — the design `.pg-lead` intro paragraph.
//
// Source: docs/plan/frontend-parity-v3/PRD-G-destination-parity.md (FR-G.1).
// The v3 list destinations open with a small muted lead paragraph — the rail
// already labels the screen, so there is NO 22px page title (README decision 1).
//
// A quiet 12px (--font-size-xs) muted paragraph, loose line-height, capped at
// ~72ch so the copy stays readable. Substrate-agnostic; token-driven only.

import type {
  CSSProperties,
  HTMLAttributes,
  ReactElement,
  ReactNode,
} from "react";

export interface PageLeadProps extends HTMLAttributes<HTMLParagraphElement> {
  readonly children: ReactNode;
}

const pageLeadStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  lineHeight: "var(--line-height-loose)",
  color: "var(--color-text-muted)",
  maxWidth: "72ch",
};

export function PageLead({
  children,
  style,
  ...rest
}: PageLeadProps): ReactElement {
  // No decorative class: the design's `.pg-lead` carried no CSS in the shipped
  // app (the geometry below is the real contract). A caller-supplied `className`
  // rides through `{...rest}`.
  return (
    <p style={{ ...pageLeadStyle, ...style }} data-testid="page-lead" {...rest}>
      {children}
    </p>
  );
}
