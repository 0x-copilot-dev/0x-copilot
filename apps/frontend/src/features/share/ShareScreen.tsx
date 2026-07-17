// PR 6.1/6.2 — recipient view of a shared conversation.
//
// Routed at ``/share/:token``. The token is the access grant; the
// session is still required (v1 keeps shares same-org-only). Two
// fetches in sequence:
//
//   1. ``GET /v1/agent/shares/{token}/preview`` — light preview gate.
//      Surfaces the design's "Source restricted" / "share has been
//      revoked" / "expired" copy without paying for the full snapshot.
//
//   2. ``GET /v1/agent/shares/{token}`` — full read-only snapshot
//      (messages + sources + drafts + subagents). Loads only when the
//      preview says ``can_view: true``.
//
// Source-restriction UX: when ``share.sources_visible_to_viewer`` is
// false, the backend strips source bodies from the payload — but the
// citation chips in the assistant prose still appear (the backend
// preserves the chip *count*; the recipient sees that work was cited
// even if the underlying snippet is hidden). The FE renders those
// chips as a non-link "Source restricted" tag.
//
// Fork-to-my-chat: ``POST /v1/agent/shares/{token}/fork`` mints a new
// conversation owned by the recipient, copies the snapshot's messages
// (clamped server-side), and returns the new conversation id. The
// recipient lands on it via ``/?conversationId=…``.

import { formatDateTime } from "../../utils/dateFormat";
import { errorMessage } from "../../utils/errors";

import { Badge, Button, Card } from "@0x-copilot/design-system";
import { type ReactElement, useCallback, useEffect, useState } from "react";
import "./share-screen.css";
import type {
  ForkResponse,
  Message,
  RecipientPreview,
  SharedConversationView,
} from "@0x-copilot/api-types";
import {
  forkShare,
  getSharedConversation,
  previewSharedConversation,
} from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";

type LoadState =
  | { kind: "loading" }
  | { kind: "blocked"; preview: RecipientPreview }
  | { kind: "ready"; view: SharedConversationView }
  | { kind: "error"; message: string };

export function ShareScreen({
  token,
  identity,
  onForked,
  onBackToChat,
}: {
  token: string;
  identity: RequestIdentity;
  onForked: (conversationId: string) => void;
  onBackToChat: () => void;
}): ReactElement {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [forking, setForking] = useState(false);
  const [forkError, setForkError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load(): Promise<void> {
      setState({ kind: "loading" });
      try {
        const preview = await previewSharedConversation(token, identity);
        if (cancelled) return;
        if (!preview.can_view) {
          setState({ kind: "blocked", preview });
          return;
        }
        const view = await getSharedConversation(token, identity);
        if (cancelled) return;
        setState({ kind: "ready", view });
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(err, "Could not load share"),
        });
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [identity, token]);

  const onForkClick = useCallback(async () => {
    if (state.kind !== "ready") return;
    setForking(true);
    setForkError(null);
    try {
      const response: ForkResponse = await forkShare(token, {
        title: state.view.conversation.title ?? null,
      });
      onForked(response.conversation_id);
    } catch (err) {
      setForkError(errorMessage(err, "Could not fork conversation"));
    } finally {
      setForking(false);
    }
  }, [onForked, state, token]);

  if (state.kind === "loading") {
    return (
      <main className="share-screen share-screen--loading" aria-busy>
        <p>Loading shared conversation…</p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main className="share-screen share-screen--error">
        <Card>
          <h2>This share could not be loaded</h2>
          <p>{state.message}</p>
          <Button type="button" variant="ghost" onClick={onBackToChat}>
            Back to your chats
          </Button>
        </Card>
      </main>
    );
  }

  if (state.kind === "blocked") {
    return (
      <main className="share-screen share-screen--blocked">
        <Card>
          <h2>{blockedHeadline(state.preview.reason)}</h2>
          <p>{blockedBody(state.preview.reason)}</p>
          <Button type="button" variant="ghost" onClick={onBackToChat}>
            Back to your chats
          </Button>
        </Card>
      </main>
    );
  }

  const { view } = state;
  const sourcesVisible = view.share.sources_visible_to_viewer;
  const sharedBy =
    view.share.shared_by.display_name ?? view.share.shared_by.user_id;

  return (
    <main className="share-screen">
      <header className="share-screen__head">
        <div>
          <p className="share-screen__eyebrow">
            <Badge tone="neutral">Read-only share</Badge>
            {sourcesVisible ? null : (
              <Badge tone="warning">Sources restricted</Badge>
            )}
          </p>
          <h1>{view.conversation.title ?? "Shared conversation"}</h1>
          <p className="share-screen__sub">
            Shared by <strong>{sharedBy}</strong> ·{" "}
            <time dateTime={view.share.snapshot_at}>
              {formatDateTime(view.share.snapshot_at)}
            </time>
          </p>
        </div>
        <div className="share-screen__actions">
          <Button
            type="button"
            variant="primary"
            onClick={onForkClick}
            disabled={forking}
          >
            {forking ? "Forking…" : "Fork to my chat"}
          </Button>
          <Button type="button" variant="ghost" onClick={onBackToChat}>
            Back to your chats
          </Button>
        </div>
      </header>

      {forkError ? (
        <div className="share-screen__error" role="alert">
          {forkError}
        </div>
      ) : null}

      {!sourcesVisible ? (
        <Card>
          <p className="share-screen__notice">
            Citation chips in this conversation appear as{" "}
            <span className="share-screen__restricted-chip">restricted</span>{" "}
            because the share owner did not include the underlying sources. Fork
            to your chat to re-run with your own connectors and see live
            sources.
          </p>
        </Card>
      ) : null}

      <ol className="share-screen__messages">
        {view.messages.map((message) => (
          <li
            key={message.message_id}
            className="share-screen__message"
            data-role={message.role}
          >
            <SharedMessage message={message} sourcesVisible={sourcesVisible} />
          </li>
        ))}
      </ol>
    </main>
  );
}

