# PR 5.1 — Login: email‑first IdP discovery, magic‑link, workspace picker, brand pane

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 5, PR 5.1 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** backend (1 service + 1 dispatcher port + 4 routes + 1 migration) · backend‑facade (4 proxy routes) · api‑types (4 types) · frontend (rebuild `LoginScreen`, add `<Brand>`, `<EmailStep>`, `<MagicLinkSent>`, `<WorkspacePicker>`, ≈ 7 LOC of CSS scroll‑lock opt‑out) · design‑system (no new primitive)
> **Size:** **L.** Adds the only auth surface that doesn't exist yet (domain → IdP discovery + magic‑link), but **reuses every privileged code path** already in the tree — `OidcService`, `SamlService`, `SessionService`, `MfaPrompt`, `AuthContext`, `lockout`, `login_attempts`, `identity_audit_events`. Net new code: 1 migration, 1 `MagicLinkService`, 1 `EmailDispatcherPort`, 4 routes, 1 anti‑enumeration rule, 1 brand pane, 1 4‑step state machine on the frontend.
> **Depends on:**
>
> - ✅ A3 OIDC routes (shipped)
> - ✅ A4 local password (shipped — kept behind a fallback toggle)
> - ✅ A5 SAML (shipped)
> - ✅ A6 MFA (TOTP + WebAuthn + recovery — shipped)
> - ✅ A7 SCIM provisioning (shipped — supplies `organization_members` for the workspace picker)
> - ✅ A8 lockouts + `login_attempts` (shipped — extended with two new outcomes)
> - PR 0.1 design tokens (✅ accent, status colours)
> - PR 4.2 Members invitations migration (`0019_invitations.sql`) — independent, no merge order
>   **Reads alongside:**
> - [`pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — audit‑on‑write pattern
> - [`pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — append‑only chain pattern for sensitive writes
> - [`pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md) — same RFC 7396 / same audit / same `me` route shape
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — facade‑only network rule
> - [`services/backend/CLAUDE.md`](../../services/backend/CLAUDE.md) — internal vs. public split
>   **Sibling docs (Wave 5):** none. Wave 5 is one PR.

---

## 0 · TL;DR

We already ship every protocol the Atlas spec calls for (OIDC, SAML, local, MFA, sessions, lockouts, audit). What we don't ship is the **glue** that lets a user type _one_ email and land on the right one. This PR adds the glue — `auth.discover` — and the two surfaces that hang off it: a magic‑link path for unknown / personal domains and a workspace picker for users in multiple orgs.

| Surface        | Today                                                                         | After this PR                                                                                                                                             |
| -------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Discovery      | None — user types `org_id` and we list providers                              | `POST /v1/auth/discover` with `{email}` returns `{kind: 'sso'\|'personal'\|'magic_link'\|'unknown', org_id?, provider_id?, member_count?, sso_enforced?}` |
| Magic link     | None                                                                          | `POST /v1/auth/magic-link/start` (always 202, anti‑enumeration); `GET /v1/auth/magic-link/callback?token=…` mints a session                               |
| Workspace pick | Implicit — `org_id` typed by user picks one                                   | `POST /v1/auth/sessions/select` after a multi‑org login                                                                                                   |
| Login UI       | Org‑id box + IdP buttons + email/password form (`LoginScreen.tsx`, 248 lines) | Email‑first flow with debounced discovery, adaptive primary button, magic‑link fallback, workspace picker, brand right pane, compliance row               |
| MFA            | `MfaPrompt.tsx` (350 lines, TOTP + WebAuthn + recovery)                       | **Reused unchanged** — mounted as a step in the new flow                                                                                                  |

**The three principles**

1. **One typed input.** Email decides everything. Provider is discovered, org is discovered, MFA is policy‑driven, workspace pick is conditional. No `org_id` field on the page.
2. **Existing privileged code paths are not rewritten.** `OidcService.authorize`, `SamlService.start`, `SessionService.create`, `MfaPrompt`, `lockout` policies — all reused. New code is the discovery + magic‑link + picker.
3. **Anti‑enumeration is the default.** Discovery and magic‑link‑start return identical shapes regardless of whether the email exists. Login attempts always write a `login_attempts` row. Rate‑limited per IP and per email.

LoC estimate: backend ≈ 580 (1 migration + `MagicLinkService` + `EmailDispatcherPort` + `DiscoveryService` + 4 routes + 6 audit actions + tests) · backend‑facade ≈ 90 · api‑types ≈ 70 · frontend ≈ 480 (rebuild of `LoginScreen` as a 4‑step state machine + `<Brand>`, `<DiscoveryCard>`, `<MagicLinkSent>`, `<WorkspacePicker>`) · design‑system ≈ 0 (existing primitives + login‑opt‑out CSS).

---

## 1 · PRD

### 1.1 Problem

Today's `LoginScreen.tsx` (lines 45–248) demands an `org_id` from the user. This is a non‑starter for the design's audience — non‑engineer enterprise users — for three reasons:

1. **They don't know it.** `acme` vs `acme-eu` vs `acme.io` is invisible to a marketing manager.
2. **It's fragile.** Typos lock them out of the very list of providers they need to see.
3. **It's redundant.** The user's email already encodes the org (the domain part) and almost always the IdP (most domains belong to one Okta or one Workspace tenant).

The design doc (Atlas → Login → §Flow — email‑first / progressive) collapses the entire pre‑auth flow to _one_ input — email — and lets the server decide the rest. The design also adds two paths that don't exist today:

| Need                    | Why                                                                                                                                                                                           | What's missing                                                                                                                             |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Magic link              | Personal domains (gmail / icloud), unknown domains, and any deploy where the org‑level local password is disabled need a passwordless email path.                                             | No `magic_link_tokens` table; no `EmailDispatcherPort`; no callback route; no UI.                                                          |
| Workspace picker        | A user can be a member of multiple workspaces (FY26 corporate IT consolidation, contractors with personal + company accounts). After authentication we need to ask which workspace they want. | `organization_members` already supports many‑to‑many, but neither the OIDC callback nor the magic‑link callback handle the multi‑org case. |
| Adaptive primary button | Reading "Continue with Okta" vs "Email me a sign‑in link" lets the user verify the heuristic before clicking.                                                                                 | Today the button label is static ("Sign in").                                                                                              |
| Brand right pane        | Cold‑linked users (recipient view of a shared thread, page B from a search result) need the "is this real?" answer before typing.                                                             | Today the login screen is a centered card, no brand surface.                                                                               |
| Compliance row          | Bank / gov buyers expect SOC 2 / ISO / HIPAA / GDPR badges visible at the auth surface — the buyer's procurement is reading.                                                                  | None today.                                                                                                                                |

### 1.2 Goals

1. **Email decides everything.** A user types `sarah.chen@acme.com`; after 450 ms of debounce the discovery card shows `Acme Inc · Okta · 12,483 members · SSO enforced` and the primary button relabels to "Continue with Okta." Submitting redirects to the existing OIDC `/start` endpoint.
2. **Personal + unknown domains are not dead ends.** Typing `me@gmail.com` or `someone@brand-new-startup.example` produces a "We'll email you a sign‑in link" CTA. The submit is non‑revealing — the server replies 202 whether the user exists or not. The URL the user receives, when consumed, mints a session (or surfaces the workspace picker if the email maps to multiple orgs).
3. **MFA is unchanged.** The existing `MfaPrompt` mounts when the upstream login response sets `requires_mfa=true`. Whether the upstream was OIDC, SAML, local, or magic‑link does not matter — `AuthContext` already routes through `mfa_pending`.
4. **Multi‑workspace users see a clean picker.** After auth, if the email matches `> 1` `organization_members` rows, surface a list with member counts and last‑active. Single‑workspace users skip.
5. **Brand surface is informational, not decorative.** Eyebrow + headline + lede + compliance row — answers "is this real" without being a marketing landing page. Cut from earlier prototypes: a floating SOC2 pill and a customer quote card.
6. **`MfaPrompt` and the OIDC/SAML/local privileged code paths stay byte‑for‑byte.** This PR adds a new entry ramp. Anything past the entry ramp is reuse.
7. **The streaming subsystem and agent harness see zero change.** Login completes before any conversation is created.

### 1.3 Non‑goals

- **Passkey / WebAuthn primary login.** WebAuthn already exists as an MFA factor. A WebAuthn‑first login (no email at all) is a future PR — kept on the design doc's "later" list.
- **Domain‑claim flow for new admins.** "I want to claim this domain for my new workspace" is a Members / Workspace flow, not Login. Out of scope.
- **Custom magic‑link templates.** v1 ships one English template per dispatcher. Localised templates ride PR 4.1's `locale` field once it's wired into the dispatcher.
- **CAPTCHA / bot challenge.** Anti‑abuse is rate‑limit + lockout (existing). If we see a bot wave we add a CAPTCHA in a follow‑up.
- **Self‑service signup.** "I don't have an account" today routes to the same email field; a signup flow is a separate PR.
- **OAuth tile fallback for personal SSO** (Google, Microsoft, Apple, GitHub). The design notes a 3×2 collapsed grid; we render the **buttons** (with the existing OIDC `/start` path for the corresponding `auth_providers` row in a designated `system_providers` org) but the per‑deploy decision to wire each tile is admin work, not login work. Tiles render only when the deploy has them configured.
- **Linking personal account → workspace.** "Sign in with your personal Google, then we attach you to Acme by invitation" is a Members PR. v1 keeps personal and SSO worlds separate — a `gmail.com` email won't authenticate into `acme.com`'s Okta.

### 1.4 Success criteria

- ✅ `POST /v1/auth/discover` with a valid email returns one of `{kind: 'sso' | 'personal' | 'magic_link' | 'unknown'}` in <60 ms p99 against the local stack. Unknown domains and personal domains both return `kind: 'magic_link'` (they take the same UI branch).
- ✅ `POST /v1/auth/discover` rate‑limited to **30 / minute / IP** (lockout middleware extended). 30‑first request always succeeds; 31st returns 429 with `Retry‑After`.
- ✅ `POST /v1/auth/magic-link/start` always returns 202, regardless of whether the email exists. One row in `magic_link_tokens` per request that **resolves to an existing user**; zero rows otherwise. One row in `login_attempts` with outcome `magic_link_requested` either way.
- ✅ Magic‑link tokens are 256‑bit, base64url‑encoded, single‑use (`consumed_at` set on first redemption), 15‑minute TTL.
- ✅ `GET /v1/auth/magic-link/callback?token=…` mints a session via `SessionService.create` if the email maps to **exactly one** workspace, otherwise issues a short‑lived `pick_token` (5 minutes) and surfaces the workspace list.
- ✅ `POST /v1/auth/sessions/select` exchanges the `pick_token` + chosen `org_id` for the final session bearer.
- ✅ `LoginScreen` rebuild matches the prototype (`/tmp/design-doc/enterprise-search/project/login-page.jsx`) on layout: brand pane left (1fr), card right (~440px), compliance row at bottom of brand pane, single email input, debounced 450 ms discovery, adaptive button.
- ✅ `<input.focus({ preventScroll: true })>` keeps the page anchored on first paint (per the design doc's decisions log entry).
- ✅ The login route opts out of the app's body scroll lock via `html.login-html, body.login-body { overflow: auto; height: auto }`.
- ✅ Multi‑workspace users see a list with member counts (from `organization_members` count per org), last‑active (max `sessions.last_seen_at` per org/user), and role.
- ✅ Single‑workspace users skip the picker; the magic‑link callback returns the final bearer directly.
- ✅ MFA prompt, when triggered, behaves byte‑for‑byte as today (TOTP / WebAuthn / recovery).
- ✅ Streaming handshake byte‑identical pre/post merge. Agent harness unchanged. `make test` green; ai‑backend pytest suite green; backend pytest suite green; frontend typecheck + build green.
- ✅ Bank‑deploy profile (`hideSelfService=true`, ENTERPRISE_AUTH_BANK_PROFILE=true): magic‑link disabled at the discovery layer (response `kind: 'sso'` only, never `'magic_link'`), even for personal domains. Personal email submission shows "Your workspace requires single sign‑on" error.

### 1.5 User stories

| #     | Persona                               | Story                                                                                                                                                                                                   |
| ----- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US‑1  | Sarah · Acme · Okta SSO               | Lands on `/login` cold. Types `sarah.chen@acme.com`. After ~450 ms the card shows "Acme Inc · Okta · 12,483 members · SSO enforced." Button now says "Continue with Okta." Click → Okta. Done.          |
| US‑2  | Devi · personal Gmail                 | Types `devi@gmail.com`. Card shows "Personal Google account · We'll email you a sign‑in link." Button says "Email me a sign‑in link." Click → "Check your email" card.                                  |
| US‑3  | Marcus · brand‑new domain             | Types `m@launchco.io`. Discovery returns `unknown`. Card shows "No SSO found · We'll email you a sign‑in link." Same magic‑link path.                                                                   |
| US‑4  | Compliance auditor · cold link        | Lands on `/login`. Reads brand pane: "Atlas reads across Notion, Drive, Slack and Salesforce. With citations, approvals, and a clear paper trail." Sees compliance row. Closes the tab satisfied.       |
| US‑5  | Sarah · second device                 | After magic‑link click on her phone, since she's only in `Acme Inc`, she's authenticated immediately. No picker.                                                                                        |
| US‑6  | Contractor with two workspaces        | Magic‑link click. Email matches `Acme Inc.` (member) and `Acme — EU` (member). Picker shows both with member counts and last active. Picks Acme — EU. Session minted with `org_id=acme-eu`.             |
| US‑7  | User who triggered MFA                | Email‑first → Okta redirect → IdP returns. Backend emits `requires_mfa=true`. Frontend mounts existing `MfaPrompt`. Sarah verifies TOTP. Lands in chat.                                                 |
| US‑8  | Bot                                   | Bot hits `/v1/auth/magic-link/start` 200 times with random emails. Rate limiter caps to 5 / minute / IP. 195 fail with 429. Zero `magic_link_tokens` rows for nonexistent emails (no enumeration leak). |
| US‑9  | Sarah typo                            | Types `sarah.chen@acme..com`. Discovery card stays empty (invalid domain shape). Inline error renders.                                                                                                  |
| US‑10 | Bank‑deploy user with personal domain | Types `sarah@gmail.com` on the bank build. Discovery returns `unknown` (personal not allowed). Card shows "Your workspace requires single sign‑on." No magic‑link button.                               |

---

## 2 · Spec

### 2.1 Wire — `/v1/auth/discover`

```http
POST /v1/auth/discover
Content-Type: application/json
{ "email": "sarah.chen@acme.com" }
```

```jsonc
// 200 — kind=sso (mapped to a workspace + provider)
{
  "kind": "sso",
  "domain": "acme.com",
  "org_id": "org_acme",
  "org_display_name": "Acme Inc.",
  "org_logo_url": "https://cdn.acme.com/logo.png",
  "member_count": 12483,
  "provider_id": "prv_acme_okta",
  "provider_kind": "oidc",            // 'oidc' | 'saml' | 'local'
  "provider_display_name": "Okta",
  "sso_enforced": true,
  "magic_link_supported": false       // sso_enforced=true implies false
}

// 200 — kind=personal (well-known consumer domain, magic-link path)
{
  "kind": "personal",
  "domain": "gmail.com",
  "provider_kind": "magic_link",
  "magic_link_supported": true
}

// 200 — kind=magic_link (unknown domain, fallback)
{
  "kind": "magic_link",
  "domain": "launchco.io",
  "provider_kind": "magic_link",
  "magic_link_supported": true
}

// 200 — kind=unknown (bank-deploy: SSO-only; magic-link disabled)
{
  "kind": "unknown",
  "domain": "gmail.com",
  "provider_kind": null,
  "magic_link_supported": false,
  "message": "Your workspace requires single sign-on. Contact your admin."
}

// 422 — invalid email shape
{ "detail": "invalid_email" }

// 429 — rate limit hit
{ "detail": "rate_limited", "retry_after_seconds": 12 }
```

Discovery is **public** (no bearer). Body is the only input the server reads — never the `x-enterprise-org-id` header. The same 200 envelope is used for `personal` and `magic_link` because the FE renders them identically; the distinction is forensic.

**Why `kind` and not `provider_kind` alone:** `kind` describes the _UI branch_. `personal` and `magic_link` both mean "show the magic‑link CTA," but they tell support whether the user came from a known consumer domain (intentional path) or an unrecognised one (potential typo). The two go to the same backend handler.

### 2.2 Wire — `/v1/auth/magic-link/start`

```http
POST /v1/auth/magic-link/start
Content-Type: application/json
{ "email": "sarah.chen@acme.com", "return_to": "/" }
```

```jsonc
// 202 — always (anti-enumeration)
{ "status": "queued", "expires_in_seconds": 900 }

// 429 — per-IP or per-email rate limit
{ "detail": "rate_limited", "retry_after_seconds": 45 }
```

**Anti‑enumeration is invariant.** Whether the email exists, is unverified, locked out, deleted, or never seen, the response is **always 202**. The only paths that diverge from 202 are pre‑validation: invalid email shape (422) and rate‑limit (429). Behind the 202 the server may have written a row to `magic_link_tokens` and dispatched an email, or it may have done neither — the caller cannot tell from the response.

`return_to` is forwarded into the magic‑link URL as a _signed_ claim on the token (not a query param), preventing post‑login redirect to attacker‑controlled URLs.

### 2.3 Wire — `/v1/auth/magic-link/callback`

```http
GET /v1/auth/magic-link/callback?token=BB6f…&return_to=/chat
```

```jsonc
// 200 — single workspace, session minted
{
  "outcome": "session_minted",
  "bearer_token": "atl_…",          // standard session bearer
  "session_id": "sess_…",
  "user_id": "usr_…",
  "org_id": "org_acme",
  "requires_mfa": false,             // true → frontend mounts MfaPrompt
  "return_to": "/chat"
}

// 200 — multi-workspace, picker required
{
  "outcome": "workspace_pick_required",
  "pick_token": "pick_…",            // 5-minute TTL, single-use
  "expires_in_seconds": 300,
  "user_id": "usr_…",
  "workspaces": [
    {
      "org_id": "org_acme",
      "display_name": "Acme Inc.",
      "logo_url": "https://cdn.acme.com/logo.png",
      "role": "Admin",
      "member_count": 12483,
      "last_active_at": "2026-05-04T18:42:11Z"
    },
    {
      "org_id": "org_acme_eu",
      "display_name": "Acme — EU",
      "role": "Member",
      "member_count": 1240,
      "last_active_at": "2026-04-22T11:08:55Z"
    }
  ]
}

// 401 — token unknown / expired / consumed
{ "detail": "invalid_token" }
```

Every consumption attempt — successful or failed — writes a `login_attempts` row with `auth_kind='magic_link'` and one of `success | invalid_token | expired_token | consumed_token`.

### 2.4 Wire — `/v1/auth/sessions/select`

```http
POST /v1/auth/sessions/select
Content-Type: application/json
{ "pick_token": "pick_…", "org_id": "org_acme" }
```

```jsonc
// 200
{
  "bearer_token": "atl_…",
  "session_id": "sess_…",
  "user_id": "usr_…",
  "org_id": "org_acme",
  "requires_mfa": false
}

// 401 — pick_token unknown / expired / consumed
{ "detail": "invalid_pick_token" }

// 403 — user is not a member of org_id (cross-org probe)
{ "detail": "not_a_member" }
```

The `pick_token` is verifiable in‑band (HMAC over `{user_id, candidate_orgs, exp}` keyed by `ENTERPRISE_AUTH_SECRET`). It does **not** carry an org‑id; the user picks one from the issued list and the server checks membership before minting.

### 2.5 Persistence

```sql
-- 0021_login_email_first.sql

-- Many domains map to many (org, provider) tuples; the same domain can route
-- to OIDC in one org and SAML in another (uncommon but legal). PK is composite
-- so we don't need a synthetic id. CITEXT is already enabled by the SCIM
-- migration (0015) — domains are case-insensitive.
CREATE TABLE IF NOT EXISTS auth_provider_domains (
    domain              CITEXT       NOT NULL,
    org_id              TEXT         NOT NULL,
    provider_id         TEXT         NOT NULL REFERENCES auth_providers(provider_id) ON DELETE CASCADE,
    sso_enforced        BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Set by the discovery cache invalidator (PR 4.2 admin Members panel)
    -- when a domain is claimed / unclaimed; lets the discovery service
    -- short-circuit reads from a 60s memcache without querying the DB.
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by_user_id  TEXT,
    deleted_at          TIMESTAMPTZ,
    PRIMARY KEY (domain, org_id, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_auth_provider_domains_lookup
    ON auth_provider_domains (domain) WHERE deleted_at IS NULL;
ALTER TABLE auth_provider_domains ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON auth_provider_domains
    USING (org_id = current_setting('app.current_org', true));

-- Magic-link tokens: store the SHA-256 of the plaintext, never the plaintext.
-- Token lifetime is 15 minutes; one consumption flips `consumed_at` and that
-- row never resurrects (single-use). `email_lower` is the requested address;
-- it may not match an existing user (anti-enumeration: we issue the row only
-- when the email maps to one).
CREATE TABLE IF NOT EXISTS magic_link_tokens (
    token_id            TEXT         PRIMARY KEY,
    org_id              TEXT,                         -- nullable: pre-pick path
    user_id             TEXT         NOT NULL,        -- always known when row written
    email_lower         CITEXT       NOT NULL,
    token_hash          TEXT         NOT NULL UNIQUE, -- sha256(token)
    candidate_orgs      JSONB        NOT NULL DEFAULT '[]'::jsonb,  -- [{org_id, display_name, ...}]
    return_to           TEXT,
    requested_ip        TEXT,
    requested_ua        TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ  NOT NULL,
    consumed_at         TIMESTAMPTZ,
    consumed_session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_user_active
    ON magic_link_tokens (user_id, created_at DESC) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_expires
    ON magic_link_tokens (expires_at) WHERE consumed_at IS NULL;
-- No RLS: the magic-link pre-pick row may not have an org yet. Cross-org reads
-- are guarded by the application layer (`MagicLinkService` always filters by
-- `token_hash` which is unique).

-- New login_attempts outcomes registered by enum widening (no DDL — outcome
-- is TEXT with a CHECK in 0004; widening done in code path).
-- Outcomes added: 'magic_link_requested', 'magic_link_consumed',
-- 'invalid_token', 'expired_token', 'consumed_token', 'rate_limited',
-- 'workspace_picker_issued', 'workspace_selected'.
ALTER TABLE login_attempts DROP CONSTRAINT IF EXISTS login_attempts_outcome_check;
ALTER TABLE login_attempts ADD  CONSTRAINT login_attempts_outcome_check
    CHECK (outcome IN (
        'success','bad_password','unknown_user','locked_out','mfa_failed',
        'provider_rejected',
        'magic_link_requested','magic_link_consumed','invalid_token',
        'expired_token','consumed_token','rate_limited',
        'workspace_picker_issued','workspace_selected'
    ));

-- New auth_kind value for magic_link.
ALTER TABLE login_attempts DROP CONSTRAINT IF EXISTS login_attempts_auth_kind_check;
ALTER TABLE login_attempts ADD  CONSTRAINT login_attempts_auth_kind_check
    CHECK (auth_kind IN ('local','oidc','saml','mfa','scim_token','api_key','magic_link'));
```

**Why `auth_provider_domains` is a join table, not a column on `auth_providers`:** a single domain can route to multiple providers (uncommon, but `acme.com` users could SAML to one IdP for engineers and OIDC to another for marketing — both real). One column would be wrong; one row per `(domain, org, provider)` is right. Lookup is `O(1)` on the partial index.

**Why `magic_link_tokens.user_id NOT NULL`:** the row is only created **once we've resolved the email to an existing user**. If the email doesn't exist, no row is written, no email is sent, and the response is still 202. This is the anti‑enumeration invariant.

**Why no soft delete on `magic_link_tokens`:** consumed and expired rows are pruned by a daily sweeper job (reuse `session_sweeper.py` pattern). 30‑day retention for forensic value, then drop.

### 2.6 Service path

```
backend-facade  POST /v1/auth/discover            →  backend  POST /internal/v1/auth/discover
backend-facade  POST /v1/auth/magic-link/start    →  backend  POST /internal/v1/auth/magic-link/start
backend-facade  GET  /v1/auth/magic-link/callback →  backend  POST /internal/v1/auth/magic-link/callback
backend-facade  POST /v1/auth/sessions/select     →  backend  POST /internal/v1/auth/sessions/select
```

All four facade routes are **public** (no bearer); the facade still attaches the service token. The backend routes mount under `/internal/v1/auth/*` and use `Depends(public_route())` (the existing pattern in `oidc.py:45`, `46`).

### 2.7 Audit

Every privileged event writes a row to the existing append‑only `identity_audit_events` chain (migration `0002_audit_hardening.sql`):

| Action                         | When                                                                               | Metadata                                             |
| ------------------------------ | ---------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `auth.discover`                | Each discovery hit (rate‑limited; not per attempted email but per resolved domain) | `{ domain, kind, sso_enforced, ip, user_agent }`     |
| `auth.magic_link.requested`    | Each `start` that resolves to a real user                                          | `{ user_id, email_hash, ip, user_agent, return_to }` |
| `auth.magic_link.consumed`     | Each successful `callback` that mints a session or issues a pick token             | `{ user_id, token_id, outcome, ip, user_agent }`     |
| `auth.workspace_pick.issued`   | Each callback that issues a pick token                                             | `{ user_id, candidate_org_ids[], ip }`               |
| `auth.workspace_pick.consumed` | Each successful select                                                             | `{ user_id, org_id, pick_token_id, ip }`             |
| `auth.discovery.rate_limited`  | Each 429 on discover (sample to 1‑in‑100 for high‑volume; reuse existing sampler)  | `{ ip, email_domain, decision }`                     |

`email_hash` is HMAC‑SHA256 of the lower‑cased email keyed by `ENTERPRISE_AUDIT_HASH_KEY`, **not** the plaintext. This is the same hashing pattern `oidc_authentications.email_hash` already uses (commit `df8a31`). Forensic queries match on the hash; the plaintext is never persisted in the chain.

`login_attempts` is also written for every magic‑link consumption attempt (success or failure) with the same outcomes. The two are different tables: `identity_audit_events` is the SIEM‑exportable, chain‑signed timeline; `login_attempts` is the operational table the lockout middleware reads.

### 2.8 Permissions & rate limits

| Endpoint                            | Auth         | Rate limit                                               | Lockout source                                                                 |
| ----------------------------------- | ------------ | -------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `POST /v1/auth/discover`            | Public       | **30 / minute / IP** (LRU bucket; reuse `lockout_store`) | Returns 429 — does not lock the email; this is a probing‑rate cap only.        |
| `POST /v1/auth/magic-link/start`    | Public       | **5 / minute / IP** AND **3 / hour / email**             | Email‑level cap reuses the same `account_lockouts` table as password attempts. |
| `GET  /v1/auth/magic-link/callback` | Public       | **20 / minute / IP** (mostly catches scanner traffic)    | Repeated invalid tokens within 5 minutes → IP lock.                            |
| `POST /v1/auth/sessions/select`     | `pick_token` | **10 / minute / pick_token**                             | Cross‑org probing → IP lock.                                                   |

Rate limits are **policy** rows in the existing `lockout_policies` table (PR A8), keyed by `(scope='auth.discover' | 'auth.magic_link.start' | …)`. Caller does not need to know the values.

`Retry‑After` is set on every 429 response.

### 2.9 Errors

| Endpoint               | Condition                                                  | Status | Code                 |
| ---------------------- | ---------------------------------------------------------- | ------ | -------------------- |
| `/discover`            | invalid email shape                                        | 422    | `invalid_email`      |
| `/discover`            | rate limited                                               | 429    | `rate_limited`       |
| `/magic-link/start`    | invalid email shape                                        | 422    | `invalid_email`      |
| `/magic-link/start`    | rate limited                                               | 429    | `rate_limited`       |
| `/magic-link/callback` | invalid / expired / consumed token                         | 401    | `invalid_token`      |
| `/sessions/select`     | invalid / expired / consumed pick_token                    | 401    | `invalid_pick_token` |
| `/sessions/select`     | user not a member of the chosen `org_id` (cross‑org probe) | 403    | `not_a_member`       |

The `/magic-link/start` shape **does not** return 4xx for nonexistent emails — anti‑enumeration. It returns 202 even when no row is written.

### 2.10 Frontend contract (`@enterprise-search/api-types`)

```ts
// packages/api-types/src/index.ts

export type DiscoverKind = "sso" | "personal" | "magic_link" | "unknown";
export type DiscoverProviderKind =
  | "oidc"
  | "saml"
  | "local"
  | "magic_link"
  | null;

export interface AuthDiscoverRequest {
  email: string;
}
export interface AuthDiscoverResponse {
  kind: DiscoverKind;
  domain: string | null;
  org_id: string | null;
  org_display_name: string | null;
  org_logo_url: string | null;
  member_count: number | null;
  provider_id: string | null;
  provider_kind: DiscoverProviderKind;
  provider_display_name: string | null;
  sso_enforced: boolean;
  magic_link_supported: boolean;
  message: string | null;
}

export interface MagicLinkStartRequest {
  email: string;
  return_to?: string;
}
export interface MagicLinkStartResponse {
  status: "queued";
  expires_in_seconds: number;
}

export type MagicLinkCallbackOutcome =
  | "session_minted"
  | "workspace_pick_required";

export interface MagicLinkCallbackResponse {
  outcome: MagicLinkCallbackOutcome;
  // session_minted branch:
  bearer_token?: string;
  session_id?: string;
  user_id: string;
  org_id?: string;
  requires_mfa?: boolean;
  return_to?: string;
  // workspace_pick_required branch:
  pick_token?: string;
  expires_in_seconds?: number;
  workspaces?: WorkspaceCandidate[];
}

export interface WorkspaceCandidate {
  org_id: string;
  display_name: string;
  logo_url: string | null;
  role: "Admin" | "Member" | "Viewer";
  member_count: number;
  last_active_at: string | null;
}

export interface SessionSelectRequest {
  pick_token: string;
  org_id: string;
}
export interface SessionSelectResponse {
  bearer_token: string;
  session_id: string;
  user_id: string;
  org_id: string;
  requires_mfa: boolean;
}
```

### 2.11 Frontend wiring

The `LoginScreen` is rebuilt as a 4‑step state machine. The states are mutually exclusive and persistent across remounts (URL‑addressable in v2; v1 keeps state in `useState`).

```tsx
type LoginStep =
  | { kind: "email" } // initial; debounced discovery
  | { kind: "redirect"; provider_id: string } // window.location.assign(/v1/auth/oidc/{id}/start?...)
  | { kind: "magic_link_sent"; email: string } // "Check your email"
  | {
      kind: "workspace_pick";
      pick_token: string;
      workspaces: WorkspaceCandidate[];
    } // after callback, multi-org
  | { kind: "mfa" }; // existing MfaPrompt
```

| Concern                | Reuse                                                                                                | Add                                                                                                   |
| ---------------------- | ---------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Auth state             | `AuthContext` state machine (`anonymous → loading → mfa_pending → authenticated`)                    | One new dispatch path: `magic_link_consume(token)`                                                    |
| Bearer storage         | `setBearer` / `localStorage[BEARER_STORAGE_KEY]`                                                     | —                                                                                                     |
| MFA UI                 | `MfaPrompt.tsx`                                                                                      | —                                                                                                     |
| Debounced discovery    | hand‑rolled 12‑LOC `useDebouncedValue<string>` (already an idiom in PR 4.1)                          | `useDiscovery(email): { state, data }` calling `POST /v1/auth/discover`                               |
| Email validation       | One regex + `EmailAddress` polyfill: `/^[^\s@]+@[^\s@]+\.[^\s@]+$/`                                  | —                                                                                                     |
| Adaptive button label  | One `useMemo` over `useDiscovery().data`                                                             | —                                                                                                     |
| Provider tile fallback | Read `auth_providers` for the `system_providers` org via existing `/v1/auth/providers?org_id=system` | One small `<ProviderTiles>` subcomponent                                                              |
| Brand pane             | —                                                                                                    | `<Brand>` (≈ 50 LOC)                                                                                  |
| Compliance row         | —                                                                                                    | One `<ComplianceRow>` (≈ 12 LOC, four `<span>`s)                                                      |
| Workspace picker       | —                                                                                                    | `<WorkspacePicker>` (≈ 80 LOC) listing rows; clicking calls `POST /sessions/select`                   |
| `preventScroll: true`  | already documented pattern (PR 4.1 references the same)                                              | One `inputRef.current?.focus({ preventScroll: true })` on mount                                       |
| Body opt‑out CSS       | —                                                                                                    | 7 LOC in `apps/frontend/src/styles.css` (or a `<Helmet>` if one exists; we don't pull `react-helmet`) |

The plumbing is intentionally thin: every privileged transition (OIDC start, OIDC callback, MFA verify, session list, logout) **already has a backend route and a frontend handler**. The four new routes (discover, magic‑link/start, magic‑link/callback, sessions/select) are the only new code paths the FE adds.

### 2.12 Email dispatcher port

We need to send the magic‑link email. The FE‑facing surface ships the **port** + a **dev adapter**; production injects an SES / SMTP / Postmark adapter at app construction.

```python
# services/backend/src/backend_app/identity/email_dispatcher.py

from typing import Protocol


class EmailDispatcherPort(Protocol):
    def send_magic_link(
        self,
        *,
        to_email: str,
        org_display_name: str | None,
        login_url: str,
        expires_minutes: int,
        request_ip: str | None,
        request_user_agent: str | None,
    ) -> None:
        """Send the magic-link email. Must not raise on transient failure;
        the caller has already returned 202 to the client. Implementations
        should buffer + retry + dead-letter according to their own SLOs."""
        ...


class LoggingEmailDispatcher:
    """Dev / single-tenant fallback. Logs to stdout; never sends an email.
    Production deploys MUST inject a real adapter at app construction."""

    def __init__(self, logger): ...
    def send_magic_link(self, **kw) -> None:
        self._logger.info("magic_link.dispatch", extra=kw)
```

Production adapters live behind separate PRs (per‑deploy choice). The dev adapter is enough to make every test green and every local flow walkable. Bank deploys with magic‑link disabled at the policy layer never call into the dispatcher at all.

This is the same shape as `TokenVault` (local fallback + injectable production adapter) — a port we already use across the codebase.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
                                           ┌─────────────────────────────────────┐
                                           │  apps/frontend                      │
                                           │  LoginScreen 4-step machine          │
                                           │   ├─ <Brand> (left pane)             │
                                           │   ├─ <EmailStep> (450 ms debounce)   │
                                           │   ├─ <RedirectStep> (OIDC/SAML)      │
                                           │   ├─ <MagicLinkSent>                 │
                                           │   ├─ <WorkspacePicker>               │
                                           │   └─ <MfaPrompt>  (existing)         │
                                           └────┬────────────────────────────────┘
                                                │ POST /v1/auth/discover
                                                │ POST /v1/auth/magic-link/start
                                                │ GET  /v1/auth/magic-link/callback
                                                │ POST /v1/auth/sessions/select
                                                ▼
                                           ┌─────────────────────────────────────┐
                                           │  backend-facade  (4 new routes,     │
                                           │   pure proxy, public_route())       │
                                           └────┬────────────────────────────────┘
                                                │ /internal/v1/auth/{discover,
                                                │   magic-link/{start,callback},
                                                │   sessions/select}
                                                ▼
                ┌────────────────────────────────────────────────────────────────────────┐
                │  backend                                                                │
                │                                                                         │
                │   DiscoveryService ──→ auth_provider_domains (NEW) + auth_providers     │
                │     │                                                                   │
                │     ├─→ existing OidcService ──→ /v1/auth/oidc/{id}/start  (REUSE)      │
                │     ├─→ existing SamlService ──→ /v1/auth/saml/{id}/start  (REUSE)      │
                │     └─→ MagicLinkService                                                │
                │                │                                                       │
                │                ├─→ EmailDispatcherPort (LoggingEmailDispatcher in dev)  │
                │                ├─→ magic_link_tokens (NEW)                              │
                │                └─→ SessionService.create  (REUSE)                      │
                │                                                                         │
                │   identity_audit_events (chain)  ←── all of the above                  │
                │   login_attempts (operational)   ←── all of the above                  │
                │   account_lockouts (rate limit)  ←── lockout middleware (REUSE)        │
                └────────────────────────────────────────────────────────────────────────┘
```

Nothing in `services/ai-backend/` is touched. The agent harness is a strict consumer of `sessions` (it reads bearer tokens via `runtime_api`'s authentication layer), and that contract does not change — a session minted by magic‑link is byte‑identical to a session minted by OIDC, SAML, or local.

### 3.2 Streaming impact — explicitly **none**

| Subsystem                                             | Touched?                                             |
| ----------------------------------------------------- | ---------------------------------------------------- |
| `runtime_events` schema                               | No                                                   |
| `RuntimeEventEnvelope` / SSE handshake                | No                                                   |
| Worker job loop                                       | No                                                   |
| Capabilities / tools / MCP loaders                    | No                                                   |
| Citation registry, drafts, approvals, subagents       | No                                                   |
| `agent_conversations`, `agent_runs`, `agent_messages` | No                                                   |
| Audit chain on the runtime side (`runtime_audit_log`) | No                                                   |
| MFA prompt + WebAuthn ceremony                        | No (reused unchanged)                                |
| OIDC / SAML privileged code paths                     | No (entry ramp is new; the ramp itself is unchanged) |

Login completes before any conversation is created. The session that lands in `AuthContext` is the same shape today as after this PR — only the _path_ that produced it is new. The streaming subsystem doesn't notice.

### 3.3 Why discovery + magic‑link live in **backend**, not ai‑backend

Same boundary call as PR 1.6 §3.3 and PR 4.1 §3.3, applied here:

- The **runtime** (ai‑backend) consumes a session, never an auth flow. It has no business knowing what produced the bearer.
- Identity, providers, sessions, lockouts all live in **backend** today; this PR sidecars onto identity.
- Putting magic‑link in ai‑backend would be a circular boundary violation (ai‑backend already calls backend for sessions; it can't also own them).

### 3.4 DRY — what we reuse vs. what we add

| Concern                    | Reuse                                                                                                 | Add                                                                                          |
| -------------------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Identity / RBAC            | `BackendServiceAuthenticator.internal_scoped_identity` for the "anonymous-with-service-token" pattern | —                                                                                            |
| Auth providers             | `auth_providers` table; `OidcService.authorize`; `SamlService.start`; `password_login`                | One join table (`auth_provider_domains`)                                                     |
| Session minting            | `SessionService.create` (already used by OIDC callback and password‑login flow)                       | —                                                                                            |
| MFA                        | `MfaPrompt.tsx`, `mfa_service`, `mfa_store`                                                           | —                                                                                            |
| Bearer + AuthContext       | `AuthContext.refresh()`, `AuthContext.completeMfa()`                                                  | One new dispatch path in `AuthContext.login()` for the magic‑link callback shape             |
| Lockout / rate limit       | `lockout_store`, `account_lockouts`, `lockout_policies` (PR A8)                                       | Three new policy rows (`auth.discover`, `auth.magic_link.start`, `auth.magic_link.callback`) |
| Audit chain                | `identity_audit_events`, append‑only trigger (`0002_audit_hardening.sql`)                             | Six new `action` constants                                                                   |
| Login attempts             | `login_attempts`                                                                                      | Eight new outcomes; one new `auth_kind='magic_link'`                                         |
| Email hashing              | `oidc_authentications.email_hash` HMAC pattern                                                        | Reuse `ENTERPRISE_AUDIT_HASH_KEY`                                                            |
| HMAC / signed tokens       | `pick_token` shape mirrors `oidc_authentications.state` (HMAC over a JSON claim set, base64url)       | One `MagicLinkTokenCodec` (≈ 30 LOC) — mirrors the OIDC state codec                          |
| Email dispatcher           | `TokenVault` adapter pattern — port + local fallback + injectable production adapter                  | One `EmailDispatcherPort` + `LoggingEmailDispatcher`                                         |
| Pre-login routes           | `Depends(public_route())` (existing in `oidc.py:45`, `46`, `108`)                                     | —                                                                                            |
| Facade proxy template      | `_anonymous_service_headers(org_id=...)` (existing in `auth_routes.py:135`)                           | Four new `register_auth_routes` handlers                                                     |
| `MfaPrompt` mount          | The existing `requires_mfa=true` branch in `AuthContext`                                              | —                                                                                            |
| Workspace candidates query | `organization_members` × `organizations` × `sessions.last_seen_at` (all existing)                     | One repository method `list_membership_candidates_for_user(user_id)`                         |
| Bank‑profile toggle        | `FacadeSettings.deployment_profile` (existing C1 toggle)                                              | One bool: `magic_link_enabled` (defaults `true`; bank deploy sets `false`)                   |
| Body scroll lock opt‑out   | —                                                                                                     | 7 LOC of CSS in `apps/frontend/src/styles.css`                                               |
| Compliance badges          | —                                                                                                     | 12 LOC of static JSX (badges are text only)                                                  |

The new code is a **service** (`MagicLinkService`), a **port** (`EmailDispatcherPort`), one **table** (`auth_provider_domains`), one **migration** (the table + the constraint widening), six **audit actions**, four **routes** on the backend, four **proxy routes** on the facade, four **types** in api‑types, and a **rebuilt frontend screen**. Everything else is reuse.

### 3.5 Pre‑built libraries — what we considered, what we use

| Need              | Considered                                                                     | Decision                                                                                                                                                                                                                            |
| ----------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Email validation  | `email-validator` (Python, pyca), `validator.js`, browser `<input type=email>` | **Browser + 1 regex.** RFC 5322 in full is overkill; the design wants "looks like an email" = `[^@]+@[^@]+\.[^@]+`. Server re‑validates with `email-validator` only if it's already on the path; falls through to the regex if not. |
| Magic‑link token  | `pyjwt`, `itsdangerous`, hand‑rolled HMAC                                      | **Hand‑rolled HMAC over a JSON claim set + base64url.** This is the same pattern `oidc_authentications.state` already uses (`identity/oidc.py`). One module, ~40 LOC. JWT would be over‑engineered: we don't need claims interop.   |
| Email sender      | `aiosmtplib`, `boto3.ses`, `httpx` to Postmark, `mailgun`, raw SMTP            | **Port + local fallback + injectable adapter.** Decision is per‑deploy. v1 ships `LoggingEmailDispatcher`. Production adapter is a separate PR per environment. We do **not** pull a sender lib here.                               |
| Debounce          | `lodash.debounce`, `use-debounce`, hand‑rolled                                 | **Hand‑rolled 12 LOC.** Same call PR 4.1 §2.9 made; consistent.                                                                                                                                                                     |
| Domain parsing    | `tldts`, `psl` (public suffix list), regex                                     | **Lower‑case + split on `@` + check for `.`.** We do not need `eTLD+1` resolution: we look up the **full domain** in `auth_provider_domains`. Subdomain routing is a future PR.                                                     |
| Rate limit        | `slowapi`, `fastapi-limiter`, custom                                           | **Reuse `lockout` middleware.** It's the same operational concept (counters keyed by IP + window); we add three policy rows. No new dep.                                                                                            |
| WebAuthn          | `@simplewebauthn/browser`, hand‑rolled                                         | **Already hand‑rolled in `MfaPrompt.tsx`.** No change.                                                                                                                                                                              |
| State machine UI  | `xstate`, `zustand`, plain `useState`                                          | **`useState` with a `LoginStep` discriminated union.** Five states; xstate would add weight without paying off until the picker grows.                                                                                              |
| Brand SVGs        | `react-icons`, `simple-icons`                                                  | **Inline SVG.** The prototype already inlines them; ~5 logos, < 100 LOC total. A lib is a bigger surface than the bytes it replaces.                                                                                                |
| Compliance badges | `@trust/badges`, etc.                                                          | **Plain text.** "SOC 2 Type II · ISO 27001 · GDPR · HIPAA" is text. Logos would imply certification on the frontend bundle which is mis‑leading.                                                                                    |
| Form library      | `react-hook-form`, `formik`                                                    | **Skip.** One field per step.                                                                                                                                                                                                       |
| URL signing       | `itsdangerous`, `pyjwt`                                                        | **Reuse the OIDC state codec module.** Same key, same shape, same key rotation.                                                                                                                                                     |
| OIDC client       | `authlib`, `python-jose`, custom                                               | **Already in tree (`OidcService`).** Reused unchanged.                                                                                                                                                                              |
| SAML client       | `python3-saml`, `xmlsec`                                                       | **Already in tree (`SamlService`).** Reused unchanged.                                                                                                                                                                              |

The deciding rule: **the right number of new top‑level deps for this PR is zero.** Every behaviour we need is either in the tree, in the stdlib, or fits in 40 LOC.

### 3.6 Sequence — Sarah, Acme, Okta SSO (US‑1)

```
Sarah                Frontend                 backend-facade           backend                 Okta
 │                     │                             │                    │                       │
 │  /login             │                             │                    │                       │
 │ ──────────────────► │                             │                    │                       │
 │                     │ <Brand> renders             │                    │                       │
 │                     │ inputRef.focus({preventScroll:true})              │                       │
 │                     │                             │                    │                       │
 │  type "sarah.chen@acme.com"                       │                    │                       │
 │ ──────────────────► │ (debounce 450 ms)           │                    │                       │
 │                     │ POST /v1/auth/discover       │                    │                       │
 │                     │   { email }                  │                    │                       │
 │                     │ ─────────────────────────► │                    │                       │
 │                     │                             │ → /internal/v1/auth/discover               │
 │                     │                             │ ─────────────────► │                       │
 │                     │                             │                    │ DiscoveryService       │
 │                     │                             │                    │   lookup auth_provider_domains[acme.com]
 │                     │                             │                    │   → org_acme + prv_acme_okta
 │                     │                             │                    │   audit auth.discover  │
 │                     │                             │ ◄───── 200 sso ─── │                       │
 │                     │ ◄───── 200 sso ──────────── │                    │                       │
 │                     │ <DiscoveryCard> renders     │                    │                       │
 │                     │ button label: "Continue with Okta"               │                       │
 │                     │                             │                    │                       │
 │  click "Continue with Okta"                       │                    │                       │
 │ ──────────────────► │ window.location.assign(     │                    │                       │
 │                     │   /v1/auth/oidc/prv_acme_okta/start?org_id=org_acme&… )                  │
 │                     │ ────────────────────────► │                    │                       │
 │                     │                             │ → existing OIDC start (NO CHANGE)          │
 │                     │                             │ ─────────────────► │                       │
 │                     │                             │                    │ OidcService.authorize  │
 │                     │                             │                    │ → 302 to Okta auth_url │
 │                     │                             │ ◄───── 302 ──────  │                       │
 │ ◄────────────────── │ ◄───── 302 (browser-driven) │                    │                       │
 │                     │ Browser navigates to Okta   │                    │                       │
 │  authenticate at Okta ────────────────────────────────────────────────────────────────────► │
 │                                                                                             ▼
 │ ◄───── 302 back to /v1/auth/oidc/callback?state=…&code=… ──────────────────────────────────  │
 │                     │ /v1/auth/oidc/callback (existing)                │                       │
 │                     │ ───────────────────────► │                    │                       │
 │                     │                             │ → /internal/v1/auth/oidc/callback          │
 │                     │                             │ ─────────────────► │                       │
 │                     │                             │                    │ Token exchange + session.create │
 │                     │                             │                    │ → bearer_token         │
 │                     │                             │ ◄───── 200 ──────  │                       │
 │                     │ AuthContext.refresh()       │                    │                       │
 │                     │ status = authenticated      │                    │                       │
 │ ◄───── /chat ────── │                             │                    │                       │
```

Branch where Okta returned `requires_mfa=true`: the response includes that flag, `AuthContext` flips to `mfa_pending`, and the existing `MfaPrompt` mounts. No new wire.

### 3.7 Sequence — Devi, gmail.com, magic‑link (US‑2)

```
Devi                Frontend                 backend-facade           backend                 EmailDispatcher
 │                    │                             │                    │                          │
 │  type "devi@gmail.com"                            │                    │                          │
 │ ─────────────────► │  POST /v1/auth/discover     │                    │                          │
 │                    │ ─────────────────────────► │ → /internal/v1/auth/discover                  │
 │                    │                             │ ─────────────────► │                          │
 │                    │                             │                    │ DiscoveryService          │
 │                    │                             │                    │   gmail.com is in PERSONAL_DOMAINS │
 │                    │                             │                    │   bank profile? no       │
 │                    │                             │ ◄── 200 personal ── │                         │
 │                    │ <DiscoveryCard> "Personal Google · We'll email you a sign-in link"           │
 │                    │ button label: "Email me a sign-in link"                                       │
 │                    │                             │                    │                          │
 │  click             │ POST /v1/auth/magic-link/start { email, return_to:'/' }                      │
 │ ─────────────────► │ ─────────────────────────► │ → /internal/v1/auth/magic-link/start          │
 │                    │                             │ ─────────────────► │                          │
 │                    │                             │                    │ MagicLinkService.request  │
 │                    │                             │                    │   resolve email → user_id │
 │                    │                             │                    │     case A: user exists   │
 │                    │                             │                    │       INSERT magic_link_tokens(user_id, hash, expires_at=+15m) │
 │                    │                             │                    │       login_attempts(magic_link_requested) │
 │                    │                             │                    │       audit auth.magic_link.requested │
 │                    │                             │                    │       EmailDispatcherPort.send_magic_link(login_url, …) ─►
 │                    │                             │                    │     case B: user unknown  │
 │                    │                             │                    │       no row, no email    │
 │                    │                             │                    │       login_attempts(unknown_user, magic_link) │
 │                    │                             │ ◄── 202 queued ── │                          │
 │                    │ <MagicLinkSent> renders     │                    │                          │
 │ ◄───── 202 ─────── │ "Check your email; expires in 15 minutes"        │                          │
 │                                                                                                  │
 │ ⌚ … later, opens inbox                                                                          │
 │ click magic-link URL                                                                             │
 │                    │ GET /v1/auth/magic-link/callback?token=…                                    │
 │ ─────────────────► │ ─────────────────────────► │                    │                          │
 │                    │                             │ → /internal/v1/auth/magic-link/callback       │
 │                    │                             │ ─────────────────► │                          │
 │                    │                             │                    │ MagicLinkService.consume  │
 │                    │                             │                    │   sha256(token) lookup    │
 │                    │                             │                    │   single workspace? yes   │
 │                    │                             │                    │   SessionService.create   │
 │                    │                             │                    │     mfa_satisfied=false   │
 │                    │                             │                    │   audit auth.magic_link.consumed │
 │                    │                             │                    │   login_attempts(magic_link_consumed)│
 │                    │                             │ ◄── 200 session ── │                          │
 │                    │ AuthContext.setBearer(...)  │                    │                          │
 │                    │ AuthContext.refresh()       │                    │                          │
 │ ◄── /chat ──────── │                             │                    │                          │
```

Multi‑workspace branch: the callback returns `outcome=workspace_pick_required` with a `pick_token` and a list. FE renders `<WorkspacePicker>`; clicking a row calls `POST /v1/auth/sessions/select { pick_token, org_id }`; the response is the final session bearer.

### 3.8 Edge cases

| Case                                                                                  | Behaviour                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User types email faster than 450 ms debounce can fire                                 | Latest email value wins; only one in‑flight discovery (cancellable via `AbortController`). Same idiom as PR 4.1.                                                                                                     |
| User submits before debounce settles                                                  | Frontend cancels the in‑flight discovery, calls `POST /discover` synchronously, branches once the result lands.                                                                                                      |
| Network down during discovery                                                         | UI keeps the input editable, shows a soft "couldn't reach the auth service" hint; the **submit** button still works (it submits to discover synchronously).                                                          |
| Magic‑link clicked twice (user re‑opens email)                                        | First click: 200 (consumed). Second click: 401 `consumed_token`. UI renders "this link was already used; request a new one." Audit row written for both attempts.                                                    |
| Magic‑link clicked after expiry                                                       | 401 `expired_token`. Audit row. UI renders "this link expired; request a new one."                                                                                                                                   |
| User has 0 workspaces (deactivated everywhere)                                        | Callback returns `outcome=session_minted` to a deactivated user is **never** issued — `MagicLinkService.consume` checks `users.status='active'` first and returns `401 invalid_token` if not. Cannot leak existence. |
| User has > 100 workspaces (contractor at every Atlas customer)                        | Picker renders a search box at the top; rows above 100 are virtualised. Practically capped at 50 in v1 (`LIMIT 50` in `list_membership_candidates_for_user`).                                                        |
| User picks an org_id they aren't a member of (cross‑org probe with stolen pick_token) | 403 `not_a_member`. Audit. Token consumed. IP rate‑limit increments.                                                                                                                                                 |
| Pick token reused                                                                     | 401 `invalid_pick_token` (single‑use; consumed on first select).                                                                                                                                                     |
| Bank‑profile deploy + personal email                                                  | Discovery returns `kind='unknown'` with `magic_link_supported=false` and `message="Your workspace requires single sign-on. Contact your admin."` Submit blocked.                                                     |
| Bank‑profile deploy + workspace email + magic‑link disabled at provider level         | Discovery returns `kind='sso'` with `magic_link_supported=false`. Submit goes to OIDC start as today.                                                                                                                |
| `auth_provider_domains` lookup miss for a workspace email that _should_ match         | Falls through to `kind='magic_link'` (or `kind='unknown'` in bank profile). Admin needs to claim the domain in PR 4.2 Members panel.                                                                                 |
| Old `LoginScreen` URL bookmarks (`?org_id=acme`)                                      | Component reads `?org_id` and pre‑fills it as a hint to discovery (matches by org_id when the user types their email, jumping straight to the matching provider). Backwards‑compatible.                              |
| `OidcService.callback` already returns `mfa_pending`                                  | Callback's `200` shape now includes `requires_mfa=true` (already does — `MfaPrompt` mount path is unchanged).                                                                                                        |
| User cancels the SSO redirect mid‑flight                                              | Browser back returns to `/login`; component remounts in the `email` step with the previous email value pre‑filled (held in `sessionStorage` keyed by tab).                                                           |
| Two tabs, two discoveries in flight                                                   | Each tab manages its own state; no shared state.                                                                                                                                                                     |
| Browser back from `/login` after authentication                                       | Existing `<AuthGate>` redirects to `/chat` if `status=authenticated`. No change.                                                                                                                                     |

### 3.9 Test plan

**Backend (`services/backend/tests/`)**

- `unit/auth/test_discovery_service.py`
  - SSO domain → `kind=sso` with org / provider / member_count
  - personal domain → `kind=personal`
  - unknown domain → `kind=magic_link` (default profile) and `kind=unknown` (bank profile)
  - invalid email → 422
  - rate limit → 429 with Retry‑After
  - audit row on every successful discovery
- `unit/auth/test_magic_link_service.py`
  - request for known user → row written, email sent (LoggingEmailDispatcher captured), 202
  - request for unknown user → no row, no email, 202
  - rate limit per email → 429 after 3rd request in an hour
  - rate limit per IP → 429 after 5th request in a minute
  - consume valid token, single workspace → SessionService.create called, session minted
  - consume valid token, multi workspace → pick_token issued, no session yet
  - consume expired token → 401 expired_token
  - consume already‑consumed token → 401 consumed_token
  - consume token for deactivated user → 401 invalid_token (no membership leak)
  - select with valid pick_token + valid org → session minted
  - select with valid pick_token + cross‑org → 403 not_a_member
- `integration/test_login_flows.py`
  - Full email‑first → OIDC happy path (mocks Okta)
  - Full email‑first → magic‑link single‑workspace happy path
  - Full email‑first → magic‑link multi‑workspace + picker happy path
  - Bank profile rejects personal email
  - Anti‑enumeration: two emails (one exists, one doesn't) produce identical 202 responses (timing‑equivalent within the 95th percentile)
- `integration/test_auth_audit_chain.py`
  - Each privileged event writes one row; chain verifier passes
  - SIEM exporter picks up new actions

**Frontend (`apps/frontend/src/features/auth/`)**

- `LoginScreen.test.tsx`
  - Initial render: email field focused without scroll; brand pane visible; compliance row present
  - Type 450 ms → discovery fires once; button relabels
  - SSO discovery → click button → `window.location.assign` called with the OIDC start URL
  - Personal discovery → click → magic‑link/start fires → `<MagicLinkSent>` mounts
  - Unknown discovery → click → magic‑link path (default profile)
  - Bank profile + personal email → submit blocked, error rendered
- `WorkspacePicker.test.tsx`
  - Renders rows from a fixture; click → `/sessions/select` fires; on 200 sets bearer + refreshes
  - 401 on stale pick_token → routes back to `<EmailStep>`
- `AuthContext.test.tsx` (extended)
  - Magic‑link callback shape (`session_minted`) flows through to `authenticated`
  - Magic‑link callback shape (`workspace_pick_required`) flows to a new state and waits for a select call
- E2E (Playwright, optional)
  - Full happy path on a seeded local stack: type email → see discovery → magic‑link → click email link from the dispatcher log → land authenticated

**Cross‑service smoke (`make test`)** — one happy path per branch (SSO + magic‑link).

**Anti‑enumeration timing test** — measure `POST /magic-link/start` mean and 95th‑percentile latency for an existing email vs a nonexistent email; assert overlap (< 5% delta) under steady load. The dispatcher fires asynchronously, so wall‑clock timing should be identical regardless of email validity.

### 3.10 Rollout

- **Behind a deploy flag.** `FACADE_LOGIN_EMAIL_FIRST=true` (defaults `true` in dev, `false` until the first prod deploy). When `false`, the legacy `LoginScreen` (org‑id + IdP picker) renders. Roll forward by flipping the flag per environment.
- **Migration is reversible.** `0021_login_email_first.sql` creates `auth_provider_domains` (drop), creates `magic_link_tokens` (drop), widens two `login_attempts` constraints (rollback restores the older constraint set). No data loss on backout.
- **Backout.** Flip the flag → legacy login renders → discovery + magic‑link routes still respond (no consumers); `magic_link_tokens` rows expire on their own; `auth_provider_domains` is harmless if unread. Drop migration only after the flag has been off in prod for 30 days.
- **Forward compatibility.** The `discover` envelope is open to additive fields (e.g. a future `passkey_supported: true`); the `magic_link_callback` outcome enum is open to additive variants (e.g. `outcome='requires_workspace_creation'` for first‑time signup).

### 3.11 Open questions

1. **Should `kind='personal'` open the OIDC tile for that consumer provider directly?** v1 sends personal domains to magic‑link. A future PR could route `gmail.com` to a Google OIDC tile; deferred until we have a per‑deploy decision on which consumer providers to wire (some bank deploys disallow consumer SSO entirely).
2. **Should we cache the discovery response client‑side?** A user switching tabs and re‑typing the same email re‑hits the server every time. Caching is an optimisation; v1 calls per keystroke‑debounce and accepts the cost.
3. **Should the workspace picker support "create a new workspace from this email's domain"?** That's the signup path (out of scope §1.3). Until we have signup, the picker shows only existing memberships.
4. **Magic‑link email template ownership.** v1 ships English text; Sarah's `locale` (PR 4.1) is wired into the dispatcher _port_ but the LoggingEmailDispatcher ignores it. Production adapters will translate per their own templating.
5. **Subdomain‑suffix routing** (`@eng.acme.com` → `acme.com`'s Okta). v1 looks up the full domain only. eTLD+1 collapse is a future enhancement; for now admins claim each subdomain explicitly.

---

## 4 · Acceptance checklist

### Backend

- [ ] Migration `0021_login_email_first.sql` applies forward + rolls back cleanly. Tables: `auth_provider_domains`, `magic_link_tokens`. Constraint widening: `login_attempts.outcome`, `login_attempts.auth_kind`.
- [ ] `DiscoveryService` resolves `(email) → DiscoverResponse` for SSO, personal, unknown, bank‑profile cases.
- [ ] `MagicLinkService.request` writes one row per known email, zero rows for unknown emails, always returns 202.
- [ ] `MagicLinkService.consume` mints a session for single‑workspace users, issues a pick_token for multi‑workspace users, 401 for deactivated users without leaking existence.
- [ ] `SessionSelectService.exchange` mints the final bearer when the user is a member of the chosen org, 403 otherwise.
- [ ] `EmailDispatcherPort` defined; `LoggingEmailDispatcher` is the dev/local default; production adapter injection point documented.
- [ ] All four routes mount under `/internal/v1/auth/*` with `Depends(public_route())` and the existing service‑token gate.
- [ ] Six new actions registered in `IdentityAuditAction`. Eight new outcomes in `LoginAttemptOutcome`. Two new auth_kinds in scope. Chain verifier passes.
- [ ] Three new lockout policies: `auth.discover` (30/min/IP), `auth.magic_link.start` (5/min/IP, 3/hour/email), `auth.magic_link.callback` (20/min/IP).
- [ ] Anti‑enumeration timing test passes (< 5% mean‑latency delta between known and unknown email).
- [ ] Backend pytest suite green.

### Backend‑facade

- [ ] `register_auth_routes` adds `POST /v1/auth/discover`, `POST /v1/auth/magic-link/start`, `GET /v1/auth/magic-link/callback`, `POST /v1/auth/sessions/select`.
- [ ] All four routes are pure proxies (no business logic). `_anonymous_service_headers(org_id=...)` reused.
- [ ] None of the four routes reaches `/internal/v1/*` from the browser.

### `@enterprise-search/api-types`

- [ ] Exports: `DiscoverKind`, `DiscoverProviderKind`, `AuthDiscoverRequest`, `AuthDiscoverResponse`, `MagicLinkStartRequest`, `MagicLinkStartResponse`, `MagicLinkCallbackOutcome`, `MagicLinkCallbackResponse`, `WorkspaceCandidate`, `SessionSelectRequest`, `SessionSelectResponse`.
- [ ] Typecheck green. Build green.

### Frontend

- [ ] `LoginScreen.tsx` rebuilt as a 4‑step state machine (`email | redirect | magic_link_sent | workspace_pick`), with the existing `MfaPrompt` mounted on `mfa_pending`.
- [ ] `<Brand>` (right pane), `<DiscoveryCard>`, `<MagicLinkSent>`, `<WorkspacePicker>`, `<ProviderTiles>` (collapsed) ship.
- [ ] Compliance row present at the bottom of the brand pane.
- [ ] Email input is auto‑focused with `preventScroll: true`.
- [ ] `html.login-html, body.login-body { overflow: auto; height: auto }` opt‑out CSS added.
- [ ] `useDiscovery(email)` calls `POST /v1/auth/discover` with `AbortController` cancellation; debounced 450 ms; cancelled on submit.
- [ ] Submit branches: SSO → `window.location.assign` to OIDC `/start`; magic‑link → `POST /magic-link/start`; bank‑profile + personal → blocked with the discovery `message`.
- [ ] Magic‑link callback page mounts `LoginScreen` in a new step (`magic_link_callback`) that consumes `?token=` on first render, then either authenticates (single‑workspace) or shows the picker (multi‑workspace).
- [ ] `AuthContext` extended with one new transition: `magic_link_consume(token)`. No other state changes.
- [ ] `MfaPrompt` rendered byte‑for‑byte (no diff in `MfaPrompt.tsx`).
- [ ] Frontend typecheck green. Build green. Tests green.
- [ ] No new top‑level dep in `apps/frontend/package.json`.

### System‑level

- [ ] Streaming handshake byte‑identical pre/post merge.
- [ ] Agent harness, runtime events, capabilities, MCP loaders, citation registry, drafts, approvals, subagents — none touched.
- [ ] `make test` green.
- [ ] CI lints + ruff + prettier green.
- [ ] Bank‑profile e2e: personal email rejection flow validated end‑to‑end.

---

## 5 · References

- Design Doc · `Login` page + `Flow — email‑first / progressive` + `Decisions log → Email‑first login (vs. tabbed picker)` + `Decisions log → Auto‑focus the email input without scrolling` — bundle at `/tmp/design-doc/enterprise-search/project/Design Doc.html` lines 402–446.
- Prototype · [`/tmp/design-doc/enterprise-search/project/login-page.jsx`](/tmp/design-doc/enterprise-search/project/login-page.jsx) — visual reference for steps, brand pane, discovery card.
- [`/tmp/design-doc/enterprise-search/project/login.css`](/tmp/design-doc/enterprise-search/project/login.css) — layout / dimensions / scroll‑lock opt‑out.
- [`apps/frontend/src/features/auth/LoginScreen.tsx`](../../apps/frontend/src/features/auth/LoginScreen.tsx) — the file this PR rebuilds.
- [`apps/frontend/src/features/auth/MfaPrompt.tsx`](../../apps/frontend/src/features/auth/MfaPrompt.tsx) — reused unchanged.
- [`apps/frontend/src/features/auth/AuthContext.tsx`](../../apps/frontend/src/features/auth/AuthContext.tsx) — one new transition.
- [`services/backend/src/backend_app/routes/oidc.py`](../../services/backend/src/backend_app/routes/oidc.py) — `Depends(public_route())` pattern reused for the four new routes.
- [`services/backend/src/backend_app/routes/sessions.py`](../../services/backend/src/backend_app/routes/sessions.py) — `SessionService.create` reused.
- [`services/backend/src/backend_app/identity/lockout.py`](../../services/backend/src/backend_app/identity/lockout.py) + [`lockout_store.py`](../../services/backend/src/backend_app/identity/lockout_store.py) — rate‑limit policies extended.
- [`services/backend/migrations/0004_identity_foundation.sql`](../../services/backend/migrations/0004_identity_foundation.sql) lines 99–161 — `auth_providers`, `identity_audit_events`, `login_attempts` (the tables this PR sidecars onto).
- [`services/backend/migrations/0002_audit_hardening.sql`](../../services/backend/migrations/0002_audit_hardening.sql) — append‑only chain.
- [`services/backend-facade/src/backend_facade/auth_routes.py`](../../services/backend-facade/src/backend_facade/auth_routes.py) lines 41–200 — proxy template.
- RFC 7396 — JSON Merge Patch (none of this PR's writes are PATCH; referenced for shape consistency with PR 4.1).
- RFC 6749 §10.6 — protect against open redirects (`return_to` is a _signed claim_, not a URL parameter the server forwards verbatim).
- RFC 8252 — OAuth for native apps (forward reference for the future passkey flow).
- [`docs/new-design/pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md) — same audit / hashing / `me` route shape.
- [`docs/new-design/pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — append‑only chain pattern.
- [`docs/new-design/pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — RFC 7396 + audit on write.
- [OWASP — Authentication Cheat Sheet § Magic Links](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) — single‑use, short TTL, anti‑enumeration response shape.
- [OWASP — Forgot Password Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html) — same family of patterns; "always return the same response" rule.
