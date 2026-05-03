import type { ReactElement } from "react";

export function LogoMark({
  compact = false,
}: {
  compact?: boolean;
}): ReactElement {
  return (
    <div className="aui-logo" aria-label="assistant-ui">
      <span className="aui-logo__mark" aria-hidden="true">
        ✦
      </span>
      {compact ? null : <span>assistant-ui</span>}
    </div>
  );
}
