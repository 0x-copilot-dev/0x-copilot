import type { ReactElement, ReactNode } from "react";

// Tool-details disclosure for the approval surfaces (PR-1.6).
//
// The host `ActivityDetails` delegated to the shared `ActivityCollapsible`
// primitive (`apps/frontend/.../activity/ActivityCollapsible.tsx`), which is
// still used by other host-side activity cards and stays there. Rather than
// drag that shared primitive across the boundary (and shim it back for its
// other consumers), we inline the exact same `<details>` DOM here — same
// class names, same structure — so the rendered markup is byte-identical
// while `chat-surface` stays app-import-free.
export function ActivityDetails({
  children,
  label = "Tool details",
}: {
  children: ReactNode;
  label?: string;
}): ReactElement {
  return (
    <details className="aui-collapsible aui-activity-card__details">
      <summary className="aui-collapsible__trigger">{label}</summary>
      <div className="aui-collapsible__content aui-activity-card__details-content">
        {children}
      </div>
    </details>
  );
}
