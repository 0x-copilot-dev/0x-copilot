// PR 3.2 — Approvals tab body for the right-rail workspace pane.
// PR-1.7 — hoisted into @0x-copilot/chat-surface with the pane it serves.
//
// Pure projection over the existing thread items via
// `useApprovalsQueue` (PR 3.2, host-owned). Clicking a row jumps to the
// inline <ApprovalTool> card in the thread (Atlas's "approvals as
// content" rule) via the host-supplied `onJumpToApproval`. PR 3.3 will
// split `pending` into pending-on-me vs. pending-on-others; until then
// this surfaces a single pending list plus a small recent-resolutions
// section.

import { Badge, Card, classNames } from "@0x-copilot/design-system";
import type { ReactElement } from "react";

import type { ApprovalsQueueItem, ApprovalsQueueProjection } from "./types";

export interface ApprovalsTabProps {
  queue: ApprovalsQueueProjection;
  onJumpToApproval?: (approvalId: string, messageId: string) => void;
}

export function ApprovalsTab({
  queue,
  onJumpToApproval,
}: ApprovalsTabProps): ReactElement {
  const { pending, recent } = queue;
  if (pending.length === 0 && recent.length === 0) {
    return (
      <div
        className="atlas-workspace-tab atlas-workspace-tab--empty"
        data-testid="workspace-approvals-tab-empty"
      >
        <p>No pending approvals in this conversation.</p>
      </div>
    );
  }

  return (
    <div className="atlas-workspace-tab" data-testid="workspace-approvals-tab">
      {pending.length > 0 ? (
        <Section
          title="Pending"
          description={
            pending.length === 1
              ? "Copilot is waiting on you."
              : `Atlas is waiting on ${pending.length} decisions.`
          }
          items={pending}
          onJumpToApproval={onJumpToApproval}
        />
      ) : null}
      {recent.length > 0 ? (
        <Section
          title="Recent"
          description="Resolved within the last hour."
          items={recent}
          onJumpToApproval={onJumpToApproval}
        />
      ) : null}
    </div>
  );
}

function Section({
  title,
  description,
  items,
  onJumpToApproval,
}: {
  title: string;
  description: string;
  items: readonly ApprovalsQueueItem[];
  onJumpToApproval?: (approvalId: string, messageId: string) => void;
}): ReactElement {
  return (
    <section className="atlas-workspace-approvals-section">
      <header>
        <h3>{title}</h3>
        <p>{description}</p>
      </header>
      <ul
        className="atlas-workspace-tab__list"
        aria-label={`${title} approvals`}
      >
        {items.map((item) => (
          <li
            key={`${item.approvalId}-${item.messageId}`}
            className={classNames(
              "atlas-workspace-tab__item",
              item.resolved && "atlas-workspace-tab__item--resolved",
            )}
            data-approval-id={item.approvalId}
          >
            <Card>
              <button
                type="button"
                className="atlas-workspace-approvals-row"
                onClick={() =>
                  onJumpToApproval?.(item.approvalId, item.messageId)
                }
                aria-label={`Open approval "${item.title}" in thread`}
              >
                <div className="atlas-workspace-approvals-row__head">
                  <Badge tone={item.resolved ? "success" : "accent"}>
                    {kindLabel(item.approvalKind)}
                  </Badge>
                  <span className="atlas-workspace-approvals-row__title">
                    {item.title}
                  </span>
                  {item.target ? (
                    <Badge tone="neutral">{item.target}</Badge>
                  ) : null}
                </div>
                {item.summary ? (
                  <p className="atlas-workspace-approvals-row__summary">
                    {item.summary}
                  </p>
                ) : null}
                {item.resolved && item.resolvedAt ? (
                  <p className="atlas-workspace-approvals-row__resolved">
                    Resolved {formatRelative(item.resolvedAt)}
                  </p>
                ) : null}
              </button>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
}

function kindLabel(kind: ApprovalsQueueItem["approvalKind"]): string {
  switch (kind) {
    case "mcp_auth":
      return "Connector";
    case "mcp_tool":
      return "Connector tool";
    case "ask_a_question":
      return "Question";
    case "tool_action":
      return "Action";
    default:
      return "Approval";
  }
}

function formatRelative(iso: string): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) {
    return iso;
  }
  const diff = Date.now() - ms;
  if (diff < 60_000) {
    return "just now";
  }
  if (diff < 60 * 60_000) {
    const minutes = Math.floor(diff / 60_000);
    return `${minutes}m ago`;
  }
  return new Date(ms).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}
