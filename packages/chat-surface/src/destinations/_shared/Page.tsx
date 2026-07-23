// <Page> — the design `.pg` content-column shell (PRD-10 D4).
//
// The one member of the `_shared/` list-surface set (PageLead / SectionHeader /
// RowList / Row) that was never written — which is why `.pg` was copy-pasted four
// ways with three different geometries. This is the single source of truth for the
// 960px content column.
//
// Design (`.pg`, copilot.css:1552-1555): `max-width: 960px; padding: 20px 24px
// 40px`. That is the WHOLE declaration — no margin. Its parent `.main`
// (copilot.css:381-388) is a plain `flex-direction: column` block with no
// `align-items`, so the column sits flush against the rail: `Page` is
// LEFT-ALIGNED, with NO `margin: 0 auto` (README G6). Centring was a live-app
// invention; a shared primitive is the wrong place to institutionalise an
// undecided divergence, so `Page` ships design-faithful. If a later PRD wants
// centring it changes this one file, not each surface.
//
// Callers add their own layout (flex column, gap) via `style`; `Page` owns only
// the shell geometry. Substrate-agnostic; token-driven only.

import type {
  CSSProperties,
  HTMLAttributes,
  ReactElement,
  ReactNode,
} from "react";

export interface PageProps extends HTMLAttributes<HTMLDivElement> {
  readonly children: ReactNode;
}

const pageStyle: CSSProperties = {
  width: "100%",
  maxWidth: 960,
  padding: "20px 24px 40px",
  boxSizing: "border-box",
};

export function Page({
  children,
  className,
  style,
  ...rest
}: PageProps): ReactElement {
  return (
    <div
      className={className === undefined ? "pg" : `pg ${className}`}
      data-page=""
      data-testid="page"
      style={{ ...pageStyle, ...style }}
      {...rest}
    >
      {children}
    </div>
  );
}
