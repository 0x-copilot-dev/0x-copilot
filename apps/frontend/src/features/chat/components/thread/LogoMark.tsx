import type { ReactElement } from "react";

/**
 * Atlas brand mark — single source of truth for the `A` glyph used in
 * the sidebar header and (smaller) on every assistant message.
 *
 * `compact` hides the wordmark — used when the sidebar is collapsed,
 * or when the mark sits next to a message body and the wordmark would
 * be redundant.
 */
export function LogoMark({
  compact = false,
}: {
  compact?: boolean;
}): ReactElement {
  return (
    <div className="aui-logo" aria-label="Copilot">
      <span className="aui-logo__mark" aria-hidden="true">
        C
      </span>
      {compact ? null : <span className="aui-logo__wordmark">Copilot</span>}
    </div>
  );
}
