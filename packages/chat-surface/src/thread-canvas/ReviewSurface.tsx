import type { ReactElement, ReactNode } from "react";

import { Badge } from "@0x-copilot/design-system";

import type {
  ApplyContext,
  DiffValueModel,
  RecoveryContext,
  ReviewProvenance,
  ReviewStatusModel,
  RowsetActionContext,
  RowsetReviewRow,
} from "./rowsetReviewModel";

export interface ReviewSurfaceProps {
  readonly children: ReactNode;
  readonly kind: "rowset";
  readonly testId?: string;
  readonly state?: string;
}

/** Bounded review shell. Its middle body is the only region allowed to scroll. */
export function ReviewSurface({
  children,
  kind,
  testId,
  state,
}: ReviewSurfaceProps): ReactElement {
  return (
    <section
      className={`tc-review-surface tc-review-surface--${kind}`}
      data-testid={testId}
      data-state={state}
    >
      {children}
    </section>
  );
}

export interface ReviewHeaderProps {
  readonly title: string;
  readonly status: ReviewStatusModel;
  readonly summary: string;
}

export function ReviewHeader({
  title,
  status,
  summary,
}: ReviewHeaderProps): ReactElement {
  return (
    <header className="tc-review-table__header">
      <span
        className="tc-review-table__title-heading"
        data-testid="tc-staged-table-connector"
        title={title}
      >
        {title}
      </span>
      <Badge tone={status.tone} data-testid="tc-staged-table-access">
        {status.label}
      </Badge>
      <span
        className="tc-review-table__counts"
        data-testid="tc-staged-table-counts"
        title={summary}
      >
        {summary}
      </span>
    </header>
  );
}

export interface ReviewTableProps {
  readonly label: string;
  readonly columns: readonly string[];
  readonly rowCount: number;
  readonly children: ReactNode;
}

/**
 * The single row-set overflow owner. The viewport scrolls in both axes while
 * the surrounding surface header/action/footer remain fixed.
 */
export function ReviewTable({
  label,
  columns,
  rowCount,
  children,
}: ReviewTableProps): ReactElement {
  return (
    <div
      className="tc-review-table__viewport"
      data-testid="tc-review-table-viewport"
      role="region"
      aria-label={label}
      tabIndex={0}
    >
      <div
        className="tc-review-table"
        role="table"
        aria-rowcount={rowCount + 1}
        aria-colcount={columns.length}
      >
        <div className="tc-review-table__columns" role="row">
          {columns.map((column) => (
            <span key={column} role="columnheader">
              {column}
            </span>
          ))}
        </div>
        {children}
      </div>
    </div>
  );
}

function reviewValue(value: unknown): string {
  if (value === null || value === undefined) return "∅";
  if (typeof value === "string") return value;
  try {
    const encoded = JSON.stringify(value);
    return encoded ?? String(value);
  } catch {
    return String(value);
  }
}

export interface DiffValueProps {
  readonly diffs: readonly DiffValueModel[];
  readonly side: "before" | "after";
}

/** What the reviewer is told about each value's author. `carried` and
 *  `proposed` are the two the old diff-only review never printed at all. */
const ORIGIN_NOTE: Readonly<Record<DiffValueModel["origin"], string>> = {
  edited: "you edited",
  carried: "sending unchanged",
  proposed: "agent wrote",
};

/**
 * The outbound side of a staged row.
 *
 * `diffs` is the row's COMPLETE outbound account — one entry per connector
 * argument, in wire order — not the subset the user touched. Every entry is
 * rendered: dropping the unedited ones is what let a recipient, a subject and a
 * model-authored body ship under a one-line "cc changed" review. Each value
 * carries who authored it, so "unchanged" and "the agent wrote this" are never
 * the same line.
 */
export function DiffValue({ diffs, side }: DiffValueProps): ReactElement {
  if (side === "before") {
    return (
      <div
        className="tc-review-table__old"
        data-testid="tc-table-row-old"
        role="cell"
      >
        {diffs.map((diff, index) => (
          <span key={`before-${diff.field}-${index}`} data-origin={diff.origin}>
            {reviewValue(diff.before)}
          </span>
        ))}
      </div>
    );
  }
  return (
    <div
      className="tc-review-table__change"
      data-testid="tc-table-row-change"
      role="cell"
    >
      {diffs.map((diff, index) => (
        <span
          key={`after-${diff.field}-${index}`}
          data-origin={diff.origin}
          data-testid="tc-table-row-send"
          data-arg={diff.field}
        >
          <small>{diff.field}</small>
          <span
            className="tc-review-table__change-value"
            data-testid="tc-table-row-change-value"
          >
            {reviewValue(diff.after)}
          </span>
          <small
            className="tc-review-table__send-origin"
            data-testid="tc-table-row-send-origin"
          >
            {ORIGIN_NOTE[diff.origin]}
          </small>
        </span>
      ))}
    </div>
  );
}

