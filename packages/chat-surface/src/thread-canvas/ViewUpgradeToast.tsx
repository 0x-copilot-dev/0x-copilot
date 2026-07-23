// View-upgrade toast (Generative Surfaces v2, PRD-B3 / SDR §7 S5).
//
// A non-modal, dismiss-on-timeout notice shown when a background shaping pass
// upgrades a surface's effective tier generic → shaped: "View upgraded ·
// r<short>·<seq>", with a one-click "Keep generic" back to the honest generic
// view (which pins a durable `view.preference`). Kit-only styling (design-system
// `.ui-card` recipe + `.ui-caption`/`Button ghost`); no port/clock/`window`
// reads — the auto-dismiss timer is a React effect over the injected callback.

import { useEffect, type CSSProperties, type ReactElement } from "react";

import { Button } from "@0x-copilot/design-system";

export interface ViewUpgradeToastProps {
  /** The upgraded surface's id (passed back on Keep generic). */
  readonly surfaceId: string;
  /** The surface's ledger id ("r<short>·<seq>") for the accountable line. */
  readonly ledgerId: string;
  /** Pin the surface to generic (fires `view.preference {keep: generic}`). */
  readonly onKeepGeneric: (surfaceId: string) => void;
  /** Dismiss without pinning (timeout or explicit close). */
  readonly onDismiss: () => void;
  /** Auto-dismiss delay in ms (0 disables the timer). Default 8000. */
  readonly autoDismissMs?: number;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  gap: "var(--space-sm)",
  padding: "8px 12px",
  maxWidth: 420,
};

const textStyle: CSSProperties = { color: "var(--color-text)" };
const idStyle: CSSProperties = { color: "var(--color-text-muted)" };
const spacerStyle: CSSProperties = { flex: "1 1 auto" };

export function ViewUpgradeToast({
  surfaceId,
  ledgerId,
  onKeepGeneric,
  onDismiss,
  autoDismissMs = 8000,
}: ViewUpgradeToastProps): ReactElement {
  useEffect(() => {
    if (autoDismissMs <= 0) return;
    const timer = setTimeout(onDismiss, autoDismissMs);
    return () => clearTimeout(timer);
  }, [autoDismissMs, onDismiss]);

  return (
    <div
      className="ui-card ui-card--muted"
      role="status"
      aria-live="polite"
      style={rootStyle}
      data-testid="tc-view-upgrade-toast"
    >
      <span className="ui-caption" style={textStyle}>
        View upgraded
      </span>
      <span
        className="ui-mono-caps"
        style={idStyle}
        data-testid="tc-view-upgrade-ledger-id"
      >
        {ledgerId}
      </span>
      <span style={spacerStyle} aria-hidden="true" />
      <Button
        variant="ghost"
        size="sm"
        onClick={() => onKeepGeneric(surfaceId)}
        data-testid="tc-view-upgrade-keep-generic"
      >
        Keep generic
      </Button>
    </div>
  );
}
