import type { RowSetEffectReview } from "@0x-copilot/api-types";
import type {
  LedgerApplyResult,
  LedgerRowChange,
  LedgerStagedWrite,
  LedgerStagedWriteStatus,
} from "./ledgerProjection";

export type ReviewTone = "neutral" | "warning" | "danger" | "success";

export interface DiffValueModel {
  readonly field: string;
  readonly before: unknown;
  readonly after: unknown;
}

export type RowsetDecision = "approved" | "held";
export type RowsetApplyOutcome = "pending" | "applied" | "failed";

export interface RowsetReviewRow {
  readonly rowKey: string;
  readonly title: string;
  readonly diffs: readonly DiffValueModel[];
  readonly decision: RowsetDecision;
  readonly decidedBy: "agent" | "user" | "policy" | null;
  readonly agentHoldReason: string | null;
  readonly outcome: RowsetApplyOutcome;
  readonly canDecide: boolean;
}

export interface RowsetReviewCounts {
  readonly total: number;
  readonly approved: number;
  readonly held: number;
  readonly applied: number;
  readonly failed: number;
  readonly pending: number;
}

export interface ReviewStatusModel {
  readonly label: string;
  readonly tone: ReviewTone;
}

export interface ReviewProvenance {
  readonly kind: "Table";
  readonly source: string;
  readonly operation: string;
  readonly policy: "per-row approval";
  readonly ledgerId: string;
}

interface RowsetActionBase {
  readonly stageId: string;
  readonly revision: number;
  readonly proposalDigest: string;
  readonly targetDigest: string;
  /** Exact immutable scope submitted by the host. Never reconstruct on click. */
  readonly rowKeys: readonly string[];
  /** Ledger basis used by the host to keep the button busy until replay advances. */
  readonly basisSequence: number;
  readonly basisLedgerId: string | null;
  readonly label: string;
  readonly message: string;
  readonly accessibleLabel: string;
  readonly pending: boolean;
  readonly disabled: boolean;
}

export interface ApplyContext extends RowsetActionBase {
  readonly kind: "apply";
}

export interface RecoveryContext extends RowsetActionBase {
  readonly kind: "retry_failed";
  readonly failedRowKeys: readonly string[];
}

export type RowsetActionContext = ApplyContext | RecoveryContext;

export interface RowsetDecisionContext {
  readonly stageId: string;
  readonly revision: number;
  readonly proposalDigest: string;
  readonly targetDigest: string;
  readonly rowKey: string;
  readonly decision: "approve" | "hold";
  readonly basisSequence: number;
}

export type RowsetReviewStatus = LedgerStagedWriteStatus | "held" | "partial";

export interface RowsetReviewModel {
  readonly stageId: string;
  readonly surfaceId: string;
  readonly revision: number;
  readonly proposalDigest: string;
  readonly targetDigest: string;
  readonly lastSequence: number;
  readonly status: RowsetReviewStatus;
  readonly applyResult: LedgerApplyResult | null;
  readonly title: string;
  readonly summary: string;
  readonly statusMark: ReviewStatusModel;
  readonly rows: readonly RowsetReviewRow[];
  readonly counts: RowsetReviewCounts;
  readonly action: RowsetActionContext | null;
  readonly provenance: ReviewProvenance;
}

export interface RowsetReviewProjectionOptions {
  readonly title?: string;
  readonly summary?: string;
  readonly actionNotice?: string;
  readonly actionPending?: boolean;
}

export const rowsetApplyPledge =
  "Writes apply only to rows you approve. Held rows stay untouched.";

export const rowsetRecoveryPledge =
  "Some writes failed. Applied rows are safe — nothing lost.";

export function rowsetApplyLabel(count: number): string {
  return `Apply ${count} changes →`;
}

export function rowsetRetryLabel(count: number): string {
  return `Retry ${count} failed →`;
}

export function rowsetCountsSummary(
  counts: Pick<RowsetReviewCounts, "approved" | "held">,
): string {
  return `${counts.approved} will apply · ${counts.held} held`;
}

export function rowsetResultSummary(
  counts: Pick<RowsetReviewCounts, "applied" | "held" | "failed">,
): string {
  const parts = [`${counts.applied} updated`];
  if (counts.failed > 0) parts.push(`${counts.failed} failed`);
  if (counts.held > 0) parts.push(`${counts.held} held, untouched`);
  return parts.join(" · ");
}

function normalizeDiff(change: LedgerRowChange): DiffValueModel {
  return {
    field: change.field,
    before: change.old,
    after: change.new,
  };
}

