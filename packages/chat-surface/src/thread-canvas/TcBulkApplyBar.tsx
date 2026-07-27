// Model-driven bulk decision/recovery bar.
//
// The exact row-key tuple is projected once by `projectRowsetReviewModel`.
// This component renders that immutable action and returns it unchanged to the
// host. It never reads ledger rows and therefore cannot widen a retry scope.

import type { ReactElement } from "react";

import { DecisionBar, RecoveryBar } from "./ReviewSurface";
import {
  rowsetApplyLabel,
  rowsetApplyPledge,
  rowsetRecoveryPledge,
  rowsetRetryLabel,
} from "./rowsetReviewModel";
import type { RowsetActionContext } from "./rowsetReviewModel";

export interface TcBulkApplyBarProps {
  readonly action: RowsetActionContext;
  readonly onApply: (action: RowsetActionContext) => void;
}

/** Backward-compatible names for the public microcopy helpers. */
export const bulkApplyPledge = rowsetApplyPledge;
export const bulkRetryPledge = rowsetRecoveryPledge;
export const bulkApplyLabel = rowsetApplyLabel;
export const bulkRetryLabel = rowsetRetryLabel;

export function bulkRetryMessage(count: number): string {
  return `${count} writes failed · successes kept — nothing lost.`;
}

export function TcBulkApplyBar({
  action,
  onApply,
}: TcBulkApplyBarProps): ReactElement {
  return action.kind === "retry_failed" ? (
    <RecoveryBar action={action} onAction={onApply} />
  ) : (
    <DecisionBar action={action} onAction={onApply} />
  );
}
