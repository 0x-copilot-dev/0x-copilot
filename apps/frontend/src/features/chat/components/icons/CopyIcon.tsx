import type { ReactElement } from "react";

export function CopyIcon(): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className="aui-footer-icon-button__icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}
