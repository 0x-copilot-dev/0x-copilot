// WorkspaceFolderBar — the folder the agent has, ON the frame (PRD-FS-10 §4.1).
//
// It replaces the `+` menu's "Attach Folder" row, which was the wrong home for
// the capability twice over: a user had to open a menu to learn that folders
// exist at all, and every other row in that menu attaches something to THE
// MESSAGE while a grant outlives the message (it persists until revoked). Claude
// Code and Codex both put the working folder above the input, where it reads as
// context rather than as an action; so does this.
//
// WHAT IT MAY SHOW. The BASENAME and nothing else. `WorkspaceGrant` is path-free
// by construction (`ports/WorkspaceGrantPort`) — `label` is the broker's own
// sanitized display name — and this bar must not become the reason that is
// loosened. It never receives a host path, so it cannot print one.
//
// Presentational: no port, no effects, no state. The caller (`AssistantComposer`)
// owns `useWorkspaceFolderGrants` and decides WHETHER to mount this at all —
// which is how a host with no `WorkspaceGrantPort` (web) gets an absent bar
// rather than a control that cannot work.

import type { ReactElement } from "react";

import { Icon } from "../icons/Icon";
import type { WorkspaceGrant } from "../ports/WorkspaceGrantPort";

/** Empty-affordance copy — pinned by the tests; a bar that appeared only once
 *  you already had a folder could never teach anyone that folders exist. */
export const WORKSPACE_FOLDER_BAR_EMPTY_LABEL = "Attach a folder";

export interface WorkspaceFolderBarProps {
  /**
   * Active grants, path-free, **most recently granted first**. The head is the
   * one this bar names; the rest become `+N`. Ordering is the caller's job on
   * purpose: the broker returns whatever order it happens to hold, and the
   * folder a user is thinking about is the one they just attached (see
   * `useWorkspaceFolderGrants().lastGrantedId`).
   */
  readonly grants: readonly WorkspaceGrant[];
  /** A failed list / request / revoke, SHOWN. Never rendered as "no folders". */
  readonly error: string | null;
  /** A port call is in flight — the native picker may be up. */
  readonly busy: boolean;
  /** Open the host's folder picker (`WorkspaceGrantPort.requestGrant`). */
  readonly onAttach: () => void;
  /** Take the named grant away. Omitted ⇒ no revoke control in the bar. */
  readonly onRevoke?: (grantId: string) => void;
}

/**
 * The composer's folder line: `🗀 kaleidoscope +2` — or `🗀 Attach a folder`
 * when nothing is granted yet.
 *
 * The attach control's ACCESSIBLE NAME is its visible text, so what a user
 * reads is what a screen reader announces and what the tests match. The revoke
 * control is a SIBLING button (never nested inside the attach button — nested
 * interactive elements are invalid and unreachable by keyboard).
 */
export function WorkspaceFolderBar({
  grants,
  error,
  busy,
  onAttach,
  onRevoke,
}: WorkspaceFolderBarProps): ReactElement {
  const named = grants[0] ?? null;
  const extra = grants.length > 1 ? grants.length - 1 : 0;

  return (
    <div className="aui-folder-bar">
      <button
        type="button"
        className="aui-folder-bar__attach"
        // The hover hint says what clicking DOES; the accessible name stays the
        // visible text (a `title` only names an element that has no other name).
        title={
          named === null
            ? "Grant the agent access to a folder on this computer"
            : "Grant the agent access to another folder on this computer"
        }
        data-busy={busy || undefined}
        disabled={busy}
        onClick={onAttach}
      >
        <Icon name="folder" size={12} />
        <span className="aui-folder-bar__name">
          {named === null ? WORKSPACE_FOLDER_BAR_EMPTY_LABEL : named.label}
        </span>
        {extra > 0 ? (
          <span className="aui-folder-bar__more">{`+${extra}`}</span>
        ) : null}
      </button>
      {named !== null && onRevoke !== undefined ? (
        <button
          type="button"
          className="aui-folder-bar__revoke"
          // "Stop sharing", not "Remove": dismissing this REVOKES access the
          // agent currently has, which is a different act from un-attaching a
          // file. Same sentence the pill used, so the habit survives the move.
          aria-label={`Stop sharing ${named.label} with the agent`}
          onClick={() => onRevoke(named.grantId)}
        >
          ×
        </button>
      ) : null}
      {/* A failed read/request/revoke is SHOWN. The whole point of this
          subsystem is that filesystem trouble never renders as nothing. */}
      {error !== null ? (
        <span className="aui-folder-bar__error" role="status">
          {error}
        </span>
      ) : null}
    </div>
  );
}
