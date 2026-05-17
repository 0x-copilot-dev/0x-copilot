// <InboxReply /> — inline reply composer for the Inbox detail pane.
//
// Source: inbox-prd.md §3.4 ("Inline reply composer" sub-region) + §11
// (telemetry `action=reply  routed_to=<existing-thread|new-thread>`).
//
// This is a *thin* wrapper around the shared chat-surface Composer in
// `mode="compose"` — there is one and only one Composer in the codebase,
// and Inbox reply is just another call site. The wrapper:
//
//   1. Pre-populates the placeholder with the sender display name (so the
//      caller doesn't repeat the "Reply to <sender>…" string everywhere).
//   2. Translates the Composer's `onSubmit` payload into a tighter
//      `onReply({ text, routedTo })` shape — the routing decision (reply
//      lands in an existing `thread_id` or spawns a new one) is a *display*
//      decision derived from `threadId` so the host can audit it without
//      knowing the Composer's internals.
//   3. Disables itself for connector-error rows. Per cross-audit §9.3,
//      reply-to-error opens the connectors-repair flow as the primary
//      action — text replies are routed comment-only to the connector
//      owner. Hosts that wire the repair flow pass `kind="error"` and
//      omit `onReply` (or pass `disabled`) to lock the textarea.

import { useCallback, type ReactElement } from "react";

import { Composer, type ComposerSubmitPayload } from "../../composer/Composer";

export type InboxReplyRouting = "existing-thread" | "new-thread";

export interface InboxReplyPayload {
  readonly text: string;
  readonly routedTo: InboxReplyRouting;
}

export interface InboxReplyProps {
  /**
   * Sender display name. Used only to seed the placeholder ("Reply to …").
   * Pure presentation — the actual recipient is derived server-side from
   * the inbox item's `sender` field (see inbox-prd.md §4.1).
   */
  readonly senderLabel: string;
  /**
   * If present the reply lands in that thread; if `undefined` the host
   * is expected to create a new thread. Drives the `routedTo` value
   * emitted on submit and (in display) the hint copy.
   */
  readonly threadId?: string;
  /**
   * Submit handler. Receives the trimmed reply text + the routing
   * outcome. The host owns the POST /v1/inbox/{id}/reply call.
   */
  readonly onReply?: (payload: InboxReplyPayload) => void;
  /**
   * Disabled state — used for connector-error rows where reply is not
   * the primary action (cross-audit §9.3) and for rows whose previous
   * reply is still in flight. The Composer's `disabled` prop is
   * forwarded; the textarea stays visible so the user knows the
   * affordance exists.
   */
  readonly disabled?: boolean;
}

export function InboxReply({
  senderLabel,
  threadId,
  onReply,
  disabled = false,
}: InboxReplyProps): ReactElement {
  const routedTo: InboxReplyRouting =
    threadId !== undefined ? "existing-thread" : "new-thread";

  const handleSubmit = useCallback(
    (payload: ComposerSubmitPayload): void => {
      // Empty submissions never reach here — Composer guards on `text.trim()`
      // before invoking `onSubmit` (see Composer.tsx::send).
      onReply?.({ text: payload.text, routedTo });
    },
    [onReply, routedTo],
  );

  // Effective disabled = caller-disabled OR no handler wired. The Composer
  // already short-circuits on `disabled`, but exposing the handle here
  // lets us also keep the placeholder honest for connector-error rows
  // (where reply lands as a comment to the connector owner, not the
  // failed-run agent).
  const isDisabled = disabled || onReply === undefined;
  const placeholder = `Reply to ${senderLabel}…`;

  return (
    <div data-testid="inbox-reply" data-routed-to={routedTo}>
      <Composer
        mode="compose"
        onSubmit={handleSubmit}
        disabled={isDisabled}
        placeholder={placeholder}
      />
    </div>
  );
}
