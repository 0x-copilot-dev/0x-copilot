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
import { Modal } from "./Modal";

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
    }
  | {
      /**
       * "Use locally, no account" — the device account. No email, no wallet;
       * the account is anchored to this install. Never renders the synthetic
       * `@local.invalid` placeholder.
       */
      readonly kind: "device";
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

/**
 * One linked sign-in identity (account-linking PRD FR-L4/U1). Mirrors the
 * api-types `LinkedIdentity` wire shape as plain props — the host maps it.
 */
export interface ProfileLinkedIdentity {
  readonly kind: "wallet" | "oidc" | string;
  readonly id: string;
  readonly provider?: string | null;
  readonly email?: string | null;
  readonly address?: string | null;
  readonly chainName?: string | null;
}

/**
 * Outcome of a host wallet-link attempt (PRD FR-L1/M1/U2). The host runs
 * the SIWE proof + `POST /v1/me/identities/wallet` and maps the response
 * to one of these so the (substrate-agnostic) page can drive the
 * merge-confirm flow without knowing any transport detail:
 *   - `linked` / `already_linked` / `merged` → success, panel refreshes.
 *   - `merge_required` → the wallet belongs to another account; the page
 *     shows the merge-confirm dialog and, on confirm, re-invokes
 *     `onLinkWallet({ confirmMerge: true })` (which MUST re-sign — the SIWE
 *     nonce is single-use). `message` is the server's user-safe reason.
 *   - `error` → surfaced inline (`message`).
 *   - `cancelled` → the user dismissed the wallet prompt; quiet reset.
 */
export type LinkWalletOutcome =
  | { readonly status: "linked" | "already_linked" | "merged" }
  | { readonly status: "merge_required"; readonly message?: string }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "cancelled" };

export interface ProfilePageProps {
  readonly person: ProfilePagePerson;
  /**
   * Persist a new display name. OPTIONAL: when omitted the name renders
   * read-only and the Save affordance is hidden (a substrate that cannot rename,
   * or a read-only identity).
   */
  readonly onSaveDisplayName?: (nextDisplayName: string) => void;
  readonly onSignOut: () => void;
  /**
   * Every sign-in identity linked to the account (PRD FR-U1). When provided
   * (even empty) the "Linked accounts" panel renders; when omitted the panel
   * is hidden entirely (older hosts / no data).
   */
  readonly linkedIdentities?: readonly ProfileLinkedIdentity[];
  /**
   * Run the link-a-wallet flow (PRD FR-L1). The host performs the SIWE
   * proof + `POST /v1/me/identities/wallet` and resolves a
   * {@link LinkWalletOutcome}; the page owns the merge-confirm dialog and
   * re-invokes with `confirmMerge: true` on consent (FR-U2). Optional — the
   * CTA renders only when the host wires the flow.
   */
  readonly onLinkWallet?: (options: {
    readonly confirmMerge: boolean;
  }) => Promise<LinkWalletOutcome>;
  /**
   * Start the link-Google flow (OAuth; also how a wallet account adds an
   * email). Optional — the CTA renders only when the host wires the flow.
   */
  readonly onLinkGoogle?: () => void;
  /**
   * Unlink a linked sign-in identity (PRD FR-L5). The host calls
   * `DELETE /v1/me/identities/{kind}/{id}`; it MUST reject (throw) on the
   * 409 last-sign-in-method guard, whose `Error.message` the page surfaces
   * verbatim next to the row. Optional — Unlink renders only when wired.
   */
  readonly onUnlinkIdentity?: (kind: string, id: string) => Promise<void>;
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
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: 12,
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const legendStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text)",
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
  color: "var(--color-text-muted)",
};

const inputStyle: CSSProperties = {
  height: 30,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm, 13px)",
  flex: 1,
  minWidth: 200,
};

const readOnlyStyle: CSSProperties = {
  ...inputStyle,
  background: "var(--color-surface-muted)",
  color: "var(--color-text-muted)",
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
  background: "var(--color-surface-muted)",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-md, 14px)",
  overflow: "hidden",
};

