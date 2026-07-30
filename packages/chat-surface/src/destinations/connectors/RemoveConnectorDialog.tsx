// Remove-a-connector confirmation dialog (Tools destination).
//
// The destructive counterpart to <ConnectModal>, on the SAME chrome: the
// shared <Modal> (head with the connector's identity tile + mono subtitle,
// body, foot with the actions) and design-system <Button>s. No new styles —
// every value here is either a design-system token or a layout property, and
// the ember "Remove" is the kit's existing `variant="danger"` (the design's
// text-only .cbtn--danger, not a filled destructive button).
//
// Why a dialog and not the two-step inline confirm this replaces: the row
// swapped its "Remove" button for a "Cancel / Remove" pair in place, which
// (a) read as two more row affordances rather than a question, sitting next
// to the row's existing Connect + access-mode controls, and (b) never said
// what removal actually does. A dialog can state the consequence, and it is
// the idiom this surface already uses for the connect half of the lifecycle.
//
// Modal supplies the dismissal contract (Escape, backdrop, ×, focus trap,
// focus restore to the trigger) and focuses the × first — dismissive by
// construction, so a stray Enter is never one keystroke from confirming.
//
// Pure presentation: the host owns the delete I/O and the error toast.

import type { CSSProperties, ReactElement } from "react";

import type { Connector } from "@0x-copilot/api-types";
import { AppIcon, Button } from "@0x-copilot/design-system";

import { Modal } from "../../settings/Modal";

export interface RemoveConnectorDialogProps {
  /** The connector awaiting confirmation; `null` keeps the dialog closed. */
  readonly connector: Connector | null;
  /** Dismiss without removing (× / Escape / backdrop / Cancel all land here). */
  readonly onCancel: () => void;
  /** Confirm — the host performs the delete. */
  readonly onConfirm: (connector: Connector) => void;
}

export function RemoveConnectorDialog({
  connector,
  onCancel,
  onConfirm,
}: RemoveConnectorDialogProps): ReactElement | null {
  if (connector === null) return null;

  const footer = (
    <>
      <span aria-hidden="true" />
      <div style={actionsStyle}>
        <Button
          variant="ghost"
          onClick={onCancel}
          data-testid="connector-remove-cancel"
        >
          Cancel
        </Button>
        <Button
          variant="danger"
          onClick={() => onConfirm(connector)}
          data-testid="connector-remove-confirm"
        >
          Remove
        </Button>
      </div>
    </>
  );

  return (
    <Modal
      open
      onClose={onCancel}
      title={`Remove ${connector.display_name}?`}
      subtitle="this can't be undone"
      logo={<AppIcon name={connector.slug} size="tile" tone="neutral" />}
      footer={footer}
      closeLabel="Cancel removing this tool"
    >
      <p style={bodyCopyStyle} data-testid="connector-remove-body">
        The agent loses access to {connector.display_name} immediately, and its
        authorization is deleted along with the stored tokens. Connecting it
        again means signing in from scratch.
      </p>
    </Modal>
  );
}

// === Styles ==============================================================
// Layout only. <Modal>'s foot is `justify-content: space-between` (it was
// built for StepDots + actions), so the leading spacer above plus this cell
// park the pair at the trailing edge.

const actionsStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-sm)",
};

const bodyCopyStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted)",
};
