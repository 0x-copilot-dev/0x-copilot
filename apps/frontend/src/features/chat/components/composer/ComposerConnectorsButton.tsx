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
      <span className="atlas-composer-connectors__icon" aria-hidden="true">
        ◇
      </span>
      {activeCount > 0 ? (
        <span className="atlas-composer-connectors__count" aria-hidden="true">
          {activeCount}
        </span>
      ) : null}
    </button>
  );
});
