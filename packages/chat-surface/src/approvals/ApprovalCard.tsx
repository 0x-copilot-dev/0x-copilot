// Purpose-built consent card for MCP / action approvals (PR-1.6, moved from
// apps/frontend/.../activity/ApprovalCard.tsx).
//
// The design stripes the card into 4 zones the eye can scan in order:
//
//   1. Header  — shield/icon · title · subtitle (the *why*) · vendor pill
//   2. Params  — inset framed key/value table (channel, visibility, …)
//   3. Actions — primary (Approve & continue) + ghost (Skip) + Forward
//   4. Footer  — persistent rule line ("You're always asked before
//                Copilot writes outside this chat.")
//
// ApprovalCard is **presentational only** — all state (resolved /
// forwarded / chain-final / cancelled) is collapsed into the
// ``ApprovalReceipt`` sibling, so this card only renders the
// "user must decide" path. The Approve / Reject / Forward controls are
// supplied by the host as the ``actions`` node (D28 pure-render rule):
// chat-surface renders the frame; the host owns the callbacks.

import { Card, classNames } from "@0x-copilot/design-system";
import type { ReactElement, ReactNode } from "react";
import { ActivityDetails } from "./ActivityDetails";
import { ActivityParams } from "./ActivityParams";
import type { ActivityParam } from "./types";

export interface ApprovalCardProps {
  /** "Search your Linear issues?" — verb-first, sentence case. */
  title: string;
  /** "Copilot is asking because this writes outside your workspace." */
  reason: ReactNode;
  /** {vendor: "LINEAR", access: "READ" | "WRITE" | "ACTION"}. */
  category?: { vendor: string; access: string } | null;
  /** Inset key/value frame. Empty list → no frame. */
  params?: ActivityParam[];
  /** Optional preview rows above the action row (search results etc). */
  result?: ReactNode;
  /** Action row buttons. Caller controls primary/secondary hierarchy. */
  actions: ReactNode;
  /** Persistent rule line. Renders with a small shield glyph. */
  reassurance: string;
  /** Tool details collapsible — debugger surface. */
  details?: ReactNode;
  detailsLabel?: string;
  className?: string;
}

export function ApprovalCard({
  title,
  reason,
  category = null,
  params = [],
  result,
  actions,
  reassurance,
  details,
  detailsLabel = "Tool details",
  className,
}: ApprovalCardProps): ReactElement {
  return (
    <Card
      className={classNames("atlas-approval-card", className)}
      data-status="waiting"
    >
      <header className="atlas-approval-card__head">
        <span className="atlas-approval-card__icon" aria-hidden="true">
          <ShieldGlyph />
        </span>
        <div className="atlas-approval-card__heading">
          <span className="atlas-approval-card__title">{title}</span>
          <p className="atlas-approval-card__reason">{reason}</p>
        </div>
        {category ? (
          <span
            className="atlas-approval-card__pill"
            aria-label={`${category.vendor} ${category.access}`}
          >
            <span className="atlas-approval-card__pill-vendor">
              {category.vendor}
            </span>
            <span className="atlas-approval-card__pill-sep" aria-hidden="true">
              ·
            </span>
            <span className="atlas-approval-card__pill-access">
              {category.access}
            </span>
          </span>
        ) : null}
      </header>

      {params.length > 0 ? (
        <div className="atlas-approval-card__params">
          <ActivityParams params={params} />
        </div>
      ) : null}

      {result ? (
        <div className="atlas-approval-card__result">{result}</div>
      ) : null}

      <div className="atlas-approval-card__actions">{actions}</div>

      <footer className="atlas-approval-card__foot">
        <span className="atlas-approval-card__foot-icon" aria-hidden="true">
          <ShieldGlyph />
        </span>
        <span>{reassurance}</span>
      </footer>

      {details ? (
        <ActivityDetails label={detailsLabel}>{details}</ActivityDetails>
      ) : null}
    </Card>
  );
}

function ShieldGlyph(): ReactElement {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M8 1.5 2.5 3.75v3.5c0 3.25 2.25 6.25 5.5 7.25 3.25-1 5.5-4 5.5-7.25v-3.5L8 1.5Z" />
    </svg>
  );
}