function rowOutcome(outcome: "applied" | "failed" | null): RowsetApplyOutcome {
  return outcome ?? "pending";
}

function statusMark(
  status: RowsetReviewStatus,
  recovery: boolean,
): ReviewStatusModel {
  if (status === "applied") {
    return { label: "applied", tone: "success" };
  }
  if (status === "corrupt") {
    return { label: "review unavailable", tone: "danger" };
  }
  if (status === "apply_pending") {
    return {
      label: recovery ? "retrying failed rows" : "applying approved rows",
      tone: "warning",
    };
  }
  if (recovery) {
    return { label: "partial · retry available", tone: "warning" };
  }
  return { label: "staged, not applied", tone: "warning" };
}

function actionMessage(
  kind: RowsetActionContext["kind"],
  notice: string | undefined,
): string {
  if (notice !== undefined && notice.trim() !== "") return notice;
  return kind === "retry_failed" ? rowsetRecoveryPledge : rowsetApplyPledge;
}

function defaultTitle(stage: LedgerStagedWrite, rowCount: number): string {
  const source =
    stage.target.connector.trim() !== ""
      ? stage.target.connector.trim()
      : "bulk";
  return `${rowCount} ${source} changes`;
}

/**
 * Normalize the durable ledger fold into the complete connector-independent
 * contract consumed by the row-set renderer.
 *
 * The exact action scope is derived once here and copied unchanged by the
 * command binder. Components never inspect ledger rows to rebuild it.
 */
export function projectRowsetReviewModel(
  stage: LedgerStagedWrite,
  options: RowsetReviewProjectionOptions = {},
): RowsetReviewModel {
  const pending = options.actionPending === true;
  const rows: readonly RowsetReviewRow[] = (stage.rows ?? []).map((row) => ({
    rowKey: row.rowKey,
    title: row.title,
    diffs: row.changes.map(normalizeDiff),
    decision: row.stance === "held" ? "held" : "approved",
    decidedBy: row.decidedBy,
    agentHoldReason: row.agentHoldReason,
    outcome: rowOutcome(row.applyOutcome),
    canDecide:
      stage.status === "staged" &&
      stage.applyResult === null &&
      row.applyOutcome === null &&
      !pending,
  }));
  const counts: RowsetReviewCounts = {
    total: rows.length,
    approved: rows.filter((row) => row.decision === "approved").length,
    held: rows.filter((row) => row.decision === "held").length,
    applied: rows.filter((row) => row.outcome === "applied").length,
    failed: rows.filter((row) => row.outcome === "failed").length,
    pending: rows.filter((row) => row.outcome === "pending").length,
  };

  // A partial apply and an all-failed first attempt are both recovery
  // presentations. The server still distinguishes their legal state when it
  // re-folds; in both cases the visible exact scope is every failed,
  // still-approved row.
  const recovery =
    stage.status === "partially_applied" ||
    stage.applyResult === "partial" ||
    stage.applyResult === "failed";
  const failedRowKeys = rows
    .filter((row) => row.decision === "approved" && row.outcome === "failed")
    .map((row) => row.rowKey);
  const freshRowKeys = rows
    .filter((row) => row.decision === "approved" && row.outcome === "pending")
    .map((row) => row.rowKey);
  const actionKeys = recovery ? failedRowKeys : freshRowKeys;
  const actionPending = pending || stage.status === "apply_pending";
  const actionKind: RowsetActionContext["kind"] = recovery
    ? "retry_failed"
    : "apply";
  const action: RowsetActionContext | null =
    stage.status === "applied" || stage.status === "corrupt"
      ? null
      : actionKind === "retry_failed"
        ? {
            kind: "retry_failed",
            stageId: stage.stageId,
            revision: stage.latestRev,
            proposalDigest: "",
            targetDigest: "",
            rowKeys: failedRowKeys,
            failedRowKeys,
            basisSequence: stage.lastSeq,
            basisLedgerId: stage.ledgerId,
            label: actionPending
              ? "Retrying…"
              : rowsetRetryLabel(failedRowKeys.length),
            message: actionMessage(actionKind, options.actionNotice),
            accessibleLabel: `Retry exactly ${failedRowKeys.length} failed rows`,
            pending: actionPending,
            disabled: actionPending || failedRowKeys.length === 0,
          }
        : {
            kind: "apply",
            stageId: stage.stageId,
            revision: stage.latestRev,
            proposalDigest: "",
            targetDigest: "",
            rowKeys: freshRowKeys,
            basisSequence: stage.lastSeq,
            basisLedgerId: null,
            label: actionPending
              ? "Applying…"
              : rowsetApplyLabel(freshRowKeys.length),
            message: actionMessage(actionKind, options.actionNotice),
            accessibleLabel: `Apply exactly ${freshRowKeys.length} approved rows`,
            pending: actionPending,
            disabled: actionPending || freshRowKeys.length === 0,
          };

  return {
    stageId: stage.stageId,
    surfaceId: stage.surfaceId,
    revision: stage.latestRev,
    proposalDigest: "",
    targetDigest: "",
    lastSequence: stage.lastSeq,
    status: stage.status,
    applyResult: stage.applyResult,
    title: options.title ?? defaultTitle(stage, rows.length),
    summary:
      options.summary ??
      (stage.status === "applied" || recovery
        ? rowsetResultSummary(counts)
        : rowsetCountsSummary(counts)),
    statusMark: statusMark(stage.status, recovery),
    rows,
    counts,
    action,
    provenance: {
      kind: "Table",
      source: stage.target.connector,
      operation: stage.target.op,
      policy: "per-row approval",
      ledgerId: stage.ledgerId,
    },
  };
}

