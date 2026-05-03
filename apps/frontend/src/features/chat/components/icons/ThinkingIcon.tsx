import type { ReactElement } from "react";

export function ThinkingIcon(): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className="aui-reasoning-group__icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <circle cx="12" cy="12" r="9" strokeWidth="1.5" stroke="currentColor" />
      <path
        d="M9 12h6M12 9v6"
        strokeWidth="1.5"
        stroke="currentColor"
        strokeLinecap="round"
      />
    </svg>
  );
}
