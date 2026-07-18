// ProjectMembersTab — P6-B2
//
// Members list (role pills) + single-add dialog. Per Projects sub-PRD
// §12 Q9: NO BULK ADD in this phase — explicit single-member add only.
//
// This file is pure presentation. The host owns transport, fetch, and
// any feedback (toasts, error surfaces beyond inline). Callbacks are
// optional so the view degrades to read-only when `canManage` is false
// or when the host does not wire a mutation handler.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const ACCENT_CONTRAST = "var(--color-accent-contrast)";
const DANGER = "var(--color-danger)";
const AVATAR_BG = "var(--color-border-strong)";

export type ProjectMemberRole = "owner" | "editor" | "viewer";

export interface ProjectMember {
  readonly userId: string;
  readonly displayName: string;
  readonly email?: string;
  readonly role: ProjectMemberRole;
  readonly joinedAt: string;
  readonly avatarUrl?: string;
}

export interface ProjectMembersTabProps {
  /** `null` while loading; empty array means "loaded, no members". */
  readonly members: ReadonlyArray<ProjectMember> | null;
  /** Owner-or-admin viewer; gates add / role-change / remove controls. */
  readonly canManage: boolean;
  /** Used to render the owner pill in the row and to block role-
   *  change / remove on the owner (transfer ownership is a separate
   *  flow — see TransferOwnershipDialog). */
  readonly ownerUserId: string;

  readonly onAddMember?: (
    userIdentifier: string,
    role: ProjectMemberRole,
  ) => Promise<void>;
  readonly onRemoveMember?: (userId: string) => Promise<void>;
  readonly onChangeMemberRole?: (
    userId: string,
    role: ProjectMemberRole,
  ) => Promise<void>;
}

// ── Helpers ──────────────────────────────────────────────────────────

function initialsOf(name: string): string {
  const cleaned = name.trim();
  if (cleaned.length === 0) return "?";
  const parts = cleaned.split(/\s+/);
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

function rolePillStyle(role: ProjectMemberRole): CSSProperties {
  // Tone-mapping matches Atlas conventions: owner = accent; editor =
  // neutral-strong; viewer = neutral-subtle.
  if (role === "owner") {
    return {
      fontSize: "var(--font-size-2xs)",
      fontWeight: 600,
      padding: "2px 8px",
      borderRadius: 999,
      backgroundColor: "rgba(217,119,87,0.12)",
      color: ACCENT,
      border: `1px solid ${ACCENT}`,
    };
  }
  if (role === "editor") {
    return {
      fontSize: "var(--font-size-2xs)",
      fontWeight: 600,
      padding: "2px 8px",
      borderRadius: 999,
      backgroundColor: PANEL_BACKGROUND,
      color: TEXT_PRIMARY,
      border: `1px solid ${PANEL_BORDER_STRONG}`,
    };
  }
  return {
    fontSize: "var(--font-size-2xs)",
    fontWeight: 500,
    padding: "2px 8px",
    borderRadius: 999,
    backgroundColor: "transparent",
    color: TEXT_SECONDARY,
    border: `1px solid ${PANEL_BORDER}`,
  };
}

function Avatar({
  displayName,
  avatarUrl,
}: {
  displayName: string;
  avatarUrl?: string;
}): ReactElement {
  const style: CSSProperties = {
    width: 32,
    height: 32,
    borderRadius: "50%",
    backgroundColor: AVATAR_BG,
    color: TEXT_PRIMARY,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    flexShrink: 0,
    overflow: "hidden",
  };
  if (avatarUrl !== undefined && avatarUrl.length > 0) {
    return (
      <img
        src={avatarUrl}
        alt={displayName}
        style={{ ...style, objectFit: "cover" }}
      />
    );
  }
  return (
    <div role="img" aria-label={displayName} style={style}>
      {initialsOf(displayName)}
    </div>
  );
}

// ── Add member dialog (single-add per Projects sub-PRD §12 Q9) ───────

interface AddMemberDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly onSubmit: (
    userIdentifier: string,
    role: ProjectMemberRole,
  ) => Promise<void>;
}

