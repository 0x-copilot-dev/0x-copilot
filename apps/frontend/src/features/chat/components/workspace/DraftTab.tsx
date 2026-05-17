// PR 3.2 — Draft tab body for the right-rail workspace pane.
//
// Shows the latest draft for the active conversation. Supports edit-in-
// place on the title and content, and "Send to {connector}" which
// routes through the existing approval flow (the runtime creates a
// tool_action approval; the inline ApprovalTool card decides). The
// status badge tracks the runtime_drafts.status enum.
//
// Optimistic concurrency: every PATCH/SEND/DISCARD carries
// `expected_version`. On 409 the latest version replaces local edits;
// the user re-applies their edit (no automatic merge in v1).

import {
  Badge,
  Button,
  Card,
  TextInput,
  classNames,
} from "@enterprise-search/design-system";
import type { Draft, DraftStatus } from "@enterprise-search/api-types";
import { useEffect, useState, type ReactElement } from "react";
import { errorMessage } from "../../../../utils/errors";

export interface DraftTabProps {
  draft: Draft | null;
  loading?: boolean;
  error?: string | null;
  /** Whether the surrounding chrome is disabled (e.g. shared-view). */
  disabled?: boolean;
  /** Edit the draft title + content (PR 1.3 PATCH endpoint). */
  onPatch?: (request: {
    expected_version: number;
    title: string;
    content_text: string;
  }) => Promise<Draft>;
  /** Send via connector approval flow (PR 1.3 send endpoint). */
  onSend?: (request: {
    expected_version: number;
    target_connector: string;
  }) => Promise<unknown>;
  /** Soft-discard a draft (PR 1.3 discard endpoint). */
  onDiscard?: (request: { expected_version: number }) => Promise<Draft>;
}

