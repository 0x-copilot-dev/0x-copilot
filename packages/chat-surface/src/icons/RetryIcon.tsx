import type { ReactElement } from "react";

export function RetryIcon(): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className="aui-footer-icon-button__icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <path d="M20 12a8 8 0 1 1-2.34-5.66" />
      <path d="M20 4v6h-6" />
    </svg>
  );
}
