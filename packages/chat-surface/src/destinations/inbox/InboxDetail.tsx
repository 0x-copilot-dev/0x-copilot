// <InboxDetail /> — Inbox detail pane (the right-hand view when an inbox
// row is selected, or the full-pane view below the 960px breakpoint).
//
// Source:
//   docs/atlas-new-design/destinations/inbox-prd.md §8 / §3.4 layout
//   docs/atlas-new-design/destinations/inbox-prd.md §3.6 (snooze options
//     surface in the action row)
//   docs/atlas-new-design/destinations/inbox-prd.md §11 (reply telemetry)
//   docs/atlas-new-design/cross-audit.md §9.3 — Inbox Q7 reply-to-error
//     opens the connectors-repair flow as the primary action.
//
// Invariants:
//   - Pure presentation. Every side-effect (PATCH/POST/DELETE) lands
//     through a callback prop; the host owns the transport call.
//   - Cross-destination links render through <ItemLink>. Direct
//     router.navigate() from this file is forbidden (cross-audit §1.1 +
//     §3.3 binding).
//   - The reply composer is the shared chat-surface Composer in
//     mode="compose" via <InboxReply>. No duplicate composer.
//   - The body is fetched separately via `body_ref` (inbox-prd.md §5
//     "list endpoint never returns body bytes"). This component accepts
//     a `bodyState` prop describing where the lazy fetch stands; the
//     host wires the actual GET /v1/inbox/{id} call.

import {
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { InboxItemId, ItemRef } from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";

import { InboxReply, type InboxReplyPayload } from "./inbox-reply";
import { SnoozePicker } from "./snooze-picker";

/**
 * Display-layer kinds. Matches inbox-prd.md §4.1 `InboxItemKind` —
 * mirrored locally (not imported) because the api-types contract for
 * the rich Inbox payload is added by a separate task (the existing
 * `InboxDestination` ships a stripped list-row contract and the wire
 * types land alongside the backend producer migration).
 */
export type InboxDetailItemKind =
  | "mention"
  | "approval_request"
  | "error"
  | "system";

export type InboxDetailStatus = "unread" | "read" | "done" | "snoozed";

export type InboxDetailPriority = "low" | "med" | "high";

/**
 * Sender description. Matches inbox-prd.md §4.1 `InboxSender` shape; we
 * accept the discriminated union so the host can pass it through
 * verbatim once the wire types land. For now we only display the label
 * and an optional kind hint.
 */
export interface InboxDetailSender {
  readonly kind: "user" | "agent" | "system";
  readonly label: string;
}

/**
 * The item header carries everything the row knew about the inbox row
 * *without* needing the body. Mirrors inbox-prd.md §4.1 `InboxItem`
 * minus `body_ref` (which is the lazy-fetch handle held by the host).
 */
export interface InboxDetailItem {
  readonly id: InboxItemId;
  readonly kind: InboxDetailItemKind;
  readonly subject: string;
  readonly sender: InboxDetailSender;
  readonly recipientLabel?: string;
  readonly receivedAt: string;
  readonly status: InboxDetailStatus;
  readonly priority: InboxDetailPriority;
  readonly labels?: ReadonlyArray<string>;
  readonly threadId?: string;
  /**
   * Cross-destination references that should render as <ItemLink>s in
   * the header (e.g. the originating run, the linked approval, the
   * project). Order is preserved.
   */
  readonly links?: ReadonlyArray<ItemRef>;
}

/**
 * Body lazy-fetch state. The host owns the GET /v1/inbox/{id} call and
 * passes the result back as one of these states; the detail pane only
 * renders. Markdown rendering is out-of-scope for this primitive (the
 * existing chat-surface markdown renderer is reused at the host level
 * — pass it in via `renderBody`).
 */
export type InboxDetailBodyState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly body: string };

export interface InboxDetailProps {
  readonly item: InboxDetailItem;
  readonly bodyState: InboxDetailBodyState;
  /**
   * Optional custom body renderer — the chat-surface markdown primitive
   * is the obvious caller, but a plain-text host is fine too. Default
   * renders the body as a `<pre>` block; do NOT use this for trusted
   * markdown.
   */
  readonly renderBody?: (body: string) => ReactNode;

  // --- Action callbacks (all optional; missing → button hidden) ---
  readonly onBack?: () => void;
  readonly onMarkRead?: (id: InboxItemId) => void;
  readonly onSnooze?: (id: InboxItemId, until: string) => void;
  readonly onReply?: (id: InboxItemId, payload: InboxReplyPayload) => void;
  readonly onDismiss?: (id: InboxItemId) => void;
  readonly onRetryBody?: () => void;

  /**
   * Pending action ids. Drives button-level disabled/pending state.
   * The host owns the actual in-flight tracking; we only render.
   */
  readonly pending?: ReadonlySet<"mark-read" | "snooze" | "dismiss">;
}

