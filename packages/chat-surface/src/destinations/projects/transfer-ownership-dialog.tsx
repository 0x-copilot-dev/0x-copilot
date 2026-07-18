// TransferOwnershipDialog — P6-B2
//
// Owner force-transfer flow. Per user green-lit Q1 (Projects sub-PRD
// §12): SHIP the transfer in this phase. The dialog is gated on:
//   1. Picking a new owner from the existing member list.
//   2. Typing the project name to confirm (destructive-style guard).
// We render a warning StatusPill ("Warning") in the header — the
// receiving member becomes owner, and the current viewer (assumed to
// be the outgoing owner) loses write permissions on the project.
//
// Pure presentation; host owns the actual API call (`onTransfer`).
//
// File-naming convention follows the kebab-case form for dialog files
// in this repo (cf. composer/mention-popover.tsx style); component is
// PascalCase.

import {
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactElement,
} from "react";

import type { ProjectMember } from "./ProjectMembersTab";

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT_CONTRAST = "var(--color-accent-contrast)";
const DANGER = "var(--color-danger)";

export interface TransferOwnershipDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;

  /** The project's current name (must be typed to confirm). */
  readonly projectName: string;
  /** Current owner — excluded from the candidate list. */
  readonly currentOwnerUserId: string;
  /** Candidate members. Excludes the current owner client-side as a
   *  safety net; the host should also enforce this server-side. */
  readonly candidates: ReadonlyArray<ProjectMember>;

  /** Called with the new owner's userId once both gates pass. The
   *  host performs the API call; the dialog closes when the promise
   *  resolves and surfaces the error message inline if it rejects. */
  readonly onTransfer: (newOwnerUserId: string) => Promise<void>;
}

// ── Warning pill ─────────────────────────────────────────────────────
// Inline StatusPill in "warning" tone — matches the design-system
// idiom (dot + label) without taking a hard CSS-class dependency
// during unit tests.

function WarningPill(): ReactElement {
  const style: CSSProperties = {
    fontSize: "var(--font-size-2xs)",
    fontWeight: 600,
    padding: "2px 10px",
    borderRadius: 999,
    backgroundColor: "rgba(245,158,11,0.14)",
    color: "rgb(251,191,36)",
    border: "1px solid rgb(251,191,36)",
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  };
  return (
    <span
      style={style}
      data-testid="transfer-ownership-warning-pill"
      role="status"
      aria-label="Warning"
    >
      <span
        aria-hidden="true"
        style={{
          width: 6,
          height: 6,
          borderRadius: 999,
          backgroundColor: "rgb(251,191,36)",
        }}
      />
      Warning
    </span>
  );
}

// ── Dialog ───────────────────────────────────────────────────────────