function AddMemberDialog({
  open,
  onClose,
  onSubmit,
}: AddMemberDialogProps): ReactElement | null {
  const [identifier, setIdentifier] = useState("");
  const [role, setRole] = useState<ProjectMemberRole>("editor");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setIdentifier("");
      setRole("editor");
      setError(null);
      setSubmitting(false);
      // Defer focus to next tick so the input is mounted.
      const t = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [open]);

  const handleSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      if (event !== undefined) event.preventDefault();
      const trimmed = identifier.trim();
      if (trimmed.length === 0) return;
      setSubmitting(true);
      setError(null);
      try {
        await onSubmit(trimmed, role);
        onClose();
      } catch (e) {
        const message = e instanceof Error ? e.message : "Failed to add member";
        setError(message);
        setSubmitting(false);
      }
    },
    [identifier, onClose, onSubmit, role],
  );

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    },
    [onClose],
  );

  if (!open) return null;

  const backdrop: CSSProperties = {
    position: "fixed",
    inset: 0,
    backgroundColor: "rgba(0,0,0,0.55)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  };
  const card: CSSProperties = {
    width: 420,
    maxWidth: "calc(100vw - 32px)",
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 12,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  };
  const labelStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_SECONDARY,
    fontWeight: 500,
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
  const selectStyle: CSSProperties = {
    ...inputStyle,
    paddingRight: 28,
  };
  const buttonRow: CSSProperties = {
    display: "flex",
    justifyContent: "flex-end",
    gap: 8,
    marginTop: 4,
  };
  const submitStyle: CSSProperties = {
    height: 34,
    padding: "0 14px",
    borderRadius: 8,
    border: "none",
    backgroundColor: ACCENT,
    color: ACCENT_CONTRAST,
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    cursor: "pointer",
    opacity: submitting || identifier.trim().length === 0 ? 0.6 : 1,
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

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-member-title"
      style={backdrop}
      data-testid="project-add-member-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form style={card} onSubmit={handleSubmit}>
        <h2
          id="add-member-title"
          style={{
            margin: 0,
            fontSize: "var(--font-size-lg)",
            fontWeight: 600,
          }}
        >
          Add member
        </h2>
        <div style={{ fontSize: "var(--font-size-xs)", color: TEXT_FAINT }}>
          Add a single workspace user to this project. Bulk add is not available
          in this phase.
        </div>
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>Email or user id</span>
          <input
            ref={inputRef}
            type="text"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={submitting}
            style={inputStyle}
            data-testid="project-add-member-input"
            aria-label="Email or user id"
            placeholder="user@example.com or user-id"
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>Role</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as ProjectMemberRole)}
            disabled={submitting}
            style={selectStyle}
            data-testid="project-add-member-role"
            aria-label="Role"
          >
            <option value="editor">Editor</option>
            <option value="viewer">Viewer</option>
          </select>
        </label>
        {error !== null ? (
          <div
            role="alert"
            style={{ color: DANGER, fontSize: "var(--font-size-xs)" }}
            data-testid="project-add-member-error"
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
            data-testid="project-add-member-cancel"
          >
            Cancel
          </button>
          <button
            type="submit"
            style={submitStyle}
            disabled={submitting || identifier.trim().length === 0}
            data-testid="project-add-member-submit"
          >
            {submitting ? "Adding…" : "Add member"}
          </button>
        </div>
      </form>
    </div>
  );
}

// ── Row ──────────────────────────────────────────────────────────────

interface MemberRowProps {
  readonly member: ProjectMember;
  readonly canManage: boolean;
  readonly ownerUserId: string;
  readonly onRemoveMember?: (userId: string) => Promise<void>;
  readonly onChangeMemberRole?: (
    userId: string,
    role: ProjectMemberRole,
  ) => Promise<void>;
}