// "Signed in with …" — a quiet pill above the identity fields.
const chipStyle: CSSProperties = {
  alignSelf: "flex-start",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted)",
  background: "var(--color-surface-muted)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-full, 999px)",
  padding: "2px 10px",
};

const badgeStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  color: "var(--color-success-contrast)",
  background: "var(--color-success)",
  borderRadius: "var(--radius-full, 999px)",
  padding: "2px 8px",
};

const chainChipStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  color: "var(--color-text-muted)",
  background: "var(--color-surface-muted)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-full, 999px)",
  padding: "2px 8px",
};

const noteStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle)",
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
  border: "1px solid var(--color-accent)",
  backgroundColor: "var(--color-accent)",
  color: "var(--color-accent-contrast)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

// Link-a-method CTA — quiet outline button inside the Linked accounts panel.
const linkCtaStyle: CSSProperties = {
  alignSelf: "flex-start",
  height: 30,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

// Unlink — a quiet, danger-toned text button trailing each linked row. Pushed
// to the row's end so it reads as a per-row action, not a primary CTA.
const unlinkButtonStyle: CSSProperties = {
  marginLeft: "auto",
  height: 28,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-danger)",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
};

// Inline error line (unlink guard, link failure) — danger-toned, small.
const inlineErrorStyle: CSSProperties = {
  width: "100%",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-danger)",
  lineHeight: 1.45,
};