/** Project the canonical owner-scoped review response into the shared UI model. */
export function projectCanonicalRowsetReviewModel(
  review: RowSetEffectReview,
  options: RowsetReviewProjectionOptions = {},
): RowsetReviewModel {
  const pending = options.actionPending === true;
  const rows: readonly RowsetReviewRow[] = review.rows.map((row) => ({
    rowKey: row.row_key,
    title: row.title,
    diffs: row.changes.map((change) => ({
      field: change.field,
      before: change.old,
      after: change.new,
    })),
    decision: row.decision === "hold" ? "held" : "approved",
    decidedBy: row.decision_source === "default" ? null : row.decision_source,
    agentHoldReason: row.hold_reason,
    outcome: row.apply_outcome ?? "pending",
    canDecide: row.can_decide && !pending,
  }));
  const counts: RowsetReviewCounts = {
    total: review.counts.total,
    approved: review.counts.approved,
    held: review.counts.held,
    applied: review.counts.applied,
    failed: review.counts.failed,
    pending: rows.filter((row) => row.outcome === "pending").length,
  };
  const recovery = review.status === "partial";
  const action =
    review.action === null
      ? null
      : review.action.kind === "retry_failed"
        ? ({
            kind: "retry_failed",
            stageId: review.stage_id,
            revision: review.revision,
            proposalDigest: review.proposal_digest,
            targetDigest: review.target_digest,
            rowKeys: review.action.row_keys,
            failedRowKeys: review.action.row_keys,
            basisSequence: review.action.basis_sequence_no,
            basisLedgerId: review.action.basis_ledger_id,
            label: pending
              ? "Retrying…"
              : rowsetRetryLabel(review.action.row_keys.length),
            message: actionMessage("retry_failed", options.actionNotice),
            accessibleLabel: `Retry exactly ${review.action.row_keys.length} failed rows`,
            pending,
            disabled: pending || review.action.row_keys.length === 0,
          } satisfies RecoveryContext)
        : ({
            kind: "apply",
            stageId: review.stage_id,
            revision: review.revision,
            proposalDigest: review.proposal_digest,
            targetDigest: review.target_digest,
            rowKeys: review.action.row_keys,
            basisSequence: review.action.basis_sequence_no,
            basisLedgerId: null,
            label: pending
              ? "Applying…"
              : rowsetApplyLabel(review.action.row_keys.length),
            message: actionMessage("apply", options.actionNotice),
            accessibleLabel: `Apply exactly ${review.action.row_keys.length} approved rows`,
            pending,
            disabled: pending || review.action.row_keys.length === 0,
          } satisfies ApplyContext);
  return {
    stageId: review.stage_id,
    surfaceId: `effect-stage://${encodeURIComponent(review.stage_id)}`,
    revision: review.revision,
    proposalDigest: review.proposal_digest,
    targetDigest: review.target_digest,
    lastSequence: review.last_sequence_no,
    status: review.status,
    applyResult:
      review.status === "partial"
        ? "partial"
        : review.status === "applied"
          ? "applied"
          : null,
    title: options.title ?? review.title,
    summary:
      options.summary ??
      (review.status === "applied" || recovery
        ? rowsetResultSummary(counts)
        : rowsetCountsSummary(counts)),
    statusMark: statusMark(review.status, recovery),
    rows,
    counts,
    action,
    provenance: {
      kind: "Table",
      source: review.source_connector,
      operation: review.source_op,
      policy: "per-row approval",
      ledgerId: review.ledger_id,
    },
  };
}
