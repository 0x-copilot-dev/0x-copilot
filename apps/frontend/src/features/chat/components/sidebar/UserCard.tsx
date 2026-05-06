import { AppIcon, Menu } from "@enterprise-search/design-system";
import { useRef, useState, type ReactElement } from "react";
import { useAuth } from "../../../auth/AuthContext";
import { DevPersonaSwitcher } from "./DevPersonaSwitcher";
import { WorkspacePicker } from "./WorkspacePicker";

/**
 * Sidebar UserCard (PR 2.2).
 *
 * The chip at the bottom of the sidebar — avatar, name, workspace · role
 * — that opens an anchored popover with workspace switcher / Settings /
 * Sign out.
 *
 * Source of identity: `useAuth()`. Today's `SessionIdentity` doesn't
 * carry a display name (that lives on the backend `users` row); we
 * gracefully fall back to the user_id text so the chip never goes
 * blank. When the auth/discover endpoint widens to include
 * `display_name` (PR 5.1), the chip upgrades automatically.
 *
 * Workspace switch: today the v1 endpoint returns only the caller's
 * current workspace, so the picker shows a single disabled row
 * ("Only one workspace"). When multi-workspace listing lands the same
 * code shows real rows; clicking calls the optional `onSwitchWorkspace`
 * prop (or the no-op fallback) so consumers can decide whether the
 * switch happens via re-login, hard nav, or a future API call.
 */
export function UserCard({
  onOpenSettings,
  onSwitchWorkspace,
}: {
  onOpenSettings: () => void;
  onSwitchWorkspace?: (orgId: string) => void;
}): ReactElement | null {
  const auth = useAuth();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  if (auth.identity === null) {
    return null;
  }

  const userId = auth.identity.user_id;
  const orgId = auth.identity.org_id;
  const initials = userId.charAt(0).toUpperCase() || "U";
  // The role list comes from the bearer's `roles[]` claim; surface the
  // first one as a hint until /me returns a richer profile.
  const role = auth.identity.roles[0] ?? null;

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
        <AppIcon name={initials} />
        <span className="aui-user-card__body">
          <span className="aui-user-card__name">{userId}</span>
          <span className="aui-user-card__sub">
            {orgId}
            {role ? ` · ${role}` : ""}
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