function OutcomeIcon({ failed }: { readonly failed: boolean }): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      width="11"
      height="11"
      fill="none"
      stroke="currentColor"
      strokeWidth={failed ? 2.2 : 2.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {failed ? <path d="M18 6 6 18M6 6l12 12" /> : <path d="M5 12l5 5L20 7" />}
    </svg>
  );
}

export function StatusMark({
  row,
}: {
  readonly row: RowsetReviewRow;
}): ReactElement {
  const outcomeLabel =
    row.outcome === "applied"
      ? "updated"
      : row.outcome === "failed"
        ? "failed"
        : row.decision === "held"
          ? "held"
          : "approved";
  return (
    <div className="tc-review-table__status" role="cell">
      {row.outcome !== "pending" ? (
        <span
          className={`tc-review-table__outcome tc-review-table__outcome--${row.outcome}`}
          data-testid="tc-table-row-outcome"
        >
          <OutcomeIcon failed={row.outcome === "failed"} />
          {outcomeLabel}
        </span>
      ) : (
        <Badge
          tone={row.decision === "held" ? "warning" : "neutral"}
          data-testid={
            row.decision === "held"
              ? "tc-table-row-held"
              : "tc-table-row-will-apply"
          }
        >
          {outcomeLabel}
        </Badge>
      )}
    </div>
  );
}

function ActionIcon({
  recovery,
}: {
  readonly recovery: boolean;
}): ReactElement {
  return (
    <svg
      className="tc-review-action-bar__icon"
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {recovery ? (
        <>
          <path d="M20 11a8.1 8.1 0 1 0-2.4 5.8" />
          <path d="M20 4v7h-7" />
        </>
      ) : (
        <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
      )}
    </svg>
  );
}

interface ActionBarProps<TAction extends RowsetActionContext> {
  readonly action: TAction;
  readonly onAction: (action: TAction) => void;
}

function ActionBar<TAction extends RowsetActionContext>({
  action,
  onAction,
}: ActionBarProps<TAction>): ReactElement {
  const recovery = action.kind === "retry_failed";
  return (
    <div
      className={`tc-review-action-bar ${
        recovery
          ? "tc-review-action-bar--recovery"
          : "tc-review-action-bar--approval"
      }`}
      data-testid="tc-bulk-apply-bar"
      data-mode={recovery ? "recovery" : "apply"}
      aria-busy={action.pending}
    >
      <ActionIcon recovery={recovery} />
      <span className="tc-review-action-bar__copy" data-testid="tc-bulk-pledge">
        {action.message}
      </span>
      <button
        type="button"
        className="ui-button ui-button--sm ui-button--primary"
        disabled={action.disabled}
        onClick={() => onAction(action)}
        aria-label={action.accessibleLabel}
        data-testid={recovery ? "tc-bulk-retry" : "tc-bulk-apply"}
      >
        {action.label}
      </button>
    </div>
  );
}

export function DecisionBar({
  action,
  onAction,
}: ActionBarProps<ApplyContext>): ReactElement {
  return <ActionBar action={action} onAction={onAction} />;
}

export function RecoveryBar({
  action,
  onAction,
}: ActionBarProps<RecoveryContext>): ReactElement {
  return <ActionBar action={action} onAction={onAction} />;
}

export function ReviewFooter({
  provenance,
}: {
  readonly provenance: ReviewProvenance;
}): ReactElement {
  const sourceOperation = [provenance.source, provenance.operation]
    .filter((part) => part.trim() !== "")
    .join(".");
  const detail = [sourceOperation, provenance.policy, provenance.ledgerId]
    .filter((part) => part !== "")
    .join(" · ");
  return (
    <footer className="tc-review-provenance">
      <span className="tc-review-provenance__kind">{provenance.kind}</span>
      <span
        className="tc-review-provenance__detail"
        data-testid="tc-staged-table-ledger-id"
        title={detail}
      >
        {detail}
      </span>
    </footer>
  );
}
