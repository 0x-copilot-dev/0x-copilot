import { AppIcon, Menu } from "@enterprise-search/design-system";
import { useEffect, useRef, useState, type ReactElement } from "react";
import { getMyProfile } from "../../../../api/meApi";
import { useAuth } from "../../../auth/AuthContext";
import { DevPersonaSwitcher } from "./DevPersonaSwitcher";
import { WorkspacePicker } from "./WorkspacePicker";

/**
 * Sidebar UserCard (PR 2.2).
 *
 * The chip at the bottom of the sidebar — avatar, display name,
 * `<workspace> · <role>` sub. Opens an anchored popover with workspace
 * switcher / Settings / Sign out.
 *
 * Identity sources, in priority order:
 *   1. `getMyProfile()` (lazy `/v1/me/profile`) for `display_name`. The
 *      profile lives on the backend `users` row; we keep a per-mount
 *      snapshot — no global cache, no churn.
 *   2. `useAuth()` for the bearer-derived role + org/user fallback.
 *
 * All sources degrade gracefully — when the profile request hasn't
 * returned yet (or the endpoint isn't wired in dev), the chip falls
 * back to user_id / org_id so it never goes blank. The chip
 * re-renders on resolution.
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

  if (auth.identity === null) {
    return null;
  }

  const userId = auth.identity.user_id;
  const orgId = auth.identity.org_id;
  const role = auth.identity.roles[0] ?? null;
  const displayName = profile?.display_name?.trim() || userId;
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
        <AppIcon name={initials} />
        <span className="aui-user-card__body">
          <span className="aui-user-card__name">{displayName}</span>
          <span className="aui-user-card__sub">
            {orgId}
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

interface ProfileSnapshot {
  display_name: string | null;
}

/** Lazy `/v1/me/profile` fetch keyed on the bearer's user_id.
 * Returns null until the first response lands; failures swallow
 * silently — the card has a safe fallback. */
function useMyProfile(): ProfileSnapshot | null {
  const auth = useAuth();
  const userId = auth.identity?.user_id ?? null;
  const [profile, setProfile] = useState<ProfileSnapshot | null>(null);

  useEffect(() => {
    if (userId === null) {
      setProfile(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const response = await getMyProfile();
        if (cancelled) {
          return;
        }
        setProfile({ display_name: response.display_name });
      } catch {
        if (!cancelled) {
          setProfile(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [userId]);

  return profile;
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
