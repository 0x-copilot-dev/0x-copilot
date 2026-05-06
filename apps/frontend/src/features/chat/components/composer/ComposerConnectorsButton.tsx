// PR 3.4 — composer-anchored trigger for the ConnectorPopover.
//
// Sibling of the topbar `<ConnectorsPill>`. Both triggers open the same
// popover (mounted by ChatScreen). The composer button shows a count
// badge of active connectors so users can see at a glance whether the
// chat is scoped before sending a prompt.

import { classNames } from "@enterprise-search/design-system";
import { forwardRef, type ReactElement } from "react";

export interface ComposerConnectorsButtonProps {
  /** Number of connectors active for the current chat. */
  activeCount: number;
  /** Open state — drives `aria-expanded` + the pressed visual. */
  open?: boolean;
  /** Click handler — parent toggles the popover. */
  onClick: () => void;
  /** Read-only chrome (shared-chat recipient view). */
  disabled?: boolean;
  className?: string;
}

export const ComposerConnectorsButton = forwardRef<
  HTMLButtonElement,
  ComposerConnectorsButtonProps
>(function ComposerConnectorsButton(
  { activeCount, open, onClick, disabled, className },
  ref,
): ReactElement {
  const label =
    activeCount === 0
      ? "Connectors — none active for this chat"
      : `Connectors — ${activeCount} active for this chat`;
  return (
    <button
      ref={ref}
      type="button"
      className={classNames(
        "aui-icon-button",
        "atlas-composer-connectors",
        className,
      )}
      onClick={onClick}
      disabled={disabled}
      aria-haspopup="menu"
      aria-expanded={open ?? false}
      aria-label={label}
      data-tooltip="Per-chat connectors"
    >
      <svg
        className="atlas-composer-connectors__icon"
        viewBox="0 0 24 24"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M12 3 3 7.5l9 4.5 9-4.5L12 3Z" />
        <path d="M3 12.5 12 17l9-4.5" />
        <path d="M3 17.5 12 22l9-4.5" />
      </svg>
      {activeCount > 0 ? (
        <span className="atlas-composer-connectors__count" aria-hidden="true">
          {activeCount}
        </span>
      ) : null}
    </button>
  );
});
