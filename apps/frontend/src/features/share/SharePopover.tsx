/**
 * SharePopover — topbar share popover.
 *
 * PR 4.5 shipped a copy-link / Slack / email surface with a disabled
 * fieldset for view-access + sources-visible. PR 6.1 fills that fieldset
 * by talking to the real `conversation_shares` API:
 *
 *   - Create a workspace- or specific-people share with the
 *     "sources visible to viewer" toggle.
 *   - List active shares on the current chat (no plaintext tokens; the
 *     server returns `share_token_prefix` + `share_url` only at create
 *     time).
 *   - Revoke a share row inline.
 *
 * The copy-link / Slack / email rows still operate on the *current page*
 * URL — they're a quick-share for "anyone in workspace" already; the new
 * fieldset wires the audited share row when the user wants real access
 * controls + revocation.
 */

import {
  IconButton,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Switch,
} from "@0x-copilot/design-system";
import type { ConversationShare, ShareViewAccess } from "@0x-copilot/api-types";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { RequestIdentity } from "../../api/config";
import { createShare, listShares, revokeShare } from "../../api/agentApi";
import { useShareLinkText } from "./useShareLinkText";

export interface SharePopoverProps {
  chatTitle: string | null | undefined;
  chatUrl: string;
  /**
   * Conversation id the popover operates on. Required for the real
   * share-row create / list / revoke surface; the legacy copy-link /
   * Slack / email row works without it.
   */
  conversationId?: string | null;
  identity?: RequestIdentity | null;
  /**
   * Notified on every share-row interaction so callers can surface a status
   * line in the topbar (matches the legacy `onShare` toast). Optional — the
   * popover renders an inline "Copied" affordance even if no callback is
   * wired.
   */
  onStatus?: (message: string) => void;
}

const COPIED_RESET_MS = 1500;

