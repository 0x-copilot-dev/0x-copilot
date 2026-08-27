// <WorkspaceShellAccess /> — the per-workspace "may the agent run commands here?"
// list (PRD-shell-execution §7.3, §14.4).
//
// WHY PER-FOLDER AND NOT ONE SWITCH. "I trust the agent to run commands in
// ~/code/my-project" is a different sentence from "I trust the agent to run
// commands", and the product must be able to express the first without implying
// the second. The grant is already the unit the user thinks in — they attached
// THAT folder — so the permission rides the grant.
//
// WHY IT IS NOT A FOURTH GrantMode. The three modes (`read_only` <
// `read_write_no_delete` < `read_write`) are ORDERED file access. A fourth member
// would read as "more than read_write", which is wrong twice: running a command
// is a different kind of authority, and it is not a superset of write — a
// read-only folder someone wants to run a build in is a coherent thing to want.
//
// PRESENTATIONAL. No port, no fetching, no browser primitive. The caller owns
// `useWorkspaceFolderGrants` and decides whether to mount this at all — which is
// how a host with no `WorkspaceGrantPort.setShellEnabled` (web, and any desktop
// build with shell execution off) gets an ABSENT section rather than a control
// that cannot work.
//
// THE ASYMMETRY IS THE DESIGN. Turning commands ON is a two-step act: the switch
// arms a confirm that NAMES THE FOLDER and states §11.5's sentence verbatim,
// and only the confirm applies it. Turning them OFF applies immediately and
// asks nothing — a control that removes authority must never be harder to reach
// than the one that grants it.

import { Button, Toggle } from "@0x-copilot/design-system";
import { type CSSProperties, type ReactElement, useState } from "react";

import { WORKSPACE_SHELL_ACCESS_NOTICE } from "../composer/useWorkspaceFolderGrants";
import type { WorkspaceGrant } from "../ports/WorkspaceGrantPort";

import { Frow, SetCard, SetNote } from "./SettingsChrome";

/** Section copy. Exported so the test reads the same bytes the surface renders. */
export const WORKSPACE_SHELL_ACCESS_TITLE = "Run commands";
export const WORKSPACE_SHELL_ACCESS_META =
  "Which attached folders the agent may run commands in. Off for every folder until you turn it on.";
/**
 * The empty state. It says "attach a folder first" rather than "no folders",
 * because the section's absence and an empty section mean different things and
 * a user who reads only this one must still know what to do next.
 */
export const WORKSPACE_SHELL_ACCESS_EMPTY =
  "No folders are attached yet. Attach one from the composer to choose whether the agent may run commands in it.";

/** The confirm's verb. Pinned by the test — it must not soften to "OK". */
export const WORKSPACE_SHELL_ACCESS_CONFIRM_LABEL = "Allow commands";
export const WORKSPACE_SHELL_ACCESS_CANCEL_LABEL = "Cancel";

/** The confirm's sentence, with the folder named. */
export function workspaceShellAccessConfirmPrompt(label: string): string {
  return `Allow the agent to run commands in ${label}?`;
}

export interface WorkspaceShellAccessProps {
  /** Active grants, path-free. Empty renders the empty note, not nothing. */
  readonly grants: readonly WorkspaceGrant[];
  /**
   * Apply a decision, or `null` when the host cannot — in which case NOTHING is
   * rendered. `useWorkspaceFolderGrants().setShellEnabled` is already this
   * shape; see that hook for why it is null rather than a no-op.
   */
  readonly onSetShellEnabled:
    | ((grantId: string, enabled: boolean) => void)
    | null;
  /** A failed read/change, SHOWN. Never rendered as "no folders". */
  readonly error?: string | null;
  /** A host call is in flight; every control is inert while it is. */
  readonly busy?: boolean;
}

const listStyle: CSSProperties = { display: "block" };

const confirmStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-end",
  gap: "var(--space-sm)",
  padding: "10px 14px",
  borderTop: "1px solid var(--color-border)",
};

/**
 * The section body. Returns `null` when the host supplies no setter — the
 * capability is absent, and a disabled row of switches would advertise a
 * feature this build does not have.
 */