function kindLabel(kind: InboxDetailItemKind): string {
  if (kind === "mention") return "Mention";
  if (kind === "approval_request") return "Approval";
  if (kind === "error") return "Error";
  return "System";
}

function kindTone(kind: InboxDetailItemKind): StatusTone {
  if (kind === "mention") return "info";
  if (kind === "approval_request") return "warning";
  if (kind === "error") return "error";
  return "muted";
}

function priorityTone(priority: InboxDetailPriority): StatusTone {
  if (priority === "high") return "error";
  if (priority === "med") return "warning";
  return "muted";
}

function statusTone(status: InboxDetailStatus): StatusTone {
  if (status === "unread") return "info";
  if (status === "snoozed") return "warning";
  if (status === "done") return "ok";
  return "muted";
}

function statusLabel(status: InboxDetailStatus): string {
  if (status === "unread") return "Unread";
  if (status === "snoozed") return "Snoozed";
  if (status === "done") return "Done";
  return "Read";
}

export function InboxDetail({
  item,
  bodyState,
  renderBody,
  onBack,
  onMarkRead,
  onSnooze,
  onReply,
  onDismiss,
  onRetryBody,
  pending,
}: InboxDetailProps): ReactElement {
  const [snoozeOpen, setSnoozeOpen] = useState(false);

  const isPending = (slug: "mark-read" | "snooze" | "dismiss"): boolean =>
    pending !== undefined && pending.has(slug);

  // Cross-audit §9.3 — reply-to-error is comment-only; the primary
  // affordance is the connectors-repair flow (wired by the host). We
  // disable the reply textarea for error rows so the user is steered
  // toward the repair button instead of typing into a dead-end box.
  const replyDisabled = item.kind === "error";

  const handleSnoozePicked = (iso: string): void => {
    setSnoozeOpen(false);
    onSnooze?.(item.id, iso);
  };

  const handleReply = (payload: InboxReplyPayload): void => {
    onReply?.(item.id, payload);
  };

  return (
    <article
      aria-label={`Inbox item — ${item.subject}`}
      data-testid="inbox-detail"
      data-item-kind={item.kind}
      data-item-status={item.status}
      style={rootStyle}
    >
      {onBack !== undefined ? (
        <button
          type="button"
          onClick={onBack}
          style={backButtonStyle}
          data-testid="inbox-detail-back"
          aria-label="Back to inbox"
        >
          ← Back to inbox
        </button>
      ) : null}

      {/* --- Header --- */}
      <header style={headerStyle} data-testid="inbox-detail-header">
        <div style={senderRowStyle}>
          <span style={senderLabelStyle} data-testid="inbox-detail-sender">
            {item.sender.label}
          </span>
          {item.recipientLabel !== undefined ? (
            <>
              <span style={arrowStyle} aria-hidden="true">
                →
              </span>
              <span
                style={recipientLabelStyle}
                data-testid="inbox-detail-recipient"
              >
                {item.recipientLabel}
              </span>
            </>
          ) : null}
        </div>
        <h1 style={subjectStyle} data-testid="inbox-detail-subject">
          {item.subject}
        </h1>
        <div style={chipRowStyle} data-testid="inbox-detail-chips">
          <StatusPill
            status={kindTone(item.kind)}
            label={kindLabel(item.kind)}
          />
          <StatusPill
            status={priorityTone(item.priority)}
            label={`${item.priority} priority`}
          />
          <StatusPill
            status={statusTone(item.status)}
            label={statusLabel(item.status)}
          />
          {(item.labels ?? []).map((label) => (
            <StatusPill key={label} status="muted" label={label} />
          ))}
        </div>
        <div style={timeRowStyle}>
          <time dateTime={item.receivedAt} data-testid="inbox-detail-time">
            {item.receivedAt}
          </time>
        </div>
        {item.links !== undefined && item.links.length > 0 ? (
          <div
            style={linksRowStyle}
            data-testid="inbox-detail-links"
            aria-label="Related items"
          >
            {item.links.map((linkRef) => (
              <ItemLink key={`${linkRef.kind}:${linkRef.id}`} ref={linkRef} />
            ))}
          </div>
        ) : null}

        {/* --- Actions row --- */}
        <div style={actionsRowStyle} role="group" aria-label="Item actions">
          {onMarkRead !== undefined ? (
            <button
              type="button"
              onClick={() => onMarkRead(item.id)}
              disabled={isPending("mark-read")}
              style={actionButtonStyle(isPending("mark-read"))}
              data-testid="inbox-detail-mark-read"
            >
              {isPending("mark-read") ? "Marking…" : "Mark read"}
            </button>
          ) : null}
          {onSnooze !== undefined ? (
            <button
              type="button"
              onClick={() => setSnoozeOpen((v) => !v)}
              disabled={isPending("snooze")}
              aria-expanded={snoozeOpen}
              aria-haspopup="dialog"
              style={actionButtonStyle(isPending("snooze"))}
              data-testid="inbox-detail-snooze"
            >
              Snooze
            </button>
          ) : null}
          {onDismiss !== undefined ? (
            <button
              type="button"
              onClick={() => onDismiss(item.id)}
              disabled={isPending("dismiss")}
              style={dangerActionButtonStyle(isPending("dismiss"))}
              data-testid="inbox-detail-dismiss"
            >
              Dismiss
            </button>
          ) : null}
        </div>
        {snoozeOpen ? (
          <div style={snoozePopoverStyle}>
            <SnoozePicker
              onSnooze={handleSnoozePicked}
              onCancel={() => setSnoozeOpen(false)}
              disabled={isPending("snooze")}
            />
          </div>
        ) : null}
      </header>

      {/* --- Body (lazy-fetched via body_ref) --- */}
      <section
        style={bodySectionStyle}
        data-testid="inbox-detail-body"
        data-body-state={bodyState.kind}
        aria-busy={bodyState.kind === "loading"}
      >
        {bodyState.kind === "idle" ? (
          <div style={bodyMutedStyle}>Body not yet loaded.</div>
        ) : null}
        {bodyState.kind === "loading" ? (
          <div style={bodyMutedStyle} role="status">
            Loading body…
          </div>
        ) : null}
        {bodyState.kind === "error" ? (
          <div role="alert" style={bodyErrorStyle}>
            <span>Could not load body: {bodyState.message}</span>
            {onRetryBody !== undefined ? (
              <button
                type="button"
                onClick={onRetryBody}
                style={retryStyle}
                data-testid="inbox-detail-body-retry"
              >
                Retry
              </button>
            ) : null}
          </div>
        ) : null}
        {bodyState.kind === "ready" ? (
          renderBody !== undefined ? (
            renderBody(bodyState.body)
          ) : (
            <pre style={bodyPreStyle}>{bodyState.body}</pre>
          )
        ) : null}
      </section>

      {/* --- Reply composer --- */}
      <section
        style={replySectionStyle}
        data-testid="inbox-detail-reply"
        aria-label="Reply"
      >
        <InboxReply
          senderLabel={item.sender.label}
          threadId={item.threadId}
          onReply={onReply !== undefined ? handleReply : undefined}
          disabled={replyDisabled}
        />
        {replyDisabled ? (
          <div style={replyHintStyle} data-testid="inbox-detail-reply-hint">
            Connector errors open the connectors-repair flow — replies route as
            a comment to the connector owner.
          </div>
        ) : null}
      </section>
    </article>
  );
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  width: "100%",
  maxWidth: 880,
  margin: "0 auto",
  padding: "16px 20px 32px",
  boxSizing: "border-box",
  color: "var(--color-text)",
};