export function SharePopover({
  chatTitle,
  chatUrl,
  conversationId,
  identity,
  onStatus,
}: SharePopoverProps): ReactElement {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const { title, body } = useShareLinkText({ chatTitle, chatUrl });

  const onCopy = useCallback(async () => {
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      onStatus?.("Copy this page URL to share the chat.");
      return;
    }
    try {
      await navigator.clipboard.writeText(chatUrl);
      setCopied(true);
      onStatus?.("Chat link copied.");
      window.setTimeout(() => setCopied(false), COPIED_RESET_MS);
    } catch {
      onStatus?.("Could not copy share link.");
    }
  }, [chatUrl, onStatus]);

  const onSlack = useCallback(() => {
    const url = `slack://share?url=${encodeURIComponent(chatUrl)}&text=${encodeURIComponent(body)}`;
    window.open(url);
    onStatus?.("Opening Slack…");
  }, [body, chatUrl, onStatus]);

  const onEmail = useCallback(() => {
    const url = `mailto:?subject=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
    window.location.href = url;
    onStatus?.("Opening email…");
  }, [body, title, onStatus]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          type="button"
          variant="ghost"
          aria-label="Share this conversation"
          data-tooltip="Share"
          data-tooltip-placement="bottom"
        >
          ⤴
        </IconButton>
      </PopoverTrigger>
      <PopoverContent aria-label="Share this conversation">
        <div className="share-popover" data-testid="share-popover">
          <button
            type="button"
            className="share-popover__row"
            onClick={onCopy}
            data-success={copied || undefined}
          >
            <span className="share-popover__icon" aria-hidden="true">
              ⎘
            </span>
            {copied ? "Copied" : "Copy link"}
          </button>
          <button
            type="button"
            className="share-popover__row"
            onClick={onSlack}
          >
            <span className="share-popover__icon" aria-hidden="true">
              #
            </span>
            Share to Slack
          </button>
          <button
            type="button"
            className="share-popover__row"
            onClick={onEmail}
          >
            <span className="share-popover__icon" aria-hidden="true">
              ✉
            </span>
            Share to email
          </button>
          <hr className="share-popover__divider" />
          <ShareForm
            conversationId={conversationId ?? null}
            identity={identity ?? null}
            popoverOpen={open}
            onStatus={onStatus}
          />
        </div>
      </PopoverContent>
    </Popover>
  );
}

interface ShareFormProps {
  conversationId: string | null;
  identity: RequestIdentity | null;
  popoverOpen: boolean;
  onStatus?: (message: string) => void;
}

interface CreatedShareView {
  share_id: string;
  share_url: string;
  share_token_prefix: string | null;
  view_access: ShareViewAccess;
  sources_visible_to_viewer: boolean;
}

function ShareForm({
  conversationId,
  identity,
  popoverOpen,
  onStatus,
}: ShareFormProps): ReactElement {
  const [viewAccess, setViewAccess] = useState<ShareViewAccess>("workspace");
  const [recipientsText, setRecipientsText] = useState("");
  const [sourcesVisible, setSourcesVisible] = useState(false);
  const [shares, setShares] = useState<ConversationShare[]>([]);
  const [createdView, setCreatedView] = useState<CreatedShareView | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const canMutate = conversationId !== null && identity !== null;

  // Reload the active-shares list whenever the popover opens.
  useEffect(() => {
    if (!popoverOpen || !canMutate) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const response = await listShares(conversationId, identity);
        if (cancelled) {
          return;
        }
        setShares(response.shares);
        setLoadError(null);
      } catch (error) {
        if (!cancelled) {
          setLoadError(messageOf(error, "Could not load shares."));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [popoverOpen, canMutate, conversationId, identity]);

  const recipientUserIds = useMemo(() => {
    return recipientsText
      .split(/[,\n]/)
      .map((value) => value.trim())
      .filter((value) => value.length > 0);
  }, [recipientsText]);

  const onSubmit = useCallback(async () => {
    if (!canMutate) {
      return;
    }
    setBusy(true);
    try {
      const response = await createShare(
        conversationId,
        {
          view_access: viewAccess,
          recipient_user_ids: viewAccess === "specific" ? recipientUserIds : [],
          sources_visible_to_viewer: sourcesVisible,
          include_link: true,
        },
        identity,
      );
      setCreatedView({
        share_id: response.share_id,
        share_url: response.share_url,
        share_token_prefix: response.share_token_prefix,
        view_access: response.view_access,
        sources_visible_to_viewer: response.sources_visible_to_viewer,
      });
      setShares((current) => [
        {
          share_id: response.share_id,
          share_token_prefix: response.share_token_prefix,
          view_access: response.view_access,
          recipient_user_ids: response.recipient_user_ids,
          sources_visible_to_viewer: response.sources_visible_to_viewer,
          snapshot_at: response.snapshot_at,
          expires_at: response.expires_at,
          revoked_at: response.revoked_at,
          created_by_user_id: response.created_by_user_id,
          created_at: response.created_at,
          view_count: 0,
        },
        ...current,
      ]);
      onStatus?.("Share created.");
    } catch (error) {
      onStatus?.(messageOf(error, "Could not create share."));
    } finally {
      setBusy(false);
    }
  }, [
    canMutate,
    conversationId,
    identity,
    viewAccess,
    recipientUserIds,
    sourcesVisible,
    onStatus,
  ]);

  const onRevoke = useCallback(
    async (shareId: string) => {
      if (!canMutate) {
        return;
      }
      setBusy(true);
      try {
        await revokeShare(shareId, identity);
        setShares((current) =>
          current.filter((share) => share.share_id !== shareId),
        );
        if (createdView?.share_id === shareId) {
          setCreatedView(null);
        }
        onStatus?.("Share revoked.");
      } catch (error) {
        onStatus?.(messageOf(error, "Could not revoke share."));
      } finally {
        setBusy(false);
      }
    },
    [canMutate, identity, createdView, onStatus],
  );

  const onCopyCreatedUrl = useCallback(async () => {
    if (createdView === null) {
      return;
    }
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      onStatus?.("Copy the share link manually.");
      return;
    }
    try {
      await navigator.clipboard.writeText(createdView.share_url);
      onStatus?.("Share link copied.");
    } catch {
      onStatus?.("Could not copy share link.");
    }
  }, [createdView, onStatus]);

  return (
    <div className="share-popover__form">
      <fieldset
        className="share-popover__fieldset"
        disabled={busy || !canMutate}
        aria-describedby={canMutate ? undefined : "share-popover-disabled-tt"}
      >
        <legend>View access</legend>
        <label>
          <input
            type="radio"
            name="va"
            value="workspace"
            checked={viewAccess === "workspace"}
            onChange={() => setViewAccess("workspace")}
          />{" "}
          Anyone in workspace
        </label>
        <label>
          <input
            type="radio"
            name="va"
            value="specific"
            checked={viewAccess === "specific"}
            onChange={() => setViewAccess("specific")}
          />{" "}
          Specific people
        </label>
        {viewAccess === "specific" && (
          <textarea
            className="share-popover__recipients"
            placeholder="user_id, user_id, …"
            value={recipientsText}
            onChange={(event) => setRecipientsText(event.target.value)}
            rows={2}
            data-testid="share-popover-recipients"
          />
        )}
      </fieldset>
      <div className="share-popover__toggle">
        <Switch
          checked={sourcesVisible}
          onChange={(event) => setSourcesVisible(event.target.checked)}
          disabled={busy || !canMutate}
          label="Sources visible to viewer"
        />
      </div>
      {!canMutate && (
        <span
          id="share-popover-disabled-tt"
          className="share-popover__hint"
          role="note"
        >
          Send a message to share this chat.
        </span>
      )}
      {canMutate && (
        <button
          type="button"
          className="share-popover__primary"
          onClick={() => void onSubmit()}
          disabled={
            busy || (viewAccess === "specific" && recipientUserIds.length === 0)
          }
          data-testid="share-popover-create"
        >
          {busy ? "Creating…" : "Create share"}
        </button>
      )}
      {createdView !== null && (
        <div
          className="share-popover__created"
          data-testid="share-popover-created"
        >
          <span className="share-popover__hint">
            Plaintext share link below — shown once. Copy now.
          </span>
          <div className="share-popover__url-row">
            <code className="share-popover__url">{createdView.share_url}</code>
            <button
              type="button"
              className="share-popover__row"
              onClick={() => void onCopyCreatedUrl()}
            >
              ⎘ Copy
            </button>
          </div>
        </div>
      )}
      {loadError !== null && (
        <span className="share-popover__hint" role="alert">
          {loadError}
        </span>
      )}
      {shares.length > 0 && (
        <ul className="share-popover__list" data-testid="share-popover-list">
          {shares.map((share) => (
            <li key={share.share_id} className="share-popover__list-item">
              <span>
                {share.share_token_prefix
                  ? `${share.share_token_prefix}…`
                  : share.share_id}
              </span>
              <span>·</span>
              <span>{share.view_access}</span>
              <button
                type="button"
                className="share-popover__row share-popover__row--danger"
                onClick={() => void onRevoke(share.share_id)}
                disabled={busy}
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