export function DraftTab({
  draft,
  loading,
  error,
  disabled,
  onPatch,
  onSend,
  onDiscard,
}: DraftTabProps): ReactElement {
  const [titleDraft, setTitleDraft] = useState<string>("");
  const [contentDraft, setContentDraft] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<"patch" | "send" | "discard" | null>(
    null,
  );
  const [actionError, setActionError] = useState<string | null>(null);

  // Reset local edits when the active draft changes (or version bumps from
  // a server emission). When a new version arrives mid-edit, we surface
  // a banner; we don't silently overwrite the user's text.
  useEffect(() => {
    if (draft === null) {
      setTitleDraft("");
      setContentDraft("");
      setDirty(false);
      setActionError(null);
      return;
    }
    if (!dirty) {
      setTitleDraft(draft.title);
      setContentDraft(draft.content_text);
    }
  }, [draft?.draft_id, draft?.version, draft?.title, draft?.content_text]);

  if (draft === null) {
    return (
      <div
        className="atlas-workspace-tab atlas-workspace-tab--empty"
        data-testid="workspace-draft-tab-empty"
      >
        {loading ? (
          <p>Loading drafts…</p>
        ) : error ? (
          <p role="alert">Couldn’t load drafts — {error}</p>
        ) : (
          <p>Drafts appear here when Atlas writes something for you.</p>
        )}
      </div>
    );
  }

  const sendDisabled =
    !!disabled ||
    pending !== null ||
    draft.target_connector === null ||
    draft.target_connector.trim() === "" ||
    draft.status !== "draft";
  const dirtyTitle = dirty && titleDraft !== draft.title;
  const dirtyContent = dirty && contentDraft !== draft.content_text;
  const canPatch =
    !!onPatch &&
    !disabled &&
    pending === null &&
    (dirtyTitle || dirtyContent) &&
    titleDraft.trim().length > 0;

  async function runPatch(): Promise<void> {
    if (!onPatch || draft === null) {
      return;
    }
    setPending("patch");
    setActionError(null);
    try {
      await onPatch({
        expected_version: draft.version,
        title: titleDraft,
        content_text: contentDraft,
      });
      setDirty(false);
    } catch (err: unknown) {
      setActionError(errorMessage(err, "Couldn’t save changes"));
    } finally {
      setPending(null);
    }
  }

  async function runSend(): Promise<void> {
    if (!onSend || draft === null || draft.target_connector === null) {
      return;
    }
    setPending("send");
    setActionError(null);
    try {
      await onSend({
        expected_version: draft.version,
        target_connector: draft.target_connector,
      });
      setDirty(false);
    } catch (err: unknown) {
      setActionError(errorMessage(err, "Couldn’t send draft"));
    } finally {
      setPending(null);
    }
  }

  async function runDiscard(): Promise<void> {
    if (!onDiscard || draft === null) {
      return;
    }
    setPending("discard");
    setActionError(null);
    try {
      await onDiscard({ expected_version: draft.version });
      setDirty(false);
    } catch (err: unknown) {
      setActionError(errorMessage(err, "Couldn’t discard draft"));
    } finally {
      setPending(null);
    }
  }

  return (
    <div
      className={classNames("atlas-workspace-tab", "atlas-workspace-draft-tab")}
      data-testid="workspace-draft-tab"
      data-status={draft.status}
    >
      <Card>
        <header className="atlas-workspace-draft-tab__header">
          <Badge tone={draftStatusBadgeTone(draft.status)}>
            {statusLabel(draft.status)}
          </Badge>
          <span className="atlas-workspace-draft-tab__version">
            v{draft.version}
          </span>
          {draft.target_connector ? (
            <Badge tone="neutral">→ {draft.target_connector}</Badge>
          ) : (
            <Badge tone="neutral">no target connector</Badge>
          )}
        </header>
        <TextInput
          aria-label="Draft title"
          className="atlas-workspace-draft-tab__title"
          value={titleDraft}
          onChange={(event) => {
            setTitleDraft(event.target.value);
            setDirty(true);
          }}
          disabled={disabled || pending !== null}
        />
        <textarea
          aria-label="Draft body"
          className="atlas-workspace-draft-tab__body"
          value={contentDraft}
          onChange={(event) => {
            setContentDraft(event.target.value);
            setDirty(true);
          }}
          disabled={disabled || pending !== null}
          rows={Math.max(8, contentDraft.split("\n").length + 2)}
        />
        {actionError ? (
          <p role="alert" className="atlas-workspace-draft-tab__error">
            {actionError}
          </p>
        ) : null}
        <footer className="atlas-workspace-draft-tab__footer">
          {onPatch ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={!canPatch}
              onClick={() => void runPatch()}
            >
              {pending === "patch" ? "Saving…" : "Save"}
            </Button>
          ) : null}
          {onSend ? (
            <Button
              type="button"
              size="sm"
              disabled={sendDisabled}
              onClick={() => void runSend()}
              data-testid="workspace-draft-tab-send"
              title={
                draft.target_connector
                  ? `Send to ${draft.target_connector}`
                  : "No target connector — pick one first"
              }
            >
              {pending === "send"
                ? "Sending…"
                : draft.target_connector
                  ? `Send to ${draft.target_connector}`
                  : "Send"}
            </Button>
          ) : null}
          {onDiscard ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={
                disabled || pending !== null || draft.status !== "draft"
              }
              onClick={() => void runDiscard()}
            >
              {pending === "discard" ? "Discarding…" : "Discard"}
            </Button>
          ) : null}
        </footer>
      </Card>
    </div>
  );
}

function draftStatusBadgeTone(
  status: DraftStatus,
): "neutral" | "accent" | "success" | "warning" | "danger" {
  switch (status) {
    case "draft":
      return "accent";
    case "send_pending_approval":
      return "warning";
    case "sent":
      return "success";
    case "discarded":
      return "neutral";
    default:
      return "neutral";
  }
}

function statusLabel(status: DraftStatus): string {
  switch (status) {
    case "draft":
      return "Draft";
    case "send_pending_approval":
      return "Pending approval";
    case "sent":
      return "Sent";
    case "discarded":
      return "Discarded";
    default:
      return status;
  }
}
