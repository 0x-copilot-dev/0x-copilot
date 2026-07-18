// <ForkDialog /> — modal shown when a user clicks "Customize" on a
// system or community agent.
//
// Source:
//   - docs/atlas-new-design/destinations/agents-prd.md §4.4 (PATCH on
//     system/community 409s with hint "Use POST /v1/agents/<id>/duplicate"),
//     §4.10 (POST /v1/agents/<id>/duplicate), §3.2 (immutability rule).
//
// Invariants:
//   - SP-1: StatusPill is the warning surface (tone="running" carries the
//     "this will create a copy" visual weight — same primitive as the rest
//     of the design system, no bespoke warning component).
//   - Single forward button per the task brief — "Create your copy".
//   - Pure presentation. Host owns the POST /v1/agents/<id>/duplicate
//     call and the resulting route navigation.

import type { CSSProperties, ReactElement } from "react";

import { StatusPill } from "@0x-copilot/design-system";

export interface ForkDialogProps {
  /** Display name of the agent being forked. */
  readonly agentName: string;
  /** Origin — drives the copy ("system" vs "community"). */
  readonly origin: "system" | "community";
  /** Confirm handler — host calls POST /v1/agents/<id>/duplicate. */
  readonly onConfirm: () => void;
  /** Cancel handler — host closes the dialog. */
  readonly onCancel: () => void;
  /**
   * Optional in-flight feedback. When true, the confirm button shows
   * "Creating…" and disables; cancel remains enabled.
   */
  readonly busy?: boolean;
}

export function ForkDialog(props: ForkDialogProps): ReactElement {
  const { agentName, origin, onConfirm, onCancel, busy = false } = props;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="agent-fork-dialog-title"
      aria-describedby="agent-fork-dialog-body"
      data-testid="agent-fork-dialog"
      style={overlayStyle}
      // Click on the backdrop dismisses — the inner content stops
      // propagation below so clicks inside don't close the dialog.
      onClick={busy ? undefined : onCancel}
    >
      <div
        style={panelStyle}
        onClick={(e) => e.stopPropagation()}
        data-testid="agent-fork-dialog-panel"
      >
        <div style={headerStyle}>
          <StatusPill
            tone="running"
            label="Heads up"
            data-testid="agent-fork-dialog-warning-pill"
          />
          <h2 id="agent-fork-dialog-title" style={titleStyle}>
            Edit a {origin} agent?
          </h2>
        </div>
        <p id="agent-fork-dialog-body" style={bodyStyle}>
          <strong>{agentName}</strong> is a {origin} agent — the original can't
          be edited. We'll create your own copy. Your changes stay on your copy;
          the original keeps shipping updates.
        </p>
        <div style={actionsStyle}>
          <button
            type="button"
            onClick={onCancel}
            data-testid="agent-fork-dialog-cancel"
            style={secondaryButtonStyle}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="agent-fork-dialog-confirm"
            style={primaryButtonStyle}
            disabled={busy}
          >
            {busy ? "Creating your copy…" : "Create your copy"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ===========================================================================
// Styles.
// ===========================================================================

const overlayStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0, 0, 0, 0.45)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1000,
};

const panelStyle: CSSProperties = {
  width: "min(420px, 92vw)",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  padding: 20,
  display: "flex",
  flexDirection: "column",
  gap: 12,
  boxShadow: "0 18px 48px rgba(0, 0, 0, 0.3)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  flexWrap: "wrap",
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-lg)",
  fontWeight: 600,
};

const bodyStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  lineHeight: 1.5,
  color: "var(--color-text-muted)",
};

const actionsStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
  marginTop: 4,
};

const primaryButtonStyle: CSSProperties = {
  background: "var(--color-accent)",
  color: "var(--color-bg)",
  border: "none",
  borderRadius: 6,
  padding: "6px 14px",
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "5px 12px",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
};