export function TransferOwnershipDialog(
  props: TransferOwnershipDialogProps,
): ReactElement | null {
  const {
    open,
    onClose,
    projectName,
    currentOwnerUserId,
    candidates,
    onTransfer,
  } = props;

  const eligible = candidates.filter((m) => m.userId !== currentOwnerUserId);

  const [selectedUserId, setSelectedUserId] = useState<string>("");
  const [confirmName, setConfirmName] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset every time the dialog opens.
  useEffect(() => {
    if (open) {
      setSelectedUserId("");
      setConfirmName("");
      setSubmitting(false);
      setError(null);
    }
  }, [open]);

  const canConfirm =
    selectedUserId.length > 0 && confirmName.trim() === projectName.trim();

  const handleSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>): Promise<void> => {
      if (event !== undefined) event.preventDefault();
      if (!canConfirm) return;
      setSubmitting(true);
      setError(null);
      try {
        await onTransfer(selectedUserId);
        onClose();
      } catch (e) {
        const message =
          e instanceof Error ? e.message : "Failed to transfer ownership";
        setError(message);
        setSubmitting(false);
      }
    },
    [canConfirm, onClose, onTransfer, selectedUserId],
  );

  if (!open) return null;

  const backdrop: CSSProperties = {
    position: "fixed",
    inset: 0,
    backgroundColor: "rgba(0,0,0,0.6)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1100,
  };
  const card: CSSProperties = {
    width: 480,
    maxWidth: "calc(100vw - 32px)",
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 12,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 14,
  };
  const headerRow: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
  };
  const titleStyle: CSSProperties = {
    margin: 0,
    fontSize: "var(--font-size-lg)",
    fontWeight: 600,
  };
  const labelStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_SECONDARY,
    fontWeight: 500,
  };
  const selectStyle: CSSProperties = {
    height: 36,
    padding: "0 12px",
    paddingRight: 28,
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-sm)",
  };
  const inputStyle: CSSProperties = {
    height: 36,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-sm)",
    outline: "none",
  };
  const cancelStyle: CSSProperties = {
    height: 34,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: "transparent",
    color: TEXT_SECONDARY,
    fontSize: "var(--font-size-sm)",
    cursor: "pointer",
  };
  const submitStyle: CSSProperties = {
    height: 34,
    padding: "0 14px",
    borderRadius: 8,
    border: "none",
    backgroundColor: DANGER,
    color: ACCENT_CONTRAST,
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    cursor: "pointer",
    opacity: !canConfirm || submitting ? 0.6 : 1,
  };
  const buttonRow: CSSProperties = {
    display: "flex",
    justifyContent: "flex-end",
    gap: 8,
    marginTop: 4,
  };
  const warningBlock: CSSProperties = {
    padding: 10,
    borderRadius: 8,
    backgroundColor: "rgba(245,158,11,0.06)",
    border: "1px solid rgba(245,158,11,0.3)",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-xs)",
    lineHeight: 1.5,
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="transfer-ownership-title"
      style={backdrop}
      data-testid="transfer-ownership-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form style={card} onSubmit={handleSubmit}>
        <div style={headerRow}>
          <h2 id="transfer-ownership-title" style={titleStyle}>
            Transfer ownership
          </h2>
          <WarningPill />
        </div>

        <div style={warningBlock} data-testid="transfer-ownership-warning">
          You are about to transfer ownership of <strong>{projectName}</strong>.
          The new owner will gain full write access; you will lose owner-only
          permissions on this project. This cannot be undone without the new
          owner&apos;s cooperation.
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>New owner</span>
          <select
            value={selectedUserId}
            onChange={(e) => setSelectedUserId(e.target.value)}
            disabled={submitting || eligible.length === 0}
            style={selectStyle}
            data-testid="transfer-ownership-candidate"
            aria-label="New owner"
          >
            <option value="">— Select a member —</option>
            {eligible.map((m) => (
              <option key={m.userId} value={m.userId}>
                {m.displayName}
                {m.email !== undefined ? ` (${m.email})` : ""}
              </option>
            ))}
          </select>
          {eligible.length === 0 ? (
            <span
              style={{ fontSize: "var(--font-size-2xs)", color: TEXT_FAINT }}
              data-testid="transfer-ownership-no-candidates"
            >
              No eligible members. Add a member first.
            </span>
          ) : null}
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>
            Type <strong>{projectName}</strong> to confirm
          </span>
          <input
            type="text"
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
            disabled={submitting}
            style={inputStyle}
            data-testid="transfer-ownership-confirm-input"
            aria-label="Confirm project name"
            placeholder={projectName}
          />
        </label>

        {error !== null ? (
          <div
            role="alert"
            style={{ color: DANGER, fontSize: "var(--font-size-xs)" }}
            data-testid="transfer-ownership-error"
          >
            {error}
          </div>
        ) : null}

        <div style={buttonRow}>
          <button
            type="button"
            style={cancelStyle}
            onClick={onClose}
            disabled={submitting}
            data-testid="transfer-ownership-cancel"
          >
            Cancel
          </button>
          <button
            type="submit"
            style={submitStyle}
            disabled={!canConfirm || submitting}
            data-testid="transfer-ownership-confirm"
            aria-label="Confirm transfer ownership"
          >
            {submitting ? "Transferring…" : "Transfer ownership"}
          </button>
        </div>
      </form>
    </div>
  );
}