export function WorkspaceShellAccess({
  grants,
  onSetShellEnabled,
  error = null,
  busy = false,
}: WorkspaceShellAccessProps): ReactElement | null {
  // Which folder is mid-confirm, by grantId. At most one — arming a second
  // replaces the first, so two "allow commands" prompts can never be open with
  // only one of them visible to the user.
  const [arming, setArming] = useState<string | null>(null);

  if (onSetShellEnabled === null) return null;

  return (
    <SetCard
      title={WORKSPACE_SHELL_ACCESS_TITLE}
      meta={WORKSPACE_SHELL_ACCESS_META}
      data-testid="workspace-shell-access"
    >
      {/* The residual-risk sentence, stated ONCE at the top of the section and
          again inside every confirm. There is no OS sandbox in v1 (§11.5), and
          a per-folder switch without this reads as a confinement claim that the
          cwd binding does not make — it decides where a command STARTS, not
          where it can reach. */}
      <SetNote tone="warning" data-testid="workspace-shell-access-notice">
        {WORKSPACE_SHELL_ACCESS_NOTICE}
      </SetNote>

      {error !== null ? (
        <SetNote tone="danger" data-testid="workspace-shell-access-error">
          {error}
        </SetNote>
      ) : null}

      {grants.length === 0 ? (
        <SetNote data-testid="workspace-shell-access-empty">
          {WORKSPACE_SHELL_ACCESS_EMPTY}
        </SetNote>
      ) : (
        <div style={listStyle}>
          {grants.map((grant) => {
            const armed = arming === grant.grantId;
            return (
              <div key={grant.grantId}>
                <Frow
                  label={grant.label}
                  hint={
                    grant.shellEnabled
                      ? "The agent may run commands here. Every command still asks before it runs."
                      : "The agent cannot run commands here."
                  }
                >
                  <Toggle
                    data-testid={`workspace-shell-toggle-${grant.grantId}`}
                    aria-label={`Allow the agent to run commands in ${grant.label}`}
                    // ARMED IS NOT ON. While the confirm is open the switch still
                    // reads its real state, so a user who walks away from the
                    // prompt cannot come back to a control that looks enabled
                    // over a permission nothing recorded.
                    checked={grant.shellEnabled}
                    disabled={busy}
                    onChange={(event) => {
                      if (event.currentTarget.checked) {
                        // Enabling never applies from the switch itself.
                        setArming(grant.grantId);
                        return;
                      }
                      setArming(null);
                      onSetShellEnabled(grant.grantId, false);
                    }}
                  />
                </Frow>
                {armed ? (
                  <div
                    style={confirmStyle}
                    data-testid={`workspace-shell-confirm-${grant.grantId}`}
                  >
                    <p
                      style={{
                        margin: 0,
                        marginRight: "auto",
                        minWidth: 0,
                        fontSize: "var(--font-size-2xs)",
                        lineHeight: "var(--line-height-base)",
                        color: "var(--color-text-muted)",
                      }}
                    >
                      {/* The folder is NAMED in the confirm (§14.4). A prompt
                          that said "this folder" would be answerable without
                          knowing which one, and the whole point of the flag is
                          that it is per-folder. */}
                      <strong>
                        {workspaceShellAccessConfirmPrompt(grant.label)}
                      </strong>{" "}
                      {WORKSPACE_SHELL_ACCESS_NOTICE}
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      data-testid={`workspace-shell-cancel-${grant.grantId}`}
                      onClick={() => setArming(null)}
                    >
                      {WORKSPACE_SHELL_ACCESS_CANCEL_LABEL}
                    </Button>
                    <Button
                      variant="primary"
                      size="sm"
                      disabled={busy}
                      data-testid={`workspace-shell-allow-${grant.grantId}`}
                      onClick={() => {
                        setArming(null);
                        onSetShellEnabled(grant.grantId, true);
                      }}
                    >
                      {WORKSPACE_SHELL_ACCESS_CONFIRM_LABEL}
                    </Button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </SetCard>
  );
}
