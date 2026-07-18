// PR 3.4 — single popover used by the topbar `<ConnectorsPill>` and the
// composer `<ComposerConnectorsButton>`. Renders the four-state row
// vocabulary and round-trips toggles through the PR 1.2 hook the parent
// owns. The popover is presentational — no fetches, no localStorage.
//
// PR 3.4.1 — visual fidelity follow-up. Each row gets a brand favicon
// (with letter-glyph fallback), a one-line scope subtitle, and either
// a slider knob (active / paused) or a Connect / Enable pill
// (disconnected / workspace_off). Header copy adopts the design's
// "Searching this chat / {n} of {N} connectors active" layout with an
// inline Manage caret link; the footer becomes an explainer line.

import { AppIcon, Button, Menu, classNames } from "@0x-copilot/design-system";
import {
  useCallback,
  useRef,
  type KeyboardEvent,
  type ReactElement,
  type RefObject,
} from "react";
import { activeCount, type ConnectorRow } from "./projectConnectors";

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
  /** Manage link in the header routes to Settings → Connectors. */
  onManage: () => void;
  /** Side of the trigger to anchor on. Topbar = "down"; composer = "up". */
  placement?: ConnectorPopoverPlacement;
  /** Inline error from the last optimistic toggle (PR 1.2 hook). */
  error?: string | null;
  /** Read-only chrome (e.g. shared-chat recipient view, W6). */
  readOnly?: boolean;
  /** Viewer's admin status. Non-admins can't enable workspace-off
   *  rows that are flagged ``admin_managed``; the row's button is
   *  disabled and a tooltip explains. */
  isAdmin?: boolean;
  /** A run is in progress for this conversation. When true, toggles
   *  are locked because connector scope is frozen at run-start; mid-flight
   *  scope changes would leave the agent's plan stale. The popover surfaces
   *  this with a notice so the user understands why their toggle is inert. */
  runInProgress?: boolean;
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
  isAdmin = true,
  runInProgress = false,
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

  const total = rows.length;
  const active = activeCount(rows);

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
        <div className="atlas-connector-popover__head-text">
          <span className="atlas-connector-popover__title">
            Searching this chat
          </span>
          <span className="atlas-connector-popover__sub">
            {active} of {total} connectors active
          </span>
        </div>
        <button
          type="button"
          className="atlas-connector-popover__manage"
          onClick={() => {
            onManage();
            onClose();
          }}
        >
          Manage
          <ManageCaretIcon />
        </button>
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
              readOnly={readOnly || runInProgress}
              isAdmin={isAdmin}
              onToggle={onToggle}
              onConnect={onConnect}
              onEnableInSettings={onEnableInSettings}
            />
          ))
        )}
      </div>
      {runInProgress ? (
        <div
          className="atlas-connector-popover__notice"
          role="status"
          aria-live="polite"
        >
          Run in progress — toggles unlock when this turn finishes.
        </div>
      ) : null}
      {error ? (
        <div className="atlas-connector-popover__error" role="alert">
          {error}
        </div>
      ) : null}
      <div className="atlas-connector-popover__foot">
        Applies to your next message. Runs in progress keep their original
        scope.
      </div>
    </Menu>
  );
}

interface RowProps {
  row: ConnectorRow;
  readOnly?: boolean;
  isAdmin: boolean;
  onToggle: (server_id: string, nextScopes: readonly string[] | null) => void;
  onConnect: (server_id: string) => void;
  onEnableInSettings: (server_id: string) => void;
}

function Row({
  row,
  readOnly,
  isAdmin,
  onToggle,
  onConnect,
  onEnableInSettings,
}: RowProps): ReactElement {
  const isToggle = row.state === "active" || row.state === "paused";
  const isActive = row.state === "active";
  const isDisconnected = row.state === "disconnected";
  const isWorkspaceOff = row.state === "workspace_off";

  // Non-admins can't enable a workspace-managed connector; surface a
  // tooltip and disable the row's activation. Toggle / Connect rows
  // remain enabled because the user owns those decisions.
  const blockedByAdminScope = isWorkspaceOff && row.admin_managed && !isAdmin;
  const disabled = readOnly || blockedByAdminScope;

  const stateLabel = LABEL_BY_STATE[row.state];
  const subtitle = row.scopes_summary ?? FALLBACK_SUBTITLE_BY_STATE[row.state];
  const actionLabel = isToggle
    ? isActive
      ? "Pause"
      : "Resume"
    : isDisconnected
      ? "Connect"
      : "Enable";

  const ariaLabel =
    `${row.display_name} — ${stateLabel}.` +
    (subtitle ? ` ${subtitle}` : "") +
    ` Press Space to ${actionLabel.toLowerCase()}.`;

  const onActivate = () => {
    if (disabled) {
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
      data-disabled={disabled || undefined}
      role={isToggle ? "menuitemcheckbox" : "menuitem"}
      aria-checked={isToggle ? isActive : undefined}
      aria-label={ariaLabel}
      title={
        blockedByAdminScope
          ? `Ask your workspace admin to enable ${row.display_name}.`
          : undefined
      }
      tabIndex={-1}
      disabled={disabled}
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
      <AppIcon
        name={row.display_name}
        logoUrl={row.logo_url ?? undefined}
        color={row.brand_color ?? undefined}
        className="atlas-connector-row__glyph"
      />
      <div className="atlas-connector-row__col">
        <div className="atlas-connector-row__title">
          <span className="atlas-connector-row__name">{row.display_name}</span>
          {isDisconnected && (
            <span className="atlas-connector-row__badge" aria-hidden="true">
              Not connected
            </span>
          )}
          {isWorkspaceOff && (
            <span className="atlas-connector-row__badge" aria-hidden="true">
              Off · Workspace
            </span>
          )}
        </div>
        {subtitle && (
          <div className="atlas-connector-row__subtitle" aria-hidden="true">
            {subtitle}
          </div>
        )}
      </div>
      {isToggle ? (
        <span
          className="atlas-connector-row__switch"
          data-checked={isActive || undefined}
          aria-hidden="true"
        />
      ) : (
        <span
          className={classNames(
            "atlas-connector-row__pill",
            isWorkspaceOff && "atlas-connector-row__pill--ghost",
          )}
          aria-hidden="true"
        >
          {actionLabel}
        </span>
      )}
    </button>
  );
}

function ManageCaretIcon(): ReactElement {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h6M6 3l3 3-3 3" />
    </svg>
  );
}

const LABEL_BY_STATE: Record<ConnectorRow["state"], string> = {
  active: "Active",
  paused: "Paused",
  disconnected: "Not connected",
  workspace_off: "Workspace off",
};

const FALLBACK_SUBTITLE_BY_STATE: Record<ConnectorRow["state"], string | null> =
  {
    active: null,
    paused: null,
    disconnected: "Not connected — Copilot can't read this app yet.",
    workspace_off: "Disabled by your workspace admin.",
  };
