import type { CSSProperties, ReactNode } from "react";

import {
  TcInlineDiff,
  type InlineDiffState,
  type PendingDiff,
} from "@enterprise-search/chat-surface";

export interface EmailDiffOverlayProps {
  readonly diff: PendingDiff;
  readonly state: InlineDiffState;
  readonly progressPercent?: number;
  readonly onApprove: () => void;
  readonly onReject: () => void;
  readonly approveLabel?: string;
  readonly rejectLabel?: string;
}

// Float anchored to the renderer's PENDING block. The parent renders an
// element with `position: relative` so this card stays bound to that
// region as it grows; absolute layout keeps the card from displacing
// surrounding body text.
const overlayStyle: CSSProperties = {
  position: "absolute",
  right: -8,
  top: "calc(100% + 12px)",
  zIndex: 10,
};

export function EmailDiffOverlay(props: EmailDiffOverlayProps): ReactNode {
  const {
    diff,
    state,
    progressPercent,
    onApprove,
    onReject,
    approveLabel = "Approve & send",
    rejectLabel = "Reject",
  } = props;
  return (
    <div
      style={overlayStyle}
      data-testid="email-diff-overlay"
      data-diff-id={diff.diffId}
    >
      <TcInlineDiff
        state={state}
        progressPercent={progressPercent}
        provenance={diff.provenance}
        title={diff.title}
        description={diff.description}
        onApprove={onApprove}
        onReject={onReject}
        approveLabel={approveLabel}
        rejectLabel={rejectLabel}
      />
    </div>
  );
}
