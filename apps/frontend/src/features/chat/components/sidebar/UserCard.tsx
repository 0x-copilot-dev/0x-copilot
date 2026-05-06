import { Menu } from "@enterprise-search/design-system";
import { useEffect, useRef, useState, type ReactElement } from "react";
import { listMyWorkspaces } from "../../../../api/meApi";
import { useAuth } from "../../../auth/AuthContext";
import { useMyProfile } from "../../../auth/useMyProfile";
import { DevPersonaSwitcher } from "./DevPersonaSwitcher";
import { WorkspacePicker } from "./WorkspacePicker";

/**
 * Sidebar UserCard (PR 2.2).
 *
 * The chip at the bottom of the sidebar — avatar, display name,
 * `<workspace> · <role>` sub. Opens an anchored popover with workspace
 * switcher / Settings / Sign out.
 *
 * Identity sources (priority order):
 *   1. shared `useMyProfile()` (lazy `/v1/me/profile`) for `display_name`.
 *   2. `useMyCurrentWorkspaceName()` (lazy `/v1/me/workspaces`) for the
 *      friendly workspace name (PR 8.0.2 — was rendering raw org_id).
 *   3. `useAuth()` for the bearer-derived role + org/user fallback.
 *
 * All sources degrade gracefully — when a request hasn't returned yet
 * (or the endpoint isn't wired in dev), the chip falls back to
 * user_id / org_id so it never goes blank. The chip re-renders on
 * resolution.
 */
export function UserCard({
  onOpenSettings,
  onSwitchWorkspace,
}: {
  onOpenSettings: () => void;
  onSwitchWorkspace?: (orgId: string) => void;
}): ReactElement | null {
  const auth = useAuth();
  const profile = useMyProfile();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const workspaceName = useMyCurrentWorkspaceName(
    auth.identity?.org_id ?? null,
  );

  if (auth.identity === null) {
    return null;
  }

  const userId = auth.identity.user_id;
  const orgId = auth.identity.org_id;
  const role = auth.identity.roles[0] ?? null;
  const displayName = profile?.display_name?.trim() || userId;
  const orgLabel = workspaceName ?? orgId;
  const initials = computeInitials(displayName);

  return (
    <div className="aui-user-card">
      <button
        ref={triggerRef}
        type="button"
        className="aui-user-card__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span
          className="ui-app-icon aui-user-card__avatar"
          aria-label={displayName}
        >
          {initials}
        </span>
        <span className="aui-user-card__body">
          <span className="aui-user-card__name">{displayName}</span>
          <span className="aui-user-card__sub">
            {orgLabel}
            {role ? ` · ${capitalize(role)}` : ""}
          </span>
        </span>
        <span aria-hidden="true">▾</span>
      </button>
      <Menu
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={triggerRef}
        side="up"
        align="left"
        className="aui-user-card__menu"
      >
        <DevPersonaSwitcher />
        <div className="aui-user-card__menu-section">
          <p className="aui-user-card__menu-heading">Workspaces</p>
          <WorkspacePicker
            currentOrgId={orgId}
            onSwitch={(nextOrgId) => {
              setOpen(false);
              onSwitchWorkspace?.(nextOrgId);
            }}
          />
        </div>
        <div className="aui-user-card__menu-divider" role="separator" />
        <div className="aui-user-card__menu-actions">
          <button
            type="button"
            className="aui-user-card__menu-item"
            onClick={() => {
              setOpen(false);
              onOpenSettings();
            }}
          >
            ⚙ Settings
          </button>
          <button
            type="button"
            className="aui-user-card__menu-item"
            onClick={() => {
              setOpen(false);
              void auth.logout();
            }}
          >
            ⏏ Sign out
          </button>
        </div>
      </Menu>
    </div>
  );
}

/** Lazy `/v1/me/workspaces` fetch keyed on the bearer's org_id. The
 * `WorkspacePicker` mounted inside the same UserCard popover already
 * fetches the same endpoint; the browser dedupes the concurrent
 * requests so we don't pay twice. */
function useMyCurrentWorkspaceName(orgId: string | null): string | null {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    if (orgId === null) {
      setName(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const response = await listMyWorkspaces();
        if (cancelled) return;
        const current = response.workspaces.find((w) => w.org_id === orgId);
        setName(current?.display_name?.trim() ?? null);
      } catch {
        if (!cancelled) setName(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgId]);
  return name;
}

/** Two-letter initials from a display name; falls back to first char. */
function computeInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) {
    return "U";
  }
  if (parts.length === 1) {
    return parts[0].charAt(0).toUpperCase();
  }
  return (
    parts[0].charAt(0).toUpperCase() +
    parts[parts.length - 1].charAt(0).toUpperCase()
  );
}

function capitalize(value: string): string {
  return value.length === 0
    ? value
    : value.charAt(0).toUpperCase() + value.slice(1);
}
