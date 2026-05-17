import type { Workspace } from "@enterprise-search/api-types";
import { useEffect, useState, type ReactElement } from "react";
import { listMyWorkspaces } from "../../../../api/meApi";
import { errorMessage } from "../../../../utils/errors";

/**
 * Workspace switcher list — rendered inside UserCard's popover (PR 2.2).
 *
 * Lazy: fetches `/v1/me/workspaces` on first mount; while loading shows
 * a one-line skeleton; on error shows a retry control. Single-workspace
 * users see a single, disabled row labeled "Only one workspace" so the
 * UI never silently disappears.
 */
export function WorkspacePicker({
  currentOrgId,
  onSwitch,
}: {
  currentOrgId: string;
  onSwitch: (orgId: string) => void;
}): ReactElement {
  const [state, setState] = useState<PickerState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    listMyWorkspaces()
      .then((response) => {
        if (cancelled) {
          return;
        }
        setState({ kind: "ready", workspaces: response.workspaces });
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        const message = errorMessage(err, "Could not load workspaces");
        setState({ kind: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <p className="aui-workspace-picker__note" role="status">
        Loading workspaces…
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="aui-workspace-picker__error" role="alert">
        <span>{state.message}</span>
        <button
          type="button"
          className="aui-ghost-button"
          onClick={() => setState({ kind: "loading" })}
        >
          Retry
        </button>
      </div>
    );
  }
  if (state.workspaces.length <= 1) {
    return <p className="aui-workspace-picker__note">Only one workspace.</p>;
  }
  return (
    <ul className="aui-workspace-picker__list" role="menu">
      {state.workspaces.map((workspace) => (
        <li key={workspace.org_id}>
          <button
            type="button"
            role="menuitemradio"
            aria-checked={workspace.org_id === currentOrgId}
            className="aui-workspace-picker__row"
            data-current={
              workspace.org_id === currentOrgId ? "true" : undefined
            }
            disabled={workspace.org_id === currentOrgId}
            onClick={() => onSwitch(workspace.org_id)}
          >
            <span className="aui-workspace-picker__name">
              {workspace.display_name}
            </span>
            <span className="aui-workspace-picker__sub">
              {workspaceSubtitle(workspace)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

type PickerState =
  | { kind: "loading" }
  | { kind: "ready"; workspaces: Workspace[] }
  | { kind: "error"; message: string };

function workspaceSubtitle(workspace: Workspace): string {
  const parts: string[] = [];
  if (workspace.role) {
    parts.push(workspace.role);
  }
  parts.push(
    `${workspace.member_count} ${workspace.member_count === 1 ? "member" : "members"}`,
  );
  return parts.join(" · ");
}