// Merge-confirm dialog action buttons.
const dialogCancelStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const dialogConfirmStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-danger)",
  background: "var(--color-danger)",
  color: "var(--color-danger-contrast)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const signOutButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
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
  if (person.anchor.kind === "device") {
    return initials(name ?? "Local account");
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

/** Wallet-link flow state owned by the page (host does the proof + POST). */
type LinkState =
  | { readonly kind: "idle" }
  | { readonly kind: "busy" }
  | { readonly kind: "confirm-merge"; readonly message: string }
  | { readonly kind: "merging" }
  | { readonly kind: "error"; readonly message: string };

// Honest default copy for the merge-confirm dialog (PRD FR-U2). Shown when
// the server sends no user-safe reason of its own.
const DEFAULT_MERGE_MESSAGE =
  "This wallet already belongs to another account. Linking it will move " +
  "that account's data into this one and disable its separate login. This " +
  "cannot be undone.";

export function ProfilePage({
  person,
  onSaveDisplayName,
  onSignOut,
  linkedIdentities,
  onLinkWallet,
  onLinkGoogle,
  onUnlinkIdentity,
}: ProfilePageProps): ReactElement {
  const reactId = useId();
  const nameId = `${reactId}-display-name`;
  const anchorId = `${reactId}-anchor`;

  // --- wallet-link + merge-confirm state ----------------------------------
  const [linkState, setLinkState] = useState<LinkState>({ kind: "idle" });

  const runWalletLink = useCallback(
    async (confirmMerge: boolean): Promise<void> => {
      if (onLinkWallet === undefined) return;
      setLinkState({ kind: confirmMerge ? "merging" : "busy" });
      try {
        const outcome = await onLinkWallet({ confirmMerge });
        switch (outcome.status) {
          case "linked":
          case "already_linked":
          case "merged":
          case "cancelled":
            // The host refreshes the identity list on success; a cancel is a
            // quiet reset. Either way, back to idle with the dialog closed.
            setLinkState({ kind: "idle" });
            break;
          case "merge_required":
            if (person.anchor.kind === "device") {
              // D2: a local/device account REJECTS conflicts instead of
              // offering the merge — that identity already belongs to
              // another profile on this device.
              setLinkState({
                kind: "error",
                message:
                  "That sign-in method already belongs to another profile " +
                  "on this device. Sign in with it directly instead.",
              });
              break;
            }
            setLinkState({
              kind: "confirm-merge",
              message: outcome.message ?? DEFAULT_MERGE_MESSAGE,
            });
            break;
          case "error":
            setLinkState({ kind: "error", message: outcome.message });
            break;
        }
      } catch (err) {
        setLinkState({
          kind: "error",
          message:
            err instanceof Error && err.message !== ""
              ? err.message
              : "Could not link that wallet. Please try again.",
        });
      }
    },
    [onLinkWallet, person.anchor.kind],
  );

  const dismissMergeConfirm = useCallback(() => {
    setLinkState({ kind: "idle" });
  }, []);

  // --- unlink state (per-identity busy + inline guard error) --------------
  const [unlinkingId, setUnlinkingId] = useState<string | null>(null);
  const [unlinkError, setUnlinkError] = useState<{
    readonly id: string;
    readonly message: string;
  } | null>(null);

  const handleUnlink = useCallback(
    async (kind: string, id: string): Promise<void> => {
      if (onUnlinkIdentity === undefined) return;
      setUnlinkError(null);
      setUnlinkingId(id);
      try {
        await onUnlinkIdentity(kind, id);
        // Success: the host refreshes `linkedIdentities`, dropping this row.
      } catch (err) {
        setUnlinkError({
          id,
          message:
            err instanceof Error && err.message !== ""
              ? err.message
              : "Could not unlink that sign-in method.",
        });
      } finally {
        setUnlinkingId(null);
      }
    },
    [onUnlinkIdentity],
  );

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
                  color: "var(--color-text-subtle)",
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
              placeholder={anchor.kind !== "email" ? "Add a name" : undefined}
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
          ) : anchor.kind === "device" ? (
            <>
              <div style={rowStyle}>
                <label htmlFor={anchorId} style={labelStyle}>
                  Account
                </label>
                <input
                  id={anchorId}
                  type="text"
                  value="This device"
                  readOnly
                  aria-readonly
                  style={readOnlyStyle}
                  data-testid="profile-device-anchor"
                />
              </div>
              <span style={noteStyle} data-testid="profile-device-note">
                Local account — everything stays on this device. Link a wallet
                or Google below to sign in with them too.
              </span>
            </>
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

        {/* Linked accounts (PRD FR-U1): every sign-in identity on the account.
            Rendered only when the host supplies the data; Link + Unlink render
            only when the host wires the flow (substrate-agnostic — callbacks
            out, data in). Unlink surfaces the FR-L5 last-method guard honestly;
            the wallet-link CTA owns the FR-U2 merge-confirm dialog below. */}
        {linkedIdentities !== undefined ? (
          <fieldset style={fieldsetStyle} data-testid="profile-linked-accounts">
            <legend style={legendStyle}>Linked accounts</legend>
            {linkedIdentities.length === 0 ? (
              <span style={noteStyle} data-testid="profile-linked-empty">
                No linked sign-in methods yet.
              </span>
            ) : (
              linkedIdentities.map((identity) => {
                const busy = unlinkingId === identity.id;
                return (
                  <div
                    key={identity.id}
                    style={{ ...rowStyle, gap: 8 }}
                    data-testid={`profile-linked-${identity.kind}`}
                  >
                    {identity.kind === "wallet" ? (
                      <>
                        <span style={labelStyle}>Wallet</span>
                        <input
                          type="text"
                          value={identity.address ?? ""}
                          readOnly
                          aria-readonly
                          style={monoReadOnlyStyle}
                        />
                        {identity.chainName ? (
                          <span style={chainChipStyle}>
                            {identity.chainName}
                          </span>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <span style={labelStyle}>
                          {identity.provider === "google"
                            ? "Google"
                            : (identity.provider ?? "SSO")}
                        </span>
                        <input
                          type="text"
                          value={identity.email ?? ""}
                          readOnly
                          aria-readonly
                          style={readOnlyStyle}
                        />
                      </>
                    )}
                    {onUnlinkIdentity !== undefined ? (
                      <button
                        type="button"
                        onClick={() => {
                          void handleUnlink(identity.kind, identity.id);
                        }}
                        disabled={busy}
                        aria-busy={busy}
                        style={{
                          ...unlinkButtonStyle,
                          opacity: busy ? 0.6 : 1,
                          cursor: busy ? "progress" : "pointer",
                        }}
                        data-testid={`profile-unlink-${identity.id}`}
                        title="Remove this sign-in method"
                      >
                        {busy ? "Unlinking…" : "Unlink"}
                      </button>
                    ) : null}
                    {unlinkError !== null && unlinkError.id === identity.id ? (
                      <span
                        style={inlineErrorStyle}
                        role="alert"
                        data-testid={`profile-unlink-error-${identity.id}`}
                      >
                        {unlinkError.message}
                      </span>
                    ) : null}
                  </div>
                );
              })
            )}
            {onLinkWallet !== undefined ? (
              <button
                type="button"
                onClick={() => {
                  void runWalletLink(false);
                }}
                disabled={linkState.kind === "busy"}
                aria-busy={linkState.kind === "busy"}
                style={{
                  ...linkCtaStyle,
                  opacity: linkState.kind === "busy" ? 0.6 : 1,
                  cursor: linkState.kind === "busy" ? "progress" : "pointer",
                }}
                data-testid="profile-link-wallet"
              >
                {linkState.kind === "busy"
                  ? "Linking wallet…"
                  : "Link a wallet"}
              </button>
            ) : null}
            {onLinkGoogle !== undefined &&
            !linkedIdentities.some((i) => i.provider === "google") ? (
              <button
                type="button"
                onClick={onLinkGoogle}
                style={linkCtaStyle}
                data-testid="profile-link-google"
              >
                {person.anchor.kind === "wallet" ||
                person.anchor.kind === "device"
                  ? "Add an email — continue with Google"
                  : "Link Google"}
              </button>
            ) : null}
            {linkState.kind === "error" ? (
              <span
                style={inlineErrorStyle}
                role="alert"
                data-testid="profile-link-error"
              >
                {linkState.message}
              </span>
            ) : null}
          </fieldset>
        ) : null}

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

      {/* Merge-confirm dialog (PRD FR-U2). Opened when a wallet link resolves
          to `merge_required`; confirming re-runs the link with consent (the
          host re-signs — the SIWE nonce is single-use). This is the ONLY
          client entry to the account-merge saga — the Google callback never
          merges (PRD §11 confused-deputy note). */}
      <Modal
        open={
          linkState.kind === "confirm-merge" || linkState.kind === "merging"
        }
        onClose={() => {
          // A merge in flight must not be dismissible mid-write.
          if (linkState.kind !== "merging") dismissMergeConfirm();
        }}
        title="Merge this account?"
        subtitle="account-linking · irreversible"
        closeLabel="Cancel merge"
        footer={
          <>
            <button
              type="button"
              onClick={dismissMergeConfirm}
              disabled={linkState.kind === "merging"}
              style={{
                ...dialogCancelStyle,
                opacity: linkState.kind === "merging" ? 0.6 : 1,
              }}
              data-testid="profile-merge-cancel"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                void runWalletLink(true);
              }}
              disabled={linkState.kind === "merging"}
              aria-busy={linkState.kind === "merging"}
              style={{
                ...dialogConfirmStyle,
                opacity: linkState.kind === "merging" ? 0.6 : 1,
                cursor: linkState.kind === "merging" ? "progress" : "pointer",
              }}
              data-testid="profile-merge-confirm"
            >
              {linkState.kind === "merging"
                ? "Merging…"
                : "Link & merge accounts"}
            </button>
          </>
        }
      >
        <p style={{ margin: 0 }} data-testid="profile-merge-message">
          {linkState.kind === "confirm-merge"
            ? linkState.message
            : DEFAULT_MERGE_MESSAGE}
        </p>
      </Modal>
    </div>
  );
}
