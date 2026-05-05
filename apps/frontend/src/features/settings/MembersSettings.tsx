// PR 4.2 — Settings → Members panel.
//
// Three columns of state, all admin-only:
//   - Members table (active rows joined from list_members + list_users).
//   - Pending invitations list with revoke action.
//   - Invite modal (mints a one-time token, surfaces it once with a Copy
//     button and a render-only accept_url).
//
// Role change + remove use the existing design-system <Menu> as the row's
// kebab menu. Member view (non-admin) still ships read-only.

import {
  Badge,
  Button,
  Card,
  Field,
  IconButton,
  Menu,
  Select,
  TextInput,
} from "@enterprise-search/design-system";
import {
  type FormEvent,
  type ReactElement,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  CreateInvitationResponse,
  Member,
  WorkspaceRoleName,
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "../../api/config";
import { Modal } from "./Modal";
import { useInvitations, useWorkspaceMembers } from "./useWorkspace";

const ROLE_LABELS: Record<WorkspaceRoleName, string> = {
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

const ROLE_OPTIONS: WorkspaceRoleName[] = ["admin", "member", "viewer"];

export function MembersSettings({
  identity,
  isAdmin,
}: {
  identity: RequestIdentity;
  isAdmin: boolean;
}): ReactElement {
  const members = useWorkspaceMembers(identity);
  const invitations = useInvitations(identity);
  const [inviteOpen, setInviteOpen] = useState(false);

  return (
    <div className="settings-section" data-section="members">
      <header className="settings-section__header">
        <div>
          <h2>Members</h2>
          <p className="settings-section__hint">
            Roles, invites, and last-active.{" "}
            {isAdmin ? null : "Read-only for members."}
          </p>
        </div>
        {isAdmin ? (
          <Button
            type="button"
            variant="primary"
            onClick={() => setInviteOpen(true)}
          >
            Invite member
          </Button>
        ) : null}
      </header>

      <Card>
        <MembersTable
          members={members.members}
          loading={members.loading}
          error={members.error}
          isAdmin={isAdmin}
          ownUserId={identity.userId}
          onChangeRole={(uid, role) => members.changeRole(uid, role)}
          onRemove={(uid) => members.remove(uid)}
        />
      </Card>

      {isAdmin ? (
        <Card data-section="pending-invitations">
          <h3>Pending invitations</h3>
          <PendingInvitationsList
            invitations={invitations.invitations}
            loading={invitations.loading}
            error={invitations.error}
            onRevoke={(id) => invitations.revoke(id)}
          />
        </Card>
      ) : null}

      <Modal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        title="Invite a member"
        description="Mint a one-time link. The token is shown once on screen."
      >
        <InviteForm
          onCancel={() => setInviteOpen(false)}
          onCreated={async (response) => {
            // Append already happened inside `create()` but keep the modal
            // open so the admin can copy the token.
            return response;
          }}
          create={(body) => invitations.create(body)}
        />
      </Modal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Members table
// ---------------------------------------------------------------------------

function MembersTable({
  members,
  loading,
  error,
  isAdmin,
  ownUserId,
  onChangeRole,
  onRemove,
}: {
  members: Member[];
  loading: boolean;
  error: string | null;
  isAdmin: boolean;
  ownUserId: string;
  onChangeRole: (userId: string, role: WorkspaceRoleName) => Promise<void>;
  onRemove: (userId: string) => Promise<void>;
}): ReactElement {
  if (loading) return <p>Loading members…</p>;
  if (error) return <p data-testid="members-error">{error}</p>;
  if (members.length === 0) return <p>No members yet.</p>;

  return (
    <table className="settings-table" data-testid="members-table">
      <thead>
        <tr>
          <th scope="col">Member</th>
          <th scope="col">Role</th>
          <th scope="col">Source</th>
          <th scope="col">Last active</th>
          {isAdmin ? <th aria-label="Actions" scope="col" /> : null}
        </tr>
      </thead>
      <tbody>
        {members.map((member) => (
          <MemberRow
            key={member.user_id}
            member={member}
            isAdmin={isAdmin}
            isSelf={member.user_id === ownUserId}
            onChangeRole={onChangeRole}
            onRemove={onRemove}
          />
        ))}
      </tbody>
    </table>
  );
}

function MemberRow({
  member,
  isAdmin,
  isSelf,
  onChangeRole,
  onRemove,
}: {
  member: Member;
  isAdmin: boolean;
  isSelf: boolean;
  onChangeRole: (userId: string, role: WorkspaceRoleName) => Promise<void>;
  onRemove: (userId: string) => Promise<void>;
}): ReactElement {
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLDivElement>(null);

  const role = member.role?.name ?? "member";

  async function changeRole(next: WorkspaceRoleName): Promise<void> {
    if (next === role) {
      setMenuOpen(false);
      return;
    }
    setPending(true);
    setError(null);
    try {
      await onChangeRole(member.user_id, next);
      setMenuOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update role");
    } finally {
      setPending(false);
    }
  }

  async function remove(): Promise<void> {
    setPending(true);
    setError(null);
    try {
      await onRemove(member.user_id);
      setConfirmingRemove(false);
      setMenuOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove member");
    } finally {
      setPending(false);
    }
  }

  return (
    <tr data-testid="member-row" data-user={member.user_id}>
      <td>
        <div className="member-row__identity">
          <span className="member-row__name">
            {member.display_name || member.email}
          </span>
          <span className="member-row__email">{member.email}</span>
          {member.title ? (
            <span className="member-row__title">{member.title}</span>
          ) : null}
        </div>
      </td>
      <td>
        {member.role ? (
          <Badge tone={role === "admin" ? "accent" : "neutral"}>
            {ROLE_LABELS[role]}
          </Badge>
        ) : (
          <span className="member-row__no-role">no role</span>
        )}
      </td>
      <td>
        <span className="member-row__source">{member.source}</span>
      </td>
      <td>
        {member.last_seen_at
          ? new Date(member.last_seen_at).toLocaleString()
          : "—"}
      </td>
      {isAdmin ? (
        <td className="member-row__actions">
          <div ref={triggerRef} className="member-row__trigger">
            <IconButton
              type="button"
              aria-label="Member actions"
              title="Member actions"
              onClick={() => setMenuOpen((v) => !v)}
              disabled={pending}
            >
              …
            </IconButton>
          </div>
          <Menu
            open={menuOpen}
            onClose={() => setMenuOpen(false)}
            anchorRef={triggerRef}
            align="right"
          >
            <div className="member-row__menu">
              <span className="member-row__menu-label">Change role</span>
              {ROLE_OPTIONS.map((option) => (
                <button
                  key={option}
                  type="button"
                  className="member-row__menu-item"
                  data-active={option === role || undefined}
                  onClick={() => void changeRole(option)}
                  disabled={pending}
                >
                  {ROLE_LABELS[option]}
                </button>
              ))}
              <hr />
              <button
                type="button"
                className="member-row__menu-item member-row__menu-item--danger"
                onClick={() => setConfirmingRemove(true)}
                disabled={pending || isSelf}
                title={
                  isSelf
                    ? "You cannot remove yourself."
                    : "Remove from workspace"
                }
              >
                Remove from workspace
              </button>
              {error ? (
                <span className="member-row__menu-error">{error}</span>
              ) : null}
            </div>
          </Menu>

          <Modal
            open={confirmingRemove}
            onClose={() => setConfirmingRemove(false)}
            title={`Remove ${member.display_name || member.email}?`}
            description="The member will lose access immediately. Existing chats stay visible to admins."
            footer={
              <>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setConfirmingRemove(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  onClick={() => void remove()}
                  disabled={pending}
                >
                  {pending ? "Removing…" : "Remove"}
                </Button>
              </>
            }
          >
            <p>
              They will be marked removed and can be re-invited later. Their
              past activity remains in the audit log.
            </p>
          </Modal>
        </td>
      ) : null}
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Invite modal form
// ---------------------------------------------------------------------------

function InviteForm({
  create,
  onCancel,
  onCreated,
}: {
  create: (body: {
    email: string;
    role: WorkspaceRoleName;
  }) => Promise<CreateInvitationResponse>;
  onCancel: () => void;
  onCreated: (
    response: CreateInvitationResponse,
  ) => Promise<CreateInvitationResponse>;
}): ReactElement {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRoleName>("member");
  const [pending, setPending] = useState(false);
  const [response, setResponse] = useState<CreateInvitationResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (response) {
      onCancel();
      return;
    }
    if (!email.trim()) return;
    setPending(true);
    setError(null);
    try {
      const created = await create({ email: email.trim(), role });
      const finalResponse = await onCreated(created);
      setResponse(finalResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not invite");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="invite-form">
      {response ? (
        <InviteSuccess response={response} onClose={onCancel} />
      ) : (
        <>
          <Field label="Email" hint="They'll receive a one-time accept link.">
            <TextInput
              type="email"
              required
              autoFocus
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="teammate@example.com"
            />
          </Field>
          <Field label="Role">
            <Select
              value={role}
              onChange={(event) =>
                setRole(event.target.value as WorkspaceRoleName)
              }
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {ROLE_LABELS[option]}
                </option>
              ))}
            </Select>
          </Field>
          {error ? <Badge tone="danger">{error}</Badge> : null}
          <div className="invite-form__actions">
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={pending}>
              {pending ? "Inviting…" : "Send invitation"}
            </Button>
          </div>
        </>
      )}
    </form>
  );
}

function InviteSuccess({
  response,
  onClose,
}: {
  response: CreateInvitationResponse;
  onClose: () => void;
}): ReactElement {
  const [copied, setCopied] = useState(false);
  const link = response.accept_url ?? response.token;

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: leave the value visible so the user can manually copy.
    }
  }

  return (
    <div className="invite-form__success">
      <p>
        Invitation minted for <strong>{response.email}</strong>. Copy the link
        now — it's shown <strong>once</strong>.
      </p>
      <code className="invite-form__token" data-testid="invite-token">
        {link}
      </code>
      <div className="invite-form__actions">
        <Button type="button" variant="primary" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy link"}
        </Button>
        <Button type="button" variant="ghost" onClick={onClose}>
          Done
        </Button>
      </div>
      <p className="invite-form__hint">
        Token prefix <code>{response.token_prefix}</code> · expires{" "}
        {new Date(response.expires_at).toLocaleString()}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pending invitations list
// ---------------------------------------------------------------------------

function PendingInvitationsList({
  invitations,
  loading,
  error,
  onRevoke,
}: {
  invitations: ReturnType<typeof useInvitations>["invitations"];
  loading: boolean;
  error: string | null;
  onRevoke: (id: string) => Promise<void>;
}): ReactElement {
  if (loading) return <p>Loading invitations…</p>;
  if (error) return <p data-testid="invitations-error">{error}</p>;
  if (invitations.length === 0) return <p>No pending invitations.</p>;

  const sorted = useMemo(
    () =>
      [...invitations].sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [invitations],
  );

  return (
    <ul className="invitations-list" data-testid="invitations-list">
      {sorted.map((invitation) => (
        <li key={invitation.invite_id} className="invitations-list__row">
          <div>
            <span className="invitations-list__email">{invitation.email}</span>
            <span className="invitations-list__meta">
              <Badge tone={invitation.role === "admin" ? "accent" : "neutral"}>
                {ROLE_LABELS[invitation.role]}
              </Badge>
              <span>
                Invited by {invitation.created_by.display_name ?? "—"} · expires{" "}
                {new Date(invitation.expires_at).toLocaleDateString()}
              </span>
            </span>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void onRevoke(invitation.invite_id)}
          >
            Revoke
          </Button>
        </li>
      ))}
    </ul>
  );
}
