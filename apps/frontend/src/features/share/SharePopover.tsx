/**
 * PR 4.5 — Topbar share popover.
 *
 * Mounts off the topbar share button via a render-prop slot (PR 2.1's
 * `Topbar.shareSlot`). v1 ships:
 *
 *   - **Copy link** — `navigator.clipboard.writeText`. Working today.
 *   - **Share to Slack** — `slack://share` URL scheme. Opens the Slack desktop
 *     client with the message pre-filled. Falls through to nothing when Slack
 *     isn't installed (browser default behaviour).
 *   - **Share to email** — `mailto:` link. Opens the user's mail client.
 *   - **View access** + **Sources visible** — disabled fieldset with a single
 *     `aria-describedby` tooltip. Wave 6 (sharing schema) wires these into a
 *     real `POST /v1/conversations/{id}/share` form without changing the
 *     popover shape.
 */

import {
  IconButton,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Switch,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useState } from "react";

import { useShareLinkText } from "./useShareLinkText";

export interface SharePopoverProps {
  chatTitle: string | null | undefined;
  chatUrl: string;
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
          <fieldset
            className="share-popover__fieldset"
            disabled
            aria-describedby="share-popover-disabled-tt"
          >
            <legend>View access</legend>
            <label>
              <input type="radio" name="va" defaultChecked /> Anyone in
              workspace
            </label>
            <label>
              <input type="radio" name="va" /> Specific people
            </label>
          </fieldset>
          <div
            className="share-popover__toggle"
            data-disabled="true"
            aria-describedby="share-popover-disabled-tt"
          >
            <Switch
              checked={false}
              disabled
              label="Sources visible to viewer"
            />
          </div>
          <span
            id="share-popover-disabled-tt"
            className="share-popover__hint"
            role="note"
          >
            Sharing settings ship with v2.
          </span>
        </div>
      </PopoverContent>
    </Popover>
  );
}
