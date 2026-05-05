# PR 4.2 — Settings expansion · "Workspace" group (Workspace · Members · Billing)

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 4, PR 4.2 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** backend (workspace branding routes + invitations table & flow + members read/write + billing read) · backend-facade (proxy + admin guard) · frontend (3 settings sections) · api-types (5 types)
> **Size:** **M.** One new table (`invitations`), one workspace-rename route, three member routes, two billing read routes (plan + invoices), three FE sections. Default-model / default-connectors / retention controls **reuse PR 1.6** unchanged. Audit-log shortcut links to PR 7.1.
> **Depends on:** PR 1.6 workspace defaults (✅ — Workspace settings panel writes to `/v1/agent/workspace/defaults`) · PR 4.1 user_profiles sidecar (the directory under Members reads display_name / title / timezone from there)
> **Reads alongside:** [`pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) (default model / default connectors / retention surface), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`services/backend/CLAUDE.md`](../../services/backend/CLAUDE.md), [`services/backend-facade/CLAUDE.md`](../../services/backend-facade/CLAUDE.md)
> **Sibling docs (Wave 4):** [`pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md) · [`pr-4.3-settings-ai-and-data.md`](pr-4.3-settings-ai-and-data.md) · [`pr-4.4-mcp-overlay-test-connection.md`](pr-4.4-mcp-overlay-test-connection.md) · [`pr-4.5-usage-overlay-share-popover.md`](pr-4.5-usage-overlay-share-popover.md)

---

## 0 · TL;DR

Three Settings sections that already have most of their persistence in the tree — we wire endpoints + UI to existing tables, and add **one** new schema (invitations).

| Section            | Backend reuse                                                                                           | Backend new                                    | FE                                                                          |
| ------------------ | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------- |
| Workspace settings | `organizations` (display_name/slug/metadata) · PR 1.6 `workspace_defaults` (model/connectors/retention) | `PATCH /v1/workspace`                          | One form panel; defaults-form delegates to PR 1.6 hook                      |
| Members            | `organization_members` · `roles` · `role_assignments` · `users` · `user_profiles` (PR 4.1)              | `invitations` table + 4 routes                 | Members table + Invite modal + Role select + Pending-invite list            |
| Billing            | `usage_daily_rollups` (existing) · `usage_budgets` (existing)                                           | `GET /v1/workspace/billing` (read-only digest) | Plan card + Seats count + Usage trend (PR 4.5 chart) + Invoices placeholder |

We deliberately **do not** ship: workspace deletion (defer with confirm-only stub), Stripe webhooks, custom-role editor (system roles only in v1), audit-log table view (PR 7.1).

LoC estimate: backend ≈ 380 (1 migration + 4 invitation routes + 3 member routes + 1 workspace route + 1 billing route + audit actions + tests) · backend-facade ≈ 110 · api-types ≈ 80 · frontend ≈ 720 (3 sections + table + modal + 3 hooks).

---

## 1 · PRD

### 1.1 Problem

The Atlas design doc (Settings → "Workspace" group) requires three admin panels:

| Panel                                                                                              | Today                                                                                                                                               |
| -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Workspace** — name, slug, logo, default model, default connectors, retention policy, danger zone | Schema exists (`organizations`); rename/slug/logo route is missing; default model/connectors/retention shipped via PR 1.6                           |
| **Members** — Admin/Member/Viewer roles, invite link, pending invites, audit-log shortcut          | Schema exists (`organization_members`, `roles`, `role_assignments`); **no invite link mechanism**; **no read endpoint** for the admin members table |
| **Billing** — plan card, usage chart, seats, payment method, invoices                              | `usage_daily_rollups` + `usage_budgets` already populate; no plan/seats/invoices route exists; payment integration deferred                         |

The blocker for an admin running a real workspace today is **invitations**: the SCIM path covers identity-providered orgs, but the design doc's "invite a teammate by email link" path is missing. SCIM tokens are the closest pattern in the tree (see `0015_scim_provisioning.sql:34-49`); we mirror it for invitations.

The other two panels are mostly UI over existing data. Workspace branding needs a single rename/slug/logo route. Billing in v1 reads existing usage + a static plan card; the Stripe-integration end is its own follow-up.

### 1.2 Goals

1. **Workspace settings** — admin can rename (`display_name`), change `slug` (with uniqueness check), set logo URL (`metadata.logo_url`), see/edit default model + default connectors + retention (delegates to PR 1.6 endpoints, not duplicated).
2. **Members** — admin can list members with role + last-active + email-verified; invite by email (mints a one-time token, stores hash, emails / link-copies); revoke pending invites; change role; remove member (sets `removed_at`, **not** destructive).
3. **Billing** — admin sees plan tier (from deployment profile in v1), seat count (`COUNT(organization_members)` minus `removed_at IS NOT NULL`), and 30-day usage trend (consumes the chart from PR 4.5). Invoices are placeholder rows; payment-provider integration ships separately.
4. **No per-workspace audit-log table** in this PR. The "Audit log" link in the design appears, points to PR 7.1's eventual surface; until 7.1 ships, the link disables with tooltip "coming soon" or routes to the SIEM-export download (admin-only) which already exists.
5. **Re-use PR 1.6 fully**: the Workspace panel does not implement default-model / default-connectors / retention. It calls the existing `/v1/agent/workspace/defaults` endpoint with the existing types.
6. **Streaming and runtime untouched.** Audit chain extends with five new actions; no new event family.

