// <ProfilePage /> — Settings → Profile.
//
// Source: team-memory-cmdk-prd.md §7.4 (Settings pages, profile entry).
// Scope: display name (editable), avatar (read-only render), email
// (read-only), and a "Sign out" CTA. Avatar UPLOAD is intentionally
// deferred — this surface only renders the current `avatar_url`.
//
// Pure presentation: NO transport, NO router. The host wires
// `onSaveDisplayName(next)` and `onSignOut()` against the facade /
// auth context.

import {
  useCallback,
  useEffect,
  useId,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type ReactElement,
} from "react";

import { PageHeader } from "../shell/PageHeader";

/**
 * Minimal person shape consumed by the page. We intentionally do not
 * import a wider `UserProfile` from api-types here — the surface only
 * needs these four fields, and a narrow prop shape lets the host adapt
 * different identity sources (UserProfile, the auth-context bearer
 * payload, etc) without coupling the chat-surface package.
 */
export interface ProfilePagePerson {
  readonly user_id: string;
  readonly email: string;
  readonly display_name: string | null;
  readonly avatar_url: string | null;
}

export interface ProfilePageProps {
  readonly person: ProfilePagePerson;
  readonly onSaveDisplayName: (nextDisplayName: string) => void;
  readonly onSignOut: () => void;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const pageStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 16,
};

const formStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

const fieldsetStyle: CSSProperties = {
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: 12,
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const legendStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  padding: "0 6px",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  flexWrap: "wrap",
};

const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const inputStyle: CSSProperties = {
  height: 30,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-surface, #18181a)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  flex: 1,
  minWidth: 200,
};

const readOnlyStyle: CSSProperties = {
  ...inputStyle,
  background: "var(--color-surface-muted, #222224)",
  color: "var(--color-text-muted, #b4b4b8)",
  cursor: "not-allowed",
};

const avatarStyle: CSSProperties = {
  width: 56,
  height: 56,
  borderRadius: "var(--radius-full, 999px)",
  background: "var(--color-surface-muted, #222224)",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-md, 14px)",
  overflow: "hidden",
};

const saveBarStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: 8,
};

const saveButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  backgroundColor: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const signOutButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

function initialsFrom(display: string | null, email: string): string {
  const source =
    display !== null && display.trim().length > 0 ? display : email;
  const parts = source.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p.charAt(0).toUpperCase()).join("") || "?";
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function ProfilePage({
  person,
  onSaveDisplayName,
  onSignOut,
}: ProfilePageProps): ReactElement {
  const reactId = useId();
  const nameId = `${reactId}-display-name`;
  const emailId = `${reactId}-email`;

  const [displayName, setDisplayName] = useState<string>(
    person.display_name ?? "",
  );
  useEffect(() => {
    setDisplayName(person.display_name ?? "");
  }, [person.display_name]);

  const handleName = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setDisplayName(e.target.value);
  }, []);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = displayName.trim();
      if (trimmed === (person.display_name ?? "")) return;
      onSaveDisplayName(trimmed);
    },
    [displayName, person.display_name, onSaveDisplayName],
  );

  const dirty = displayName.trim() !== (person.display_name ?? "");

  return (
    <div style={pageStyle} data-testid="profile-page">
      <PageHeader
        title="Profile"
        subtitle="Your name and avatar across the workspace."
      />
      <form style={formStyle} onSubmit={handleSubmit}>
        <fieldset style={fieldsetStyle}>
          <legend style={legendStyle}>Identity</legend>
          <div style={rowStyle}>
            <div
              style={avatarStyle}
              aria-hidden={person.avatar_url !== null}
              data-testid="profile-avatar"
            >
              {person.avatar_url !== null ? (
                <img
                  src={person.avatar_url}
                  alt=""
                  style={{ width: "100%", height: "100%", objectFit: "cover" }}
                />
              ) : (
                initialsFrom(person.display_name, person.email)
              )}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={labelStyle}>Avatar</span>
              <span
                style={{
                  fontSize: "var(--font-size-xs, 12px)",
                  color: "var(--color-text-subtle, #7e7e84)",
                }}
              >
                Avatar upload is coming soon.
              </span>
            </div>
          </div>
          <div style={rowStyle}>
            <label htmlFor={nameId} style={labelStyle}>
              Display name
            </label>
            <input
              id={nameId}
              type="text"
              value={displayName}
              onChange={handleName}
              maxLength={120}
              style={inputStyle}
              data-testid="profile-display-name"
            />
          </div>
          <div style={rowStyle}>
            <label htmlFor={emailId} style={labelStyle}>
              Email
            </label>
            <input
              id={emailId}
              type="email"
              value={person.email}
              readOnly
              aria-readonly
              style={readOnlyStyle}
              data-testid="profile-email"
            />
          </div>
        </fieldset>
        <div style={saveBarStyle}>
          <button
            type="button"
            onClick={onSignOut}
            style={signOutButtonStyle}
            data-testid="profile-signout"
          >
            Sign out
          </button>
          <button
            type="submit"
            style={{
              ...saveButtonStyle,
              opacity: dirty ? 1 : 0.6,
              cursor: dirty ? "pointer" : "not-allowed",
            }}
            disabled={!dirty}
            aria-disabled={!dirty}
            data-testid="profile-save"
          >
            Save changes
          </button>
        </div>
      </form>
    </div>
  );
}
