// PR 3.4 — single popover used by the topbar `<ConnectorsPill>` and the
// composer `<ComposerConnectorsButton>`. Renders the four-state row
// vocabulary and round-trips toggles through the PR 1.2 hook the parent
// owns. The popover is presentational — no fetches, no localStorage.

import {
  AppIcon,
  Button,
  Menu,
  classNames,
} from "@enterprise-search/design-system";
import {
  useCallback,
  useRef,
  type KeyboardEvent,
  type ReactElement,
  type RefObject,
} from "react";
import type { ConnectorRow } from "./projectConnectors";

export type ConnectorPopoverPlacement = "down" | "up";

export interface ConnectorPopoverProps {
  /** Open / close state owned by the parent. */
  open: boolean;
  /** Closes the popover. Wired to `Menu`'s pointerdown-outside / Escape. */
  onClose: () => void;
  /** The trigger button. Used both for outside-click suppression and
   * Menu anchor. */
  triggerRef: RefObject<HTMLElement | null>;
  /** Pre-projected rows (see `projectConnectors`). */
  rows: readonly ConnectorRow[];
  /**
   * Toggle a single connector. `nextScopes === null` pauses; an array
   * activates. Parent forwards to `useConversationConnectors.patch`.
   */
  onToggle: (server_id: string, nextScopes: readonly string[] | null) => void;
  /** Disconnected → Connect. Parent forwards to `connectors.authenticate`. */
  onConnect: (server_id: string) => void;
  /** Workspace-off → Enable. Parent routes to Settings → Connectors. */
  onEnableInSettings: (server_id: string) => void;
  /** Manage link in the footer routes to Settings → Connectors. */
  onManage: () => void;
  /** Side of the trigger to anchor on. Topbar = "down"; composer = "up". */
  placement?: ConnectorPopoverPlacement;
  /** Inline error from the last optimistic toggle (PR 1.2 hook). */
  error?: string | null;
  /** Read-only chrome (e.g. shared-chat recipient view, W6). */
  readOnly?: boolean;
}

const ROW_SELECTOR = '[data-row="true"]';

/**
 * Connector popover — anchored, dismissable, keyboard-driven.
 *
 * The Menu host shell handles pointerdown-outside + Escape dismissal.
 * Internally rows are ARIA `role="menuitemcheckbox"` for active/paused
 * (toggle semantics) and `role="menuitem"` for Connect / Enable
 * (single-action semantics). Keyboard nav is roving-tabindex via a
 * tiny `onKeyDown` handler — no Radix dependency.
 */
export function ConnectorPopover({
  open,
  onClose,
  triggerRef,
  rows,
  onToggle,
  onConnect,
  onEnableInSettings,
  onManage,
  placement = "down",
  error,
  readOnly,
}: ConnectorPopoverProps): ReactElement | null {
  const listRef = useRef<HTMLDivElement>(null);

  const onKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    const list = listRef.current;
    if (!list) {
      return;
    }
    const items = Array.from(
      list.querySelectorAll<HTMLElement>(ROW_SELECTOR),
    ).filter((el) => !el.hasAttribute("data-disabled"));
    if (items.length === 0) {
      return;
    }
    const active = document.activeElement;
    const currentIndex = items.findIndex((el) => el === active);

    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const nextIndex =
        currentIndex < 0
          ? direction === 1
            ? 0
            : items.length - 1
          : (currentIndex + direction + items.length) % items.length;
      items[nextIndex]?.focus();
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      items[0]?.focus();
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      items[items.length - 1]?.focus();
    }
  }, []);

  if (!open) {
    return null;
  }

  return (
    <Menu
      open={open}
      onClose={onClose}
      anchorRef={triggerRef}
      side={placement}
      align="right"
      className="atlas-connector-popover"
      aria-label="Per-chat connectors"
    >
      <div className="atlas-connector-popover__head">
        <span className="atlas-connector-popover__title">Connectors</span>
        <span className="atlas-connector-popover__sub">
          Active for this chat
        </span>
      </div>
      <div
        ref={listRef}
        className="atlas-connector-popover__list"
        onKeyDown={onKeyDown}
      >
        {rows.length === 0 ? (
          <div className="atlas-connector-popover__empty" role="note">
            No connectors installed yet.
          </div>
        ) : (
          rows.map((row) => (
            <Row
              key={row.server_id}
              row={row}
              readOnly={readOnly}
              onToggle={onToggle}
              onConnect={onConnect}
              onEnableInSettings={onEnableInSettings}
            />
          ))
        )}
      </div>
      {error ? (
        <div className="atlas-connector-popover__error" role="alert">
          {error}
        </div>
      ) : null}
      <div className="atlas-connector-popover__foot">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            onManage();
            onClose();
          }}
        >
          Manage in Settings →
        </Button>
      </div>
    </Menu>
  );
}

interface RowProps {
  row: ConnectorRow;
  readOnly?: boolean;
  onToggle: (server_id: string, nextScopes: readonly string[] | null) => void;
  onConnect: (server_id: string) => void;
  onEnableInSettings: (server_id: string) => void;
}

function Row({
  row,
  readOnly,
  onToggle,
  onConnect,
  onEnableInSettings,
}: RowProps): ReactElement {
  const isToggle = row.state === "active" || row.state === "paused";
  const isActive = row.state === "active";
  const isDisconnected = row.state === "disconnected";
  const isWorkspaceOff = row.state === "workspace_off";

  const stateLabel = LABEL_BY_STATE[row.state];
  const actionLabel = isToggle
    ? isActive
      ? "Pause"
      : "Resume"
    : isDisconnected
      ? "Connect"
      : "Enable";

  const ariaLabel = `${row.display_name} — ${stateLabel}. Press Space to ${actionLabel.toLowerCase()}.`;

  const onActivate = () => {
    if (readOnly) {
      return;
    }
    if (isToggle) {
      onToggle(row.server_id, isActive ? null : row.default_scopes);
    } else if (isDisconnected) {
      onConnect(row.server_id);
    } else if (isWorkspaceOff) {
      onEnableInSettings(row.server_id);
    }
  };

  return (
    <button
      type="button"
      data-row="true"
      data-state={row.state}
      data-disabled={readOnly || undefined}
      role={isToggle ? "menuitemcheckbox" : "menuitem"}
      aria-checked={isToggle ? isActive : undefined}
      aria-label={ariaLabel}
      tabIndex={-1}
      disabled={readOnly}
      className={classNames(
        "atlas-connector-row",
        `atlas-connector-row--${row.state}`,
      )}
      onClick={onActivate}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onActivate();
        }
      }}
    >
      <AppIcon name={row.display_name} className="atlas-connector-row__glyph" />
      <span className="atlas-connector-row__name">{row.display_name}</span>
      <span className="atlas-connector-row__state" aria-hidden="true">
        {stateLabel}
      </span>
      <span className="atlas-connector-row__action" aria-hidden="true">
        {actionLabel}
      </span>
    </button>
  );
}

const LABEL_BY_STATE: Record<ConnectorRow["state"], string> = {
  active: "Active",
  paused: "Paused",
  disconnected: "Not connected",
  workspace_off: "Workspace off",
};