function SharedMessage({
  message,
  sourcesVisible,
}: {
  message: Message;
  sourcesVisible: boolean;
}): ReactElement {
  return (
    <article className={`shared-message shared-message--${message.role}`}>
      <header className="shared-message__head">
        <span className="shared-message__role">
          {labelForRole(message.role)}
        </span>
        <time dateTime={message.created_at}>
          {formatDateTime(message.created_at)}
        </time>
      </header>
      <div className="shared-message__body">
        {renderContentWithCitations(message.content_text, sourcesVisible)}
      </div>
    </article>
  );
}

/**
 * Render assistant prose with ``[c<id>]`` citation tokens converted to
 * either link chips (when sources are visible) or a "restricted" chip
 * (when not). Recipient view never re-runs against the backend's
 * citation registry — the visible side of restriction is purely a
 * marker rendered in place of where the live chip would have been.
 */
function renderContentWithCitations(
  text: string,
  sourcesVisible: boolean,
): ReactElement[] {
  const tokenRe = /\[c(\d+)\]/g;
  const parts: ReactElement[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = tokenRe.exec(text)) !== null) {
    if (match.index > cursor) {
      parts.push(
        <span key={`t-${key++}`} className="shared-message__text">
          {text.slice(cursor, match.index)}
        </span>,
      );
    }
    if (sourcesVisible) {
      parts.push(
        <span key={`c-${key++}`} className="shared-message__chip">
          {match[1]}
        </span>,
      );
    } else {
      parts.push(
        <span
          key={`r-${key++}`}
          className="shared-message__chip shared-message__chip--restricted"
          title="Source restricted by the share owner"
          aria-label="Source restricted"
        >
          restricted
        </span>,
      );
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    parts.push(
      <span key={`t-${key++}`} className="shared-message__text">
        {text.slice(cursor)}
      </span>,
    );
  }
  return parts;
}

function labelForRole(role: Message["role"]): string {
  switch (role) {
    case "user":
      return "You";
    case "assistant":
      return "Assistant";
    case "system":
      return "System";
    case "tool":
      return "Tool";
    default:
      return role;
  }
}

function blockedHeadline(reason: RecipientPreview["reason"]): string {
  switch (reason) {
    case "revoked":
      return "This share has been revoked.";
    case "expired":
      return "This share has expired.";
    case "not_recipient":
      return "You don't have access to this share.";
    case "foreign_org":
      return "This share belongs to a different workspace.";
    case "share_not_found":
      return "Share not found.";
    case "ok":
      return "Access blocked.";
  }
}

function blockedBody(reason: RecipientPreview["reason"]): string {
  switch (reason) {
    case "revoked":
      return "Ask the share owner for a new link if you still need access.";
    case "expired":
      return "Ask the share owner to extend the expiry or send a new share.";
    case "not_recipient":
      return "The owner shared this with specific people and you're not on the list.";
    case "foreign_org":
      return "Sign in to the workspace this share belongs to, then re-open the link.";
    case "share_not_found":
      return "The link may be malformed, or the share row no longer exists.";
    case "ok":
      return "The preview returned an unexpected state. Try refreshing.";
  }
}
