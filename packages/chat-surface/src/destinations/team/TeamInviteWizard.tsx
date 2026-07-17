// TeamInviteWizard — invite-teammate modal.
//
// Source: team-memory-cmdk-prd.md §7.1 (TeamInviteWizard). Single-form
// modal — email + role picker + optional note. Submits `InviteRequest`
// via the `onInvite` callback. On success, the wizard reveals the
// invite link copy-once via the shared <RevealOnce> primitive (same
// pattern Routines / Connectors use — DRY: no new reveal primitive).
//
// Pure presentation — the host owns transport, the wire's
// `POST /v1/team/invite` shape lives in `@0x-copilot/api-types`.
// The wizard does not call the network; the parent passes a fresh
// `onInvite` and the resulting "invite_link" plaintext.

import {
  useCallback,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactElement,
} from "react";

import type { InviteRequest, TeamRole } from "@0x-copilot/api-types";

import { RevealOnce } from "../connectors/RevealOnce";

const ASSIGNABLE_ROLES: ReadonlyArray<TeamRole> = ["admin", "member", "guest"];

const ROLE_LABEL: Readonly<Record<TeamRole, string>> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  guest: "Guest",
};

const ROLE_DESC: Readonly<Record<TeamRole, string>> = {
  owner:
    "Founding admin — read/write on all assets; protected from demotion while sole owner.",
  admin: "Full read/write on workspace assets + invite + offboarding.",
  member: "Default — reads tenant, writes own assets.",
  guest: "Limited — read-only on the workspace; project-scoped writes only.",
};

export interface TeamInviteWizardResult {
  /** The invite link to copy-once. Plaintext from the server response. */
  readonly invite_link: string;
}

export interface TeamInviteWizardProps {
  /**
   * Host transport. Receives the assembled `InviteRequest` once the
   * user clicks "Send invite". Resolves to the result envelope on
   * success (carrying the invite link to copy once), or `null` on
   * failure. The wizard renders the copy-once reveal locally.
   */
  readonly onInvite: (
    req: InviteRequest,
  ) => Promise<TeamInviteWizardResult | null>;
  /** Clipboard port — used by <RevealOnce>. */
  readonly onCopy: (text: string) => Promise<void>;
  /** Cancel — host owns the dirty-prompt confirm guard. */
  readonly onCancel?: () => void;
  /** Called once the user clicks "Done" in the success state. */
  readonly onDone?: () => void;
}

function isValidEmail(value: string): boolean {
  const trimmed = value.trim();
  // Accept anything with one `@` and at least one `.` in the host. The
  // server is the source of truth; this is the cheapest first-line gate.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed);
}

