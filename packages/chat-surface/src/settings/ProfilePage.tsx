// <ProfilePage /> — Settings → Profile.
//
// Source: team-memory-cmdk-prd.md §7.4 (Settings pages, profile entry).
// Scope: display name (editable), avatar (read-only render), the identity
// ANCHOR (a verified email XOR a wallet address + chain), a "Signed in with"
// indicator, and a "Sign out" CTA. Avatar UPLOAD is intentionally deferred —
// this surface only renders the current `avatar_url`.
//
// Honest identity (Issues 3 + 4): a wallet (SIWE) account has no real email, so
// the anchor is a discriminated union — the host resolves whether the account
// is email- or wallet-based and passes the right variant. The page NEVER shows
// a synthesized `@wallet.invalid` address, and never nags a wallet user to
// "verify" an address that is structurally unverifiable.
//
// Pure presentation: NO transport, NO router. The host wires
// `onSaveDisplayName(next)` and `onSignOut()` against the facade / auth context.

import {
  useCallback,
  useEffect,
  useId,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type FormEvent,
  type ReactElement,
} from "react";

import { PageHeader } from "../shell/PageHeader";

/**
 * The account's identity anchor — the thing that *is* the account. Exactly one
 * variant per account (mutually exclusive by construction, so a wallet binder
 * can never be forced to synthesize a fake email):
 *   - `email`  — a real address (Google / dev), with its verified state.
 *   - `wallet` — an EIP-55 checksummed address + the chain it linked on.
 */
export type ProfileIdentityAnchor =
  | {
      readonly kind: "email";
      readonly email: string;
      readonly verified: boolean;
    }
  | {
      readonly kind: "wallet";
      readonly address: string;
      readonly chainId: number | null;
      readonly chainLabel: string | null;
    };

/**
 * Minimal person shape consumed by the page. We intentionally do not import a
 * wider `UserProfile` from api-types here — a narrow prop shape lets the host
 * adapt different identity sources without coupling the chat-surface package.
 */
export interface ProfilePagePerson {
  readonly user_id: string;
  readonly display_name: string | null;
  readonly avatar_url: string | null;
  /** Email XOR wallet — see {@link ProfileIdentityAnchor}. */
  readonly anchor: ProfileIdentityAnchor;
  /**
   * Durable auth origin, for the "Signed in with" indicator. Optional; when
   * absent the label falls back to the anchor kind.
   */
  readonly authMethod?: "google" | "siwe" | "local" | "dev" | string | null;
}

export interface ProfilePageProps {
  readonly person: ProfilePagePerson;
  /**
   * Persist a new display name. OPTIONAL: when omitted the name renders
   * read-only and the Save affordance is hidden (a substrate that cannot rename,
   * or a read-only identity).
   */
  readonly onSaveDisplayName?: (nextDisplayName: string) => void;
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

// Wallet address: read-only + monospace (an address is a code, not prose), and
// user-selectable so it can be copied natively.
const monoReadOnlyStyle: CSSProperties = {
  ...readOnlyStyle,
  fontFamily: "var(--font-mono, ui-monospace, monospace)",
  fontSize: "var(--font-size-xs, 12px)",
  userSelect: "all",
  cursor: "text",
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

// "Signed in with …" — a quiet pill above the identity fields.
const chipStyle: CSSProperties = {
  alignSelf: "flex-start",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  background: "var(--color-surface-muted, #222224)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-full, 999px)",
  padding: "2px 10px",
};

const badgeStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  color: "var(--color-success-contrast, #06210f)",
  background: "var(--color-success, #4ea674)",
  borderRadius: "var(--radius-full, 999px)",
  padding: "2px 8px",
};

const chainChipStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  background: "var(--color-surface-muted, #222224)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-full, 999px)",
  padding: "2px 8px",
};

const noteStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  lineHeight: 1.45,
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

// A wallet mark for the avatar fallback when the only "name" is the address.
const WALLET_GLYPH = "⬡";

function initials(source: string): string {
  const parts = source.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p.charAt(0).toUpperCase()).join("") || "?";
}

function avatarContent(person: ProfilePagePerson): string {
  const name = person.display_name?.trim() ?? "";
  // A user-chosen name → initials. The wallet default IS the truncated address
  // ("0x…"), which makes lousy initials, so fall through to the glyph for it.
  if (name !== "" && !name.startsWith("0x")) {
    return initials(name);
  }
  if (person.anchor.kind === "email") {
    return initials(person.anchor.email);
  }
  return WALLET_GLYPH;
}

function signedInLabel(person: ProfilePagePerson): string {
  const method = person.authMethod;
  if (method === "google") return "Signed in with Google";
  if (method === "siwe" || person.anchor.kind === "wallet") {
    return "Signed in with a wallet";
  }
  if (method === "local" || method === "dev") return "Signed in on this device";
  return "Signed in with email";
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
  const anchorId = `${reactId}-anchor`;

  const canEditName = onSaveDisplayName !== undefined;
  const [displayName, setDisplayName] = useState<string>(
    person.display_name ?? "",
  );
  useEffect(() => {
    setDisplayName(person.display_name ?? "");
  }, [person.display_name]);

  const handleName = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setDisplayName(e.target.value);
  }, []);

  const dirty = displayName.trim() !== (person.display_name ?? "");

  const handleSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault();
      if (onSaveDisplayName === undefined) return;
      const trimmed = displayName.trim();
      if (trimmed === (person.display_name ?? "")) return;
      onSaveDisplayName(trimmed);
    },
    [displayName, person.display_name, onSaveDisplayName],
  );

  const anchor = person.anchor;

  return (
    <div style={pageStyle} data-testid="profile-page">
      <PageHeader title="Profile" subtitle="Your name and how you sign in." />
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
                avatarContent(person)
              )}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span style={chipStyle} data-testid="profile-signed-in-with">
                {signedInLabel(person)}
              </span>
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
              readOnly={!canEditName}
              aria-readonly={!canEditName}
              maxLength={120}
              placeholder={anchor.kind === "wallet" ? "Add a name" : undefined}
              style={canEditName ? inputStyle : readOnlyStyle}
              data-testid="profile-display-name"
            />
          </div>

          {anchor.kind === "email" ? (
            <div style={rowStyle}>
              <label htmlFor={anchorId} style={labelStyle}>
                Email
              </label>
              <input
                id={anchorId}
                type="email"
                value={anchor.email}
                readOnly
                aria-readonly
                style={readOnlyStyle}
                data-testid="profile-email"
              />
              {anchor.verified ? (
                <span style={badgeStyle} data-testid="profile-verified-badge">
                  Verified
                </span>
              ) : null}
            </div>
          ) : (
            <>
              <div style={rowStyle}>
                <label htmlFor={anchorId} style={labelStyle}>
                  Wallet address
                </label>
                <input
                  id={anchorId}
                  type="text"
                  value={anchor.address}
                  readOnly
                  aria-readonly
                  style={monoReadOnlyStyle}
                  data-testid="profile-wallet-address"
                />
                {anchor.chainLabel !== null && anchor.chainLabel !== "" ? (
                  <span
                    style={chainChipStyle}
                    data-testid="profile-wallet-chain"
                  >
                    {anchor.chainLabel}
                  </span>
                ) : null}
              </div>
              <span style={noteStyle} data-testid="profile-wallet-note">
                You signed in with your wallet — no email is associated with
                this account.
              </span>
            </>
          )}
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
          {canEditName ? (
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
          ) : null}
        </div>
      </form>
    </div>
  );
}