### 1.3 Non-goals

- **Custom roles.** The three system roles (Admin / Member / Viewer) are sufficient for v1. Custom-role editor + permission_scopes UI is out of scope.
- **Workspace deletion.** UI shows a danger-zone confirmation; the actual cascade-delete job ships behind a feature flag in a follow-up. v1 stub returns 501 with a copy "contact support."
- **Stripe / invoice integration.** v1 reads a hard-coded plan tier from `deployment_profile.plan_tier`; invoices return an empty list; payment method displays "managed externally."
- **Avatar/logo upload pipeline.** v1 stores URLs only (matches PR 4.1's avatar decision).
- **Email-template configuration for invites.** v1 sends a fixed template with workspace name, inviter, and role.
- **Bulk member operations.** Multi-select / bulk-delete is the design's "later" pill.
- **Login-discovery integration** with the invitation accept flow (the post-accept landing already lives in the existing login screen — PR 5.1 owns the login refresh).
- **Owning sender adapters** — the invite email send path uses the existing `services/backend` notification dispatcher (or no-ops in dev with a `[link copied]` toast).

### 1.4 Success criteria

- ✅ `PATCH /v1/workspace` writes `display_name`, `slug` (with uniqueness check), `metadata.logo_url`. Audit row `workspace.update`.
- ✅ `GET /v1/workspace/members` returns paginated list `{user_id, email, display_name, title, role, joined_at, last_seen_at, removed_at}`.
- ✅ `POST /v1/workspace/invitations` mints a one-time token (sha256 hashed at rest, prefix kept for UI), returns `{invite_id, expires_at, token, accept_url}`. Token is shown **once**; subsequent reads return only `prefix + expires_at`.
- ✅ `GET /v1/workspace/invitations` returns pending invites (not accepted, not revoked, within TTL).
- ✅ `DELETE /v1/workspace/invitations/{id}` revokes (sets `revoked_at`).
- ✅ `POST /v1/auth/invitations/{token}/accept` (no-auth) creates `users` row + `organization_members` row + initial `role_assignments` row inside one TX, marks invitation accepted, audits.
- ✅ `PATCH /v1/workspace/members/{user_id}` changes role (revokes prior `role_assignments`, inserts new). Audit `member.role.update`.
- ✅ `DELETE /v1/workspace/members/{user_id}` sets `removed_at` (does **not** destroy rows). Audit `member.remove`. Existing conversations and runs continue to be visible to admins; the user can no longer authenticate (existing `users.deleted_at`/`status` semantics).
- ✅ `GET /v1/workspace/billing` returns `{plan, seats: {used, limit}, current_period: {start, end}, usage: UsageOrgResponse-shaped, invoices: []}`.
- ✅ Streaming handshake byte-identical pre/post merge. `make test` green; backend pytest green; frontend typecheck + build green.

### 1.5 User stories

| #    | Persona           | Story                                                                                                                                                                                       |
| ---- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Marcus (admin)    | I rename the workspace from "Acme Inc." to "Acme — GTM"; the topbar crumb updates after a refetch; the audit log records who/when.                                                          |
| US-2 | Marcus            | I invite Priya (priya@acme.com) as a Member. I copy the link; I paste it into our Slack DM. She clicks; the accept page shows the org name + role; she signs in; she's a member.            |
| US-3 | Marcus            | I changed my mind — Priya should be Admin. I open the Members table, click her row, change role; the audit log shows the role change.                                                       |
| US-4 | Marcus            | A teammate left the company. I click Remove on their row; the row goes grey with "Removed 2026-05-05"; their old chats stay visible to me as admin (audit-friendly), but they can't log in. |
| US-5 | Marcus (billing)  | The billing card shows Plan: Pro, Seats: 12 of 25, Usage this month: 4.2M tokens / $86. I click a 30-day chart and see usage trending. Invoices area says "managed externally."             |
| US-6 | Marcus (defaults) | I go to Workspace → Defaults. The form is the PR 1.6 form, embedded. I change the default model. The audit log shows it. New chats inherit it.                                              |
| US-7 | Sarah (member)    | I see Settings → Workspace in read-only mode. I see the workspace name and the default model my admin set, but the form fields are disabled.                                                |
| US-8 | Pending user      | I click an invite link from Slack. I see "Sarah invited you to **Acme — GTM** as **Member**." I click "Continue with Okta" (existing login flow); after MFA I'm in.                         |

---

## 2 · Spec

### 2.1 Wire — workspace branding

`GET /v1/workspace`

```jsonc
{
  "org_id": "org_…",
  "display_name": "Acme — GTM",
  "slug": "acme-gtm",
  "deployment_kind": "shared",
  "status": "active",
  "metadata": { "logo_url": "https://cdn.acme.com/logo.png" },
  "created_at": "2025-11-12T09:00:00Z",
}
```

`PATCH /v1/workspace` (admin) — RFC 7396 merge-patch — fields supplied are written, fields omitted are untouched.

```jsonc
{
  "display_name": "Acme — GTM",
  "slug": "acme-gtm",
  "metadata": { "logo_url": "https://cdn.acme.com/logo.png" },
}
```

Slug uniqueness uses the existing UNIQUE index on `organizations.slug` (migration 0004 line 21); 422 `slug_taken` on conflict.

### 2.2 Wire — members

`GET /v1/workspace/members?cursor=&limit=50&role=&include_removed=` (admin)

Each row joins `users` + `organization_members` + active `role_assignments` + (optional) `user_profiles` (PR 4.1):

```jsonc
{
  "members": [
    {
      "user_id": "usr_…",
      "email": "sarah.chen@acme.com",
      "email_verified_at": "…",
      "display_name": "Sarah Chen",
      "title": "Marketing Ops",
      "role": { "id": "role_…", "name": "member", "display_name": "Member" },
      "joined_at": "…",
      "last_seen_at": "…",
      "removed_at": null,
      "source": "scim", // or 'invite' | 'oidc' | 'bootstrap'
    },
  ],
  "next_cursor": null,
}
```

`PATCH /v1/workspace/members/{user_id}` (admin)

```jsonc
{ "role": "admin" } // 'admin' | 'member' | 'viewer'
```

Implementation: `revoke_role_assignments(user_id, where revoked_at is null)` then `insert role_assignments(role_id=ROLES[role])`. One TX. Audit row `member.role.update` with `before/after`.

`DELETE /v1/workspace/members/{user_id}` — sets `organization_members.removed_at = NOW()`; cascades nothing (the user's other memberships in other orgs remain). Audit `member.remove`.

Cannot remove the last admin: 409 `cannot_remove_last_admin`.

### 2.3 Wire — invitations

```sql
-- 0019_invitations.sql

CREATE TABLE IF NOT EXISTS invitations (
    invite_id            TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL REFERENCES organizations(org_id),
    email                CITEXT NOT NULL,
    role_id              TEXT NOT NULL REFERENCES roles(role_id),
    -- token mint/verify follows the SCIM-token pattern (0015_scim_provisioning.sql:34-49)
    token_hash           TEXT NOT NULL UNIQUE,
    token_prefix         TEXT NOT NULL,                    -- visible-in-UI 8-char prefix
    created_by_user_id   TEXT NOT NULL REFERENCES users(user_id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at           TIMESTAMPTZ NOT NULL,
    accepted_at          TIMESTAMPTZ,
    accepted_user_id     TEXT REFERENCES users(user_id),
    revoked_at           TIMESTAMPTZ,
    revoked_by_user_id   TEXT REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_invitations_org_pending
    ON invitations (org_id, expires_at DESC)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON invitations
    USING (org_id = current_setting('app.current_org', true));

-- The accept route runs unauthenticated; it bypasses RLS via `SET LOCAL row_security = off`
-- inside the accept transaction, mirroring how the magic-link login path works (existing).
```

`POST /v1/workspace/invitations` (admin)

```jsonc
{ "email": "priya@acme.com", "role": "member", "ttl_seconds": 604800 }
```

Returns:

```jsonc
{
  "invite_id": "inv_…",
  "email": "priya@acme.com",
  "role": "member",
  "expires_at": "2026-05-12T16:01:14Z",
  "token": "inv_live_8e1f…ZxQ", // 32-byte URL-safe; SHOWN ONCE
  "token_prefix": "inv_live_8e1f",
  "accept_url": "https://app.example.com/invite/inv_live_8e1f…ZxQ",
}
```

The token is generated server-side with `secrets.token_urlsafe(32)`, hashed with SHA-256 at rest. Subsequent reads (`GET /v1/workspace/invitations`) return only `token_prefix` and `expires_at`. This is the **same pattern** SCIM tokens use (commit `bcf5b45`); we deliberately copy it byte-for-byte.

`GET /v1/workspace/invitations` (admin) — returns pending invites only (active `expires_at > now`, `revoked_at IS NULL`, `accepted_at IS NULL`):

```jsonc
{
  "invitations": [
    {
      "invite_id": "inv_…",
      "email": "priya@acme.com",
      "role": "member",
      "token_prefix": "inv_live_8e1f",
      "created_by": { "user_id": "usr_…", "display_name": "Marcus T." },
      "created_at": "…",
      "expires_at": "…",
    },
  ],
}
```

`DELETE /v1/workspace/invitations/{invite_id}` (admin) — sets `revoked_at`. 404 if already accepted; 204 on success. Audit `invitation.revoke`.

`POST /v1/auth/invitations/{token}/accept` (no auth header required)

Flow:

1. Hash the path token with SHA-256; lookup row by `token_hash`.
2. Reject if `expires_at < now` (410 `invitation_expired`), `revoked_at IS NOT NULL` (410 `invitation_revoked`), `accepted_at IS NOT NULL` (409 `invitation_already_accepted`).
3. Begin TX with `SET LOCAL row_security = off`.
4. UPSERT `users` by `email` (creates if missing, with `status='pending_login'` — first login completes the row).
5. INSERT `organization_members(org_id, user_id, source='invite', invited_by_user_id=invitation.created_by_user_id)` — idempotent on conflict (already a member → 200 OK with the role they already have).
6. INSERT `role_assignments(role_id=invitation.role_id, granted_by_user_id=invitation.created_by_user_id)`.
7. UPDATE `invitations SET accepted_at = NOW(), accepted_user_id = users.user_id`.
8. INSERT `identity_audit_events(action='invitation.accept', metadata={...})`.
9. COMMIT.
10. Response includes `{org_id, org_display_name, role, accept_redirect: '/login?accepted_invite=…'}` so the FE shows a confirmation page and chains into the existing login flow.

Rate limit: existing `login_attempts` row per accept attempt (success or failure), keyed by `(token_prefix, ip)`. The endpoint is unauthenticated; aggressive rate limiting applies (60 attempts / IP / hour, hard cut at 10 / token-prefix / hour).

### 2.4 Wire — billing (read-only)

`GET /v1/workspace/billing` (admin)

```jsonc
{
  "plan": {
    "tier": "pro", // from deployment_profile.plan_tier
    "display_name": "Atlas Pro",
    "managed_externally": true, // v1 stays true
    "billing_contact": "billing@acme.com",
  },
  "seats": { "used": 12, "limit": 25, "removed_in_period": 1 },
  "current_period": {
    "start": "2026-05-01T00:00:00Z",
    "end": "2026-05-31T23:59:59Z",
  },
  "budgets": [
    // optional — usage_budgets rows
    {
      "scope": "org",
      "period": "month",
      "limit_micro_usd": 100000000,
      "current_spend_micro_usd": 8600000,
    },
  ],
  "invoices": [], // v1 placeholder; provider integration follows
}
```

The seats block is a SQL `COUNT(*)` on `organization_members WHERE removed_at IS NULL`; the limit comes from `deployment_profile.seat_limit` (a static deploy-time value). The budgets block proxies the existing `GET /v1/budgets` call from ai-backend. The invoices block is `[]` until the payment-provider PR ships.

### 2.5 Audit

Five new actions on `identity_audit_events`:

| Action               | Metadata                                                                              |
| -------------------- | ------------------------------------------------------------------------------------- |
| `workspace.update`   | `{ before: { display_name, slug, metadata.logo_url }, after: { … }, diff_keys: […] }` |
| `member.role.update` | `{ user_id, before_role, after_role }`                                                |
| `member.remove`      | `{ user_id, source }`                                                                 |
| `invitation.create`  | `{ invite_id, email, role, expires_at }` (no token!)                                  |
| `invitation.accept`  | `{ invite_id, accepted_user_id }`                                                     |
| `invitation.revoke`  | `{ invite_id, revoked_by_user_id }`                                                   |

(Six rows; we listed five categories above and `invitation.accept` separately.)

### 2.6 Permissions

| Caller                | Workspace branding | Members directory | Members write | Invitations    | Billing read |
| --------------------- | ------------------ | ----------------- | ------------- | -------------- | ------------ |
| Workspace admin       | ✅ R/W             | ✅ R/W            | ✅            | ✅             | ✅           |
| Member                | ✅ R               | ✅ R (basic)      | ❌            | ❌             | ❌           |
| Viewer                | ✅ R               | ❌                | ❌            | ❌             | ❌           |
| No-auth (accept link) | ❌                 | ❌                | ❌            | ✅ accept-only | ❌           |

Admin check reuses the existing `ADMIN_USERS` permission scope from `auth.py` (PR 1.2.1 introduced for connector admin override; PR 1.6 already extended for workspace defaults). No new RBAC primitive.

### 2.7 Error semantics

| Condition                                                           | Status | Code                          |
| ------------------------------------------------------------------- | ------ | ----------------------------- |
| `PATCH /v1/workspace` non-admin                                     | 403    | `forbidden`                   |
| `PATCH /v1/workspace` slug taken                                    | 422    | `slug_taken`                  |
| `PATCH /v1/workspace` slug bad shape (`!regex /^[a-z0-9-]{3,40}$/`) | 422    | `invalid_slug`                |
| `POST /v1/workspace/invitations` for existing active member         | 409    | `already_a_member`            |
| `POST /v1/workspace/invitations` rate limit                         | 429    | `rate_limited`                |
| `POST /v1/auth/invitations/{tok}/accept` expired                    | 410    | `invitation_expired`          |
| same — revoked                                                      | 410    | `invitation_revoked`          |
| same — already accepted                                             | 409    | `invitation_already_accepted` |
| `PATCH /v1/workspace/members/{id}` last-admin downgrade             | 409    | `cannot_remove_last_admin`    |
| `DELETE /v1/workspace/members/{id}` last admin                      | 409    | `cannot_remove_last_admin`    |
| `DELETE /v1/workspace/members/{id}` self                            | 409    | `cannot_remove_self`          |

### 2.8 Frontend contract (`@enterprise-search/api-types`)

```ts
// packages/api-types/src/index.ts

export interface Workspace {
  org_id: string;
  display_name: string;
  slug: string;
  deployment_kind: string;
  status: string;
  metadata: { logo_url?: string };
  created_at: string;
}

export type WorkspaceRoleName = "admin" | "member" | "viewer";

export interface Member {
  user_id: string;
  email: string;
  email_verified_at: string | null;
  display_name: string | null;
  title: string | null;
  role: { id: string; name: WorkspaceRoleName; display_name: string };
  joined_at: string;
  last_seen_at: string | null;
  removed_at: string | null;
  source: "invite" | "scim" | "oidc" | "bootstrap";
}

export interface Invitation {
  invite_id: string;
  email: string;
  role: WorkspaceRoleName;
  token_prefix: string;
  created_by: { user_id: string; display_name: string | null };
  created_at: string;
  expires_at: string;
}

export interface CreateInvitationResponse extends Invitation {
  token: string;
  accept_url: string;
}

export interface BillingDigest {
  plan: {
    tier: string;
    display_name: string;
    managed_externally: boolean;
    billing_contact: string | null;
  };
  seats: { used: number; limit: number; removed_in_period: number };
  current_period: { start: string; end: string };
  budgets: BudgetSummary[];
  invoices: InvoiceStub[];
}
```

`BudgetSummary` is the shape that already comes back from `GET /v1/budgets/me` in ai-backend; we re-export. `InvoiceStub` is `{}` for v1.

### 2.9 Frontend wiring

| Concern                 | Reuse                                                                  | Add                                                                                                                 |
| ----------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Section host            | `SettingsScreen` left rail + section switch                            | Three section components + three rail entries grouped under the "Workspace" header                                  |
| Workspace defaults form | PR 1.6 `useWorkspaceDefaults()` + existing `WorkspaceDefaults` shape   | Embed it as a sub-form inside `<WorkspaceSettings />`                                                               |
| Slug uniqueness preview | n/a                                                                    | Debounced `HEAD /v1/workspace?slug=…` ping (or accept the 422 round-trip)                                           |
| Members table           | Existing tabular layout primitives in `<Card>` + `<Field>`             | `<MembersTable>` with row actions menu (Change role / Remove). Use `@radix-ui/react-dropdown-menu` for the row menu |
| Invite modal            | `<Dialog>` from PR 4.4 (lifts Radix Dialog into the design-system)     | `<InviteModal>` form + token "copy once" UI                                                                         |
| Pending invites list    | Same `<Card>` + `<Badge>`                                              | `<InvitationsList>` with revoke action                                                                              |
| Billing card            | Existing `<Card>` + `<Badge>`                                          | `<BillingCard>` rendering plan + seats + budget; usage chart imported from PR 4.5 (`<UsageWorkspaceChart />`)       |
| Last-active             | n/a (best-effort from `sessions.last_used_at` if available; else null) | One column                                                                                                          |
| State                   | Two thin hooks `useWorkspaceMembers()` + `useInvitations()`            | ~80 LOC each                                                                                                        |

### 2.10 Service path

```
backend-facade  /v1/workspace                         →  backend  /internal/v1/workspace
backend-facade  /v1/workspace/members[/{user_id}]     →  backend  /internal/v1/workspace/members[/{user_id}]
backend-facade  /v1/workspace/invitations[/{id}]      →  backend  /internal/v1/workspace/invitations[/{id}]
backend-facade  /v1/auth/invitations/{token}/accept   →  backend  /internal/v1/auth/invitations/{token}/accept
backend-facade  /v1/workspace/billing                 →  backend  /internal/v1/workspace/billing
                                                          backend internally calls ai-backend for the budget block
```

Backend handlers under `services/backend/src/backend_app/routes/workspace.py` (new), `members.py` (new), `invitations.py` (new), `billing.py` (new). Backend's billing handler proxies `GET /v1/budgets` to ai-backend via the existing service-to-service `RuntimeServiceAuthenticator` channel — no new tunnel.

### 2.11 Workspace deletion stub

The danger-zone delete button posts to `DELETE /v1/workspace`. v1 implementation: 501 `not_implemented` with copy "Workspace deletion is gated. Contact support." UI shows the confirmation dialog, captures a typed-confirmation slug, and posts. The audit row `workspace.delete_attempt` is written even on the 501 so we can see who's asking and whether to prioritise the cascade-delete job. Real implementation lands in a dedicated PR (high blast radius).

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌──────────────────────┐                                   ┌────────────────────────┐
   │ apps/frontend        │ /v1/workspace*                    │ backend-facade         │
   │ Settings → Workspace │ /v1/workspace/members*            │ admin guard +          │
   │ Settings → Members   │ /v1/workspace/invitations*        │ proxy to backend       │
   │ Settings → Billing   │ /v1/workspace/billing             │                        │
   └──────────┬───────────┘ /v1/auth/invitations/{tok}/accept └────────────┬───────────┘
              │                                                            │ /internal/v1/workspace*
              │                                                            ▼
              │                                                   ┌─────────────────────┐
              │                                                   │ services/backend    │
              │                                                   │ routes/workspace.py │
              │                                                   │ routes/members.py   │
              │                                                   │ routes/invitations  │
              │                                                   │ routes/billing.py   │
              │                                                   │                     │
              │                                                   │ identity_audit_events│
              │                                                   └──────────┬──────────┘
              │                                                              │ for the budget block only
              │                                                              ▼
              │                                                   ┌─────────────────────┐
              │ /v1/agent/workspace/defaults (PR 1.6)             │ services/ai-backend │
              └──────────────────────────────────────────────────►│ /v1/budgets/me      │
                                                                  └─────────────────────┘
```

The Workspace settings form for **default model / default connectors / retention** continues to call PR 1.6's `/v1/agent/workspace/defaults` directly (via facade). This PR does **not** wrap PR 1.6 in a backend-side compose — the FE simply invokes both endpoints in parallel on page load, and the user's edits route to whichever one is the source of truth.

### 3.2 Streaming impact — explicitly **none**

| Subsystem                                | Touched?                                                         |
| ---------------------------------------- | ---------------------------------------------------------------- |
| `runtime_events`, `RuntimeEventEnvelope` | No                                                               |
| SSE handshake                            | No                                                               |
| Worker job loop                          | No                                                               |
| Capabilities / tools / MCP loaders       | No                                                               |
| Citations, drafts, approvals, subagents  | No                                                               |
| Audit chain                              | Additive — six new `action` constants on `identity_audit_events` |
| Retention sweeper                        | No                                                               |
| Run resolution                           | No                                                               |

Removing a member does **not** retroactively cancel runs they own. Their runs continue to completion; their conversations are still readable by admins (audit-friendly); they just can't authenticate. This matches the SCIM deprovisioning behaviour shipped in `bcf5b45`.

### 3.3 Why invitations live in **backend**, not ai-backend

Identity is backend-owned per `services/backend/CLAUDE.md`. Invitations create users and members; they belong with the rest of identity. ai-backend has no business minting them.

### 3.4 DRY — what we reuse vs. what we add

| Concern                                | Reuse                                                                                       | Add                                                         |
| -------------------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Token mint pattern                     | `scim_tokens` (token_hash UNIQUE + token_prefix UI) from `0015_scim_provisioning.sql:34-49` | `invitations` table, identical token-mint semantics         |
| Audit chain                            | `identity_audit_events` + chain-signature trigger (`0002_audit_hardening.sql`)              | Six new `action` constants                                  |
| Append-only enforcement                | Existing trigger                                                                            | —                                                           |
| RFC 7396 merge-patch                   | Pydantic v2 `model_dump(exclude_unset=True)`                                                | —                                                           |
| RLS policy template                    | `tenant_isolation` from `0008_rls`                                                          | One CREATE POLICY                                           |
| Slug uniqueness                        | UNIQUE index on `organizations.slug`                                                        | —                                                           |
| Last-active                            | `sessions.last_used_at` (already populated by every authenticated request)                  | One JOIN in members read                                    |
| Email validation                       | `email-validator` (transitive on `pydantic[email]`)                                         | —                                                           |
| Budget block in Billing                | `GET /v1/budgets/me` from ai-backend                                                        | One service-to-service GET inside backend's billing handler |
| Usage trend in Billing                 | PR 4.5's `<UsageWorkspaceChart />` component                                                | Embed it                                                    |
| Default model / connectors / retention | PR 1.6's endpoints + hook                                                                   | Embed the form                                              |
| FE state                               | Existing fetch+state pattern                                                                | Two thin hooks                                              |
| Modal primitive                        | `@radix-ui/react-dialog` (added by PR 4.4)                                                  | —                                                           |
| Dropdown row menu                      | `@radix-ui/react-dropdown-menu` (mature, accessible, headless; ~5 KB gzipped)               | One install + one wrapper component                         |
| Identity                               | Existing `RuntimeServiceAuthenticator` headers; admin scope check                           | —                                                           |
| Service-to-service for budgets         | Existing internal channel                                                                   | —                                                           |

### 3.5 Pre-built libraries — what we considered, what we use

| Need                                         | Considered                                                       | Decision                                                                                                                                       |
| -------------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Token generation                             | `secrets` (stdlib), `python-secrets`                             | **`secrets.token_urlsafe(32)`** — stdlib. SCIM uses it; we use it.                                                                             |
| Email validation                             | `email-validator`                                                | **Already transitive** via `pydantic[email]` — no new dep.                                                                                     |
| Email sender                                 | `aiosmtplib`, SES SDK                                            | **Out of scope** — v1 emits to existing notification dispatcher (no-op in dev).                                                                |
| Stripe integration                           | `stripe` SDK                                                     | **Out of scope** — billing is read-only digest in v1.                                                                                          |
| Drop-down menu                               | `@radix-ui/react-dropdown-menu`, `react-aria-components`, custom | **Radix Dropdown Menu** — accessible (proper ARIA), composable, ~5 KB gzipped, MIT, weekly downloads >2M. Industry standard.                   |
| Modal / Dialog                               | `@radix-ui/react-dialog`, `react-modal`, `headlessui`            | **Radix Dialog** — same family as the Dropdown; PR 4.4 ships the same primitive for the MCP wizard. We share one install across PRs 4.2 / 4.4. |
| Form state                                   | `react-hook-form`, `formik`                                      | **Skip** — invite form is one email field + one role select; native state is fine.                                                             |
| Pagination                                   | `useInfiniteQuery` (`react-query`)                               | **Skip** — keyset cursor is two states (cursor, hasMore) and a button. PR 4.x doesn't pull a server-state lib.                                 |
| Slug field UX                                | `slugify`                                                        | **Skip** — slug is admin-typed, validated server-side. No client transform.                                                                    |
| Confirm-typed-text dialog (delete workspace) | `react-confirm`                                                  | **Skip** — 30 LOC native.                                                                                                                      |

### 3.6 Sequence — Marcus invites Priya

```
Marcus              FE (Members)              backend-facade            backend                          DB
 │                   │                          │                         │                                 │
 │  click Invite     │                          │                         │                                 │
 │ ────────────────► │ open <InviteModal>       │                         │                                 │
 │  type email,      │                          │                         │                                 │
 │  pick role        │                          │                         │                                 │
 │  click Send       │                          │                         │                                 │
 │                   │ POST /v1/workspace/invitations                     │                                 │
 │                   │ ────────────────────────►│ admin guard            │                                 │
 │                   │                          │ proxy /internal/v1/...  │                                 │
 │                   │                          │ ───────────────────────►│ secrets.token_urlsafe(32)      │
 │                   │                          │                         │ sha256 → hash                  │
 │                   │                          │                         │ INSERT invitations             │
 │                   │                          │                         │ INSERT identity_audit_events   │
 │                   │                          │                         │ enqueue invite-email send      │
 │                   │                          │                         │ ────────────────────────────►  │ row
 │                   │                          │ ◄──────────────────────│ 200 { invite, token (once) }   │
 │                   │ ◄────────────────────────│                         │                                 │
 │                   │ token displayed once      │                         │                                 │
 │  copy + paste     │  with "Copy" button       │                         │                                 │
 │  to Slack         │                          │                         │                                 │
 │                   │                          │                         │                                 │
 │  …Priya clicks    │                          │                         │                                 │
 │                   │ /invite/inv_live_8e1f… (FE accept page renders org info)
 │                   │                          │                         │                                 │
 │                   │ POST /v1/auth/invitations/{tok}/accept             │                                 │
 │                   │ ────────────────────────►│ no-auth route          │                                 │
 │                   │                          │ rate-limit by ip+prefix │                                 │
 │                   │                          │ proxy → backend        │                                 │
 │                   │                          │ ───────────────────────►│ BEGIN; row_security off        │
 │                   │                          │                         │ verify token hash               │
 │                   │                          │                         │ check expiry / revoked / accepted│
 │                   │                          │                         │ UPSERT users (status=pending_login)│
 │                   │                          │                         │ INSERT organization_members    │
 │                   │                          │                         │ INSERT role_assignments        │
 │                   │                          │                         │ UPDATE invitations.accepted_at │
 │                   │                          │                         │ INSERT identity_audit_events   │
 │                   │                          │                         │ COMMIT                         │
 │                   │                          │ ◄──────────────────────│ 200 { redirect: /login?...}    │
 │                   │ ◄────────────────────────│                         │                                 │
 │                   │ chains into existing login flow (PR 5.1 owns it)                                     │
```

### 3.7 Edge cases

| Case                                                                                | Behaviour                                                                                                                                               |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Two admins remove each other simultaneously                                         | Last-admin guard — at least one admin must remain. If both attempts target the only-other admin, the second one returns 409 `cannot_remove_last_admin`. |
| Member uses an invitation link in another workspace's domain                        | Token is validated against `invitations.org_id`; the FE shows the right org name; cross-org confusion is impossible.                                    |
| Invitation accepted by a user who already exists in another org with the same email | We UPSERT `users` by email (existing row found); INSERT `organization_members` succeeds; the user gains a second org membership.                        |
| Slug change while clients are mid-request                                           | `organizations.slug` is denormalised in the URL only; we do not store slug in agent_conversations etc. The next page load re-derives the URL.           |
| Workspace rename mid-stream of a chat                                               | Topbar crumb refreshes on next visibility change. Stream is unaffected.                                                                                 |
| Token-prefix collision                                                              | Prefix is the first 12 chars of `token_urlsafe(32)`; collision probability is 2^-72. Not a real concern.                                                |
| Member removed mid-run                                                              | The run completes; the user can no longer authenticate; existing sessions terminate at next refresh per `sessions.token_hash` lookup pattern.           |
| Last-admin guard contests with concurrent role demote                               | Both writes serialize on `(org_id, role_id) = admin` count check (advisory lock); only one wins.                                                        |
| Plan tier downgrade pushes seats over limit                                         | UI surfaces the over-limit; v1 is read-only display. Real over-limit handling ships with billing integration.                                           |
| Invitation TTL = 0 / negative                                                       | 422 `invalid_ttl_seconds`.                                                                                                                              |
| Email value not RFC 5321 valid                                                      | 422 `invalid_email`.                                                                                                                                    |

### 3.8 Test plan

**Backend (`services/backend/tests/`)**

- `unit/workspace/test_patch_workspace.py` — happy path, slug-taken, last-admin restriction not triggered (workspace branding doesn't touch members).
- `unit/workspace/test_members_directory.py` — pagination, role filter, removed filter, RLS leak test.
- `unit/workspace/test_member_role_change.py` — TX rollback when role lookup fails; last-admin guard.
- `unit/workspace/test_member_remove.py` — soft-remove; runs not cancelled; sessions invalidated.
- `unit/invitations/test_invitation_create_revoke.py` — token-once flow; revoked_at; rate limit.
- `unit/invitations/test_invitation_accept.py` — happy path; expired; revoked; already accepted; concurrent-accept (advisory lock).
- `unit/billing/test_billing_digest.py` — seats from members; budgets from ai-backend stub; invoices empty.
- `integration/test_audit_chain_for_workspace_writes.py` — six new action types; chain verifier passes.

**Frontend (`apps/frontend/src/features/settings/sections/`)**

- `WorkspaceSettings.test.tsx` — admin can save name+slug+logo; slug-taken renders inline error; defaults form delegates to PR 1.6 hook.
- `Members.test.tsx` — table renders; role change calls API + optimistic update; remove confirmation modal; last-admin guard surfaces 409.
- `InviteModal.test.tsx` — token "copy once" UI; revoke removes from list.
- `BillingCard.test.tsx` — read-only display; chart embedded.

**Cross-service smoke (`make test`)** — invite + accept end-to-end through facade.

### 3.9 Rollout

- **Flag-free for everything except workspace-delete** (which is 501 stub).
- **Zero-downtime migration.** `CREATE TABLE invitations IF NOT EXISTS`; one partial index.
- **Backout.** Drop the `invitations` table; the four invitation routes return 501; existing SCIM-provisioned orgs are unaffected.
- **Forward compatibility.** Adding more roles in v2 (e.g. `billing_admin`) is one row in `roles` and a switch update.
- **Audit chain forwards.** New action constants are append-only; chain verifier sees them as additive.

### 3.10 Open questions

1. **Granular roles / permission_scopes editor.** The schema (`roles.permission_scopes`) supports it. v1 ships only the three system roles. Custom-role editor when there's a user asking for it.
2. **Invitation email template customisation.** Out of scope; one fixed template.
3. **SAML / OIDC just-in-time member creation vs. invitation.** Two paths coexist (SCIM, OIDC, invitation). The accept flow is idempotent on email — JIT auto-creation lands when SSO sends a never-seen-before email.
4. **Stripe integration timing.** Tracked separately. v1 keeps `managed_externally: true`.
5. **Workspace deletion.** Cascade scope is huge; needs its own design + safeguards.

---

## 4 · Acceptance checklist

- [ ] Migration `0019_invitations.sql` applies cleanly forward and rolls back.
- [ ] `WorkspaceService.patch()` writes name / slug / metadata; slug uniqueness enforced; one audit row.
- [ ] `MembersService.list/patch/remove()` handle pagination, role change with last-admin guard, soft-remove with audit.
- [ ] `InvitationsService.create/list/revoke/accept()` mint with `token_urlsafe(32)`, hash-only persistence, rate-limited accept, idempotent on already-member.
- [ ] `BillingService.digest()` joins seats + budgets + invoices stub.
- [ ] `backend-facade` exposes `/v1/workspace`, `/v1/workspace/members`, `/v1/workspace/invitations`, `/v1/auth/invitations/{tok}/accept`, `/v1/workspace/billing` proxies; admin guard at the facade for all but accept.
- [ ] `@enterprise-search/api-types` exports `Workspace`, `Member`, `Invitation`, `CreateInvitationResponse`, `BillingDigest`, plus role/source enums.
- [ ] `<WorkspaceSettings />`, `<Members />`, `<Billing />` mount under the "Workspace" group in `SettingsScreen`.
- [ ] `<InviteModal />` shows the token once with a Copy button; `<MembersTable />` row menu uses Radix Dropdown.
- [ ] PR 1.6 form embedded inside `<WorkspaceSettings />` (does not duplicate logic).
- [ ] Six new audit `action` constants registered; chain verifier passes.
- [ ] No new event types, no new wire variants, no LangGraph harness changes, no SSE schema change.
- [ ] `make test` green; backend pytest green; frontend typecheck + build green.

---

## 5 · References

- Design Doc · Settings → "Workspace" group — bundle at `/tmp/design-doc/enterprise-search/project/Design Doc.html` lines 546-548.
- [`services/backend/migrations/0004_identity_foundation.sql`](../../services/backend/migrations/0004_identity_foundation.sql) — `organizations`, `organization_members`, `roles`, `role_assignments`.
- [`services/backend/migrations/0015_scim_provisioning.sql`](../../services/backend/migrations/0015_scim_provisioning.sql) — token-mint pattern we mirror for invitations.
- [`services/backend/migrations/0002_audit_hardening.sql`](../../services/backend/migrations/0002_audit_hardening.sql) — append-only audit chain.
- [`services/backend/src/backend_app/routes/me.py`](../../services/backend/src/backend_app/routes/me.py) — pattern for the new routes.
- [`services/backend-facade/src/backend_facade/me_routes.py`](../../services/backend-facade/src/backend_facade/me_routes.py) — proxy template.
- [Radix UI · Dialog](https://www.radix-ui.com/primitives/docs/components/dialog) — modal primitive (shared with PR 4.4).
- [Radix UI · Dropdown Menu](https://www.radix-ui.com/primitives/docs/components/dropdown-menu) — row action menu.
- RFC 7396 — JSON Merge Patch.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — defaults form embedded inside `<WorkspaceSettings />`.
- [`docs/new-design/pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md) — `user_profiles` sidecar joined into the members directory.
- [`docs/new-design/pr-4.5-usage-overlay-share-popover.md`](pr-4.5-usage-overlay-share-popover.md) — `<UsageWorkspaceChart />` embedded inside the Billing card.