export function TeamInviteWizard(props: TeamInviteWizardProps): ReactElement {
  const { onInvite, onCopy, onCancel, onDone } = props;

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<TeamRole>("member");
  const [note, setNote] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<TeamInviteWizardResult | null>(null);

  const canSubmit = isValidEmail(email) && !submitting;

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!canSubmit) return;
      setSubmitting(true);
      setSubmitError(null);
      try {
        const req: InviteRequest = {
          email: email.trim(),
          role,
          ...(note.trim().length > 0 ? { note: note.trim() } : {}),
        };
        const response = await onInvite(req);
        if (response === null) {
          setSubmitError("Invite failed. Try again.");
        } else {
          setResult(response);
        }
      } finally {
        setSubmitting(false);
      }
    },
    [canSubmit, onInvite, email, role, note],
  );

  const handleDismissLink = useCallback(() => {
    setResult((prev) => (prev === null ? null : { ...prev, invite_link: "" }));
  }, []);

  return (
    <section
      role="dialog"
      aria-modal="true"
      aria-labelledby="team-invite-heading"
      data-testid="team-invite-wizard"
      style={containerStyle}
    >
      <header style={headerStyle}>
        <h2 id="team-invite-heading" style={titleStyle}>
          Invite teammate
        </h2>
        <p style={subtitleStyle}>
          Magic-link invite. Atlas signs the invite token; the link is shown{" "}
          <strong>once</strong> after send — copy it for the recipient.
        </p>
      </header>

      {result === null ? (
        <form onSubmit={handleSubmit} style={formStyle}>
          <label style={labelStyle}>
            <span style={labelTextStyle}>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="teammate@acme.test"
              required
              autoFocus
              style={inputStyle}
              data-testid="team-invite-email"
              aria-invalid={
                email.length > 0 && !isValidEmail(email) ? true : undefined
              }
            />
          </label>

          <fieldset style={fieldsetStyle}>
            <legend style={legendStyle}>Role</legend>
            {ASSIGNABLE_ROLES.map((r) => (
              <label
                key={r}
                style={radioLabelStyle}
                data-testid={`team-invite-role-${r}`}
              >
                <input
                  type="radio"
                  name="team-invite-role"
                  value={r}
                  checked={role === r}
                  onChange={() => setRole(r)}
                />
                <span style={radioTextStyle}>
                  <span style={radioLabelTextStyle}>{ROLE_LABEL[r]}</span>
                  <span style={radioDescriptionStyle}>{ROLE_DESC[r]}</span>
                </span>
              </label>
            ))}
          </fieldset>

          <label style={labelStyle}>
            <span style={labelTextStyle}>Note (optional)</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="Welcome to the team — looking forward to working with you."
              style={textareaStyle}
              data-testid="team-invite-note"
            />
          </label>

          {submitError !== null ? (
            <p role="alert" style={errorStyle} data-testid="team-invite-error">
              {submitError}
            </p>
          ) : null}

          <footer style={footerStyle}>
            {onCancel !== undefined ? (
              <button
                type="button"
                onClick={onCancel}
                style={ghostButtonStyle}
                data-testid="team-invite-cancel"
              >
                Cancel
              </button>
            ) : (
              <span />
            )}
            <button
              type="submit"
              disabled={!canSubmit}
              style={primaryButtonStyle}
              data-testid="team-invite-submit"
            >
              {submitting ? "Sending…" : "Send invite"}
            </button>
          </footer>
        </form>
      ) : (
        <div data-testid="team-invite-success" style={successStyle}>
          <p style={successHintStyle}>
            Invite sent. The recipient also gets a magic-link email — share the
            link below if you want to deliver it manually. Atlas will not show
            it again.
          </p>
          <RevealOnce
            value={result.invite_link.length > 0 ? result.invite_link : null}
            maskedPlaceholder="https://atlas/invite/••••••"
            label="invite link"
            onCopy={onCopy}
            onDismiss={handleDismissLink}
            testId="team-invite-link"
          />
          <footer style={footerStyle}>
            <span />
            <button
              type="button"
              onClick={() => onDone?.()}
              style={primaryButtonStyle}
              data-testid="team-invite-done"
            >
              Done
            </button>
          </footer>
        </div>
      )}
    </section>
  );
}

// === Styles ============================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 16,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: 10,
  boxSizing: "border-box",
  width: "100%",
  maxWidth: 480,
};

const headerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-lg, 18px)",
  fontWeight: 600,
};

const subtitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.55,
};

const formStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const labelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const labelTextStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const inputStyle: CSSProperties = {
  height: 34,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
};

const textareaStyle: CSSProperties = {
  padding: 10,
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  resize: "vertical",
  fontFamily: "inherit",
};

const fieldsetStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  border: "none",
  padding: 0,
  margin: 0,
};

const legendStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  marginBottom: 4,
};

const radioLabelStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 10,
  cursor: "pointer",
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
};

const radioTextStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const radioLabelTextStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
};

const radioDescriptionStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const errorStyle: CSSProperties = {
  margin: 0,
  padding: "8px 10px",
  background: "var(--color-danger-bg, #321a1a)",
  border: "1px solid var(--color-danger, #d97777)",
  borderRadius: "var(--radius-sm, 6px)",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text, #ededee)",
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  paddingTop: 10,
  borderTop: "1px solid var(--color-border, #232325)",
};

const primaryButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  background: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const ghostButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const successStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const successHintStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.55,
};