function MemberRow({
  member,
  canManage,
  ownerUserId,
  onRemoveMember,
  onChangeMemberRole,
}: MemberRowProps): ReactElement {
  const [pendingRole, setPendingRole] = useState(false);
  const [pendingRemove, setPendingRemove] = useState(false);

  const isOwner = member.userId === ownerUserId;

  const row: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "10px 12px",
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 10,
    backgroundColor: PANEL_BACKGROUND,
  };
  const nameStyle: CSSProperties = {
    fontSize: "var(--font-size-md)",
    fontWeight: 500,
    color: TEXT_PRIMARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const subStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_FAINT,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const selectStyle: CSSProperties = {
    height: 28,
    padding: "0 8px",
    paddingRight: 24,
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-xs)",
  };
  const removeBtn: CSSProperties = {
    height: 28,
    padding: "0 10px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: "transparent",
    color: DANGER,
    fontSize: "var(--font-size-xs)",
    cursor: "pointer",
  };

  const handleRoleChange = async (next: ProjectMemberRole): Promise<void> => {
    if (onChangeMemberRole === undefined) return;
    setPendingRole(true);
    try {
      await onChangeMemberRole(member.userId, next);
    } finally {
      setPendingRole(false);
    }
  };

  const handleRemove = async (): Promise<void> => {
    if (onRemoveMember === undefined) return;
    setPendingRemove(true);
    try {
      await onRemoveMember(member.userId);
    } finally {
      setPendingRemove(false);
    }
  };

  return (
    <li
      style={row}
      data-testid="project-member-row"
      data-user-id={member.userId}
      data-role={member.role}
    >
      <Avatar displayName={member.displayName} avatarUrl={member.avatarUrl} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={nameStyle}>{member.displayName}</div>
        {member.email !== undefined ? (
          <div style={subStyle}>{member.email}</div>
        ) : null}
      </div>
      {isOwner ? (
        <span
          style={rolePillStyle("owner")}
          data-testid="project-member-role-pill"
        >
          Owner
        </span>
      ) : canManage && onChangeMemberRole !== undefined ? (
        <select
          value={member.role}
          disabled={pendingRole}
          onChange={(e) =>
            void handleRoleChange(e.target.value as ProjectMemberRole)
          }
          style={selectStyle}
          data-testid="project-member-role-select"
          aria-label={`Change role for ${member.displayName}`}
        >
          <option value="editor">Editor</option>
          <option value="viewer">Viewer</option>
        </select>
      ) : (
        <span
          style={rolePillStyle(member.role)}
          data-testid="project-member-role-pill"
        >
          {member.role === "editor" ? "Editor" : "Viewer"}
        </span>
      )}
      {canManage && !isOwner && onRemoveMember !== undefined ? (
        <button
          type="button"
          onClick={() => void handleRemove()}
          disabled={pendingRemove}
          style={removeBtn}
          data-testid="project-member-remove"
          aria-label={`Remove ${member.displayName}`}
        >
          Remove
        </button>
      ) : null}
    </li>
  );
}

// ── Tab body ─────────────────────────────────────────────────────────

export function ProjectMembersTab(props: ProjectMembersTabProps): ReactElement {
  const {
    members,
    canManage,
    ownerUserId,
    onAddMember,
    onRemoveMember,
    onChangeMemberRole,
  } = props;

  const [dialogOpen, setDialogOpen] = useState(false);

  const wrapper: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 12,
  };
  const header: CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  };
  const list: CSSProperties = {
    listStyle: "none",
    padding: 0,
    margin: 0,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const addBtn: CSSProperties = {
    height: 32,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    cursor: "pointer",
  };
  const skeletonRow: CSSProperties = {
    height: 56,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    opacity: 0.6,
  };
  const emptyStyle: CSSProperties = {
    padding: 24,
    border: `1px dashed ${PANEL_BORDER_STRONG}`,
    borderRadius: 10,
    textAlign: "center",
    color: TEXT_SECONDARY,
    fontSize: "var(--font-size-sm)",
  };

  return (
    <section
      data-testid="project-members-tab"
      data-state={members === null ? "loading" : "ready"}
      style={wrapper}
    >
      <div style={header}>
        <div style={{ fontSize: "var(--font-size-md)", fontWeight: 600 }}>
          Members
        </div>
        {canManage && onAddMember !== undefined ? (
          <button
            type="button"
            style={addBtn}
            onClick={() => setDialogOpen(true)}
            data-testid="project-members-add-trigger"
          >
            + Add member
          </button>
        ) : null}
      </div>

      {members === null ? (
        <ul style={list} aria-busy="true">
          {Array.from({ length: 3 }).map((_, i) => (
            <li
              key={i}
              style={skeletonRow}
              data-testid="project-members-skeleton"
              aria-hidden="true"
            />
          ))}
        </ul>
      ) : members.length === 0 ? (
        <div style={emptyStyle} data-testid="project-members-empty">
          No members yet.
        </div>
      ) : (
        <ul style={list} data-testid="project-members-list">
          {members.map((m) => (
            <MemberRow
              key={m.userId}
              member={m}
              canManage={canManage}
              ownerUserId={ownerUserId}
              onRemoveMember={onRemoveMember}
              onChangeMemberRole={onChangeMemberRole}
            />
          ))}
        </ul>
      )}

      {onAddMember !== undefined ? (
        <AddMemberDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          onSubmit={onAddMember}
        />
      ) : null}
    </section>
  );
}