const backButtonStyle: CSSProperties = {
  alignSelf: "flex-start",
  height: 28,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-xs)",
  cursor: "pointer",
};

const headerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "16px",
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  position: "relative",
};

const senderRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const senderLabelStyle: CSSProperties = {
  fontWeight: 600,
  color: "var(--color-text)",
};

const recipientLabelStyle: CSSProperties = {
  color: "var(--color-text-muted)",
};

const arrowStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
};

const subjectStyle: CSSProperties = {
  fontSize: "var(--font-size-xl)",
  fontWeight: 700,
  margin: 0,
  lineHeight: 1.3,
};

const chipRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: 6,
};

const timeRowStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-subtle)",
};

const linksRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: 8,
  paddingTop: 4,
};

const actionsRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  paddingTop: 6,
};

const actionButtonStyle = (pendingState: boolean): CSSProperties => ({
  height: 30,
  padding: "0 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: pendingState ? "default" : "pointer",
  opacity: pendingState ? 0.6 : 1,
});

const dangerActionButtonStyle = (pendingState: boolean): CSSProperties => ({
  height: 30,
  padding: "0 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-danger)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: pendingState ? "default" : "pointer",
  opacity: pendingState ? 0.6 : 1,
});

const snoozePopoverStyle: CSSProperties = {
  paddingTop: 6,
};

const bodySectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  padding: "16px",
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  minHeight: 80,
};

const bodyMutedStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-sm)",
};

const bodyErrorStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  color: "var(--color-danger)",
  fontSize: "var(--font-size-sm)",
};

const retryStyle: CSSProperties = {
  height: 26,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
};

const bodyPreStyle: CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontFamily: "inherit",
  fontSize: "var(--font-size-sm)",
  lineHeight: 1.5,
  color: "var(--color-text)",
};

const replySectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const replyHintStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-subtle)",
  paddingLeft: 2,
};
