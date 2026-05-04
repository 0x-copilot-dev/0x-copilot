# PR 17 — A8: Login Attempts Audit, Rate Limiting, and Account Lockout

**Spec ID:** A8 | **Track:** Identity & Access | **Wave:** 4 (Auth Completion) | **Estimated effort:** M
**Depends on:** A1 (login_attempts table created there), A3, A4, A5, A6
**Required for:** A9 (frontend shows attempt history), A10 (admin unlock route)

---

## 1. Functional Specification

### 1.1 Goal

Detect and block credential-stuffing and brute-force attacks. `login_attempts` table was created in A1 and populated by A3/A4/A5/A6 — this PR reads it and enforces rate limits + account lockout. Required by NIST SP 800-63B §5.2.2.

### 1.2 User-visible behavior

- **End user (after N failed attempts):** sees `423 Locked` + a clear message saying when they can try again.
- **End user (during cooldown):** correct password also returns 423 (lockout supersedes password check).
- **Admin:** can force-unlock a user; can view recent attempt history.
- **End user:** sees their own recent login attempts via `GET /v1/auth/me/login-attempts`.

### 1.3 Out of scope

- Distributed rate limiting (single-Postgres for now; Redis later if needed).
- CAPTCHA (left as a hook; not implemented).
- IP allow/deny lists.

---

## 2. Technical Specification

### 2.1 Architecture

- Sliding-window rate limit per `(org_id, email)` and per `(ip)` — token bucket implemented in Postgres (single source of truth, no Redis).
- Lockout after `max_failures` failures within `failure_window_seconds`. Auto-unlock after `lockout_duration_seconds`. Permanent lockout after N consecutive auto-unlock periods (escalation hook).
- Admin force-unlock leaves an audit row.
- Per-org policies via `lockout_policies` table.

### 2.2 Schema changes

Migration `services/backend/migrations/0011_account_lockouts.sql`:

```sql
-- login_attempts already exists from A1.

CREATE TABLE account_lockouts (
    lockout_id           TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL,
    user_id              TEXT NOT NULL REFERENCES users(user_id),
    locked_at            TIMESTAMPTZ NOT NULL,
    lock_reason          TEXT NOT NULL,
    auto_unlock_at       TIMESTAMPTZ,
    unlocked_at          TIMESTAMPTZ,
    unlocked_by_user_id  TEXT
);
CREATE UNIQUE INDEX idx_account_lockouts_active
    ON account_lockouts (org_id, user_id) WHERE unlocked_at IS NULL;

CREATE TABLE lockout_policies (
    policy_id                    TEXT PRIMARY KEY,
    org_id                       TEXT NOT NULL UNIQUE,
    max_failures                 INTEGER NOT NULL DEFAULT 5,
    failure_window_seconds       INTEGER NOT NULL DEFAULT 300,
    lockout_duration_seconds     INTEGER NOT NULL DEFAULT 900,
    permanent_after_n_lockouts   INTEGER NOT NULL DEFAULT 0,   -- 0 = never permanent
    updated_at                   TIMESTAMPTZ NOT NULL
);
```

### 2.3 Endpoints

**Facade public:**

- `GET /v1/auth/me/login-attempts?limit=20` — caller's recent attempts.

**Backend internal:**

- `POST /internal/v1/auth/lockouts/{user_id}/unlock` — admin unlock; requires admin scope.
- `GET /internal/v1/auth/lockouts?org_id=&active=true` — admin list.
- `GET /internal/v1/auth/login-attempts?org_id=&user_id=&since=` — paged for SIEM export.

### 2.4 Code changes

**New:**

- `services/backend/src/backend_app/identity/lockout.py` — `LockoutService` with `check(org_id, email_or_user)`, `record_failure(...)`, `record_success(...)`, `force_unlock(...)`, `policy_for(org_id)`.

**Hook into login paths from A3/A4/A5/A6:**

- A3 OIDC callback failure → `lockout.record_failure(...)`.
- A4 local login failure → same.
- A5 SAML ACS failure → same.
- A6 MFA verify failure → same (per-user, not per-email).
- All success paths → `lockout.record_success(...)`.

**Sliding-window logic** (in `lockout.py`):

```sql
-- Count failures in window
SELECT count(*) FROM login_attempts
WHERE (
        (org_id = %(org_id)s AND email_attempted = %(email)s)
     OR (user_id = %(user_id)s)
    )
  AND outcome IN ('bad_password','mfa_failed','provider_rejected')
  AND created_at > now() - interval '%(window_seconds)s seconds';
```

If count ≥ max_failures, INSERT `account_lockouts` with `auto_unlock_at = now() + lockout_duration`.

Login pre-check:

```sql
SELECT 1 FROM account_lockouts
WHERE org_id = ? AND user_id = ?
  AND unlocked_at IS NULL
  AND (auto_unlock_at IS NULL OR auto_unlock_at > now())
```

If any row, return 423 with `Retry-After: <seconds>` header.

**Sweeper:** background task purges `login_attempts` older than `LOGIN_ATTEMPTS_RETENTION_DAYS` (default 90).

### 2.5 Trust model & failure semantics

- Pre-check happens BEFORE password verify, so a locked user with the right password still 423s.
- Race condition: two concurrent failed logins might both insert lockout rows; partial unique index `WHERE unlocked_at IS NULL` ensures only one active lockout. Use ON CONFLICT DO NOTHING.
- Admin unlock writes `unlocked_at`, `unlocked_by_user_id`, audit row.
- Lockout for an unknown email (no user yet exists): tracked by `(ip, email_attempted)` only — does not create an account_lockouts row, but returns 423 to slow brute force enumeration.

### 2.6 Tenant isolation

- Lockout for email X in org_a does NOT lock email X in org_b.
- Admin unlock requires admin scope in the _target_ org.

### 2.7 Observability

- Audit: `lockout.locked`, `lockout.auto_unlocked`, `lockout.admin_unlocked`.
- Metrics: `login_lockout_total{auth_kind}`, `login_attempts_total{auth_kind,outcome}`, `account_lockouts_active{org_id_hash}`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] 5 bad-password attempts in < 5min → 6th attempt returns 423 with Retry-After header.
- [ ] During lockout, correct password also returns 423.
- [ ] After cooldown elapses, correct password works.
- [ ] Admin force-unlock immediately ends the lockout; audit row written.
- [ ] Lockout in org_a does not affect same email in org_b.
- [ ] `GET /v1/auth/me/login-attempts` returns caller's history.

### 3.2 Test plan

**Unit:**

- Sliding-window threshold triggers at exactly N+1.
- Auto-unlock at `auto_unlock_at`.
- Concurrent failures don't create duplicate active lockouts.
- Admin unlock writes audit row.

**Integration:**

- Rapid-fire bad passwords → lockout.
- Correct password during lockout → 423.
- Cooldown elapses → 200.

**Tenant-isolation:**

- Email X locked in org_a; same email in org_b can still log in.

**Sweeper:**

- Old `login_attempts` rows purged after retention; audit summary row.

### 3.3 Compliance evidence produced

- NIST SP 800-63B §5.2.2 throttling.
- Audit of every attempt.
- Documented retention + sweeper.
- SIEM-exportable via `GET /internal/v1/auth/login-attempts`.

### 3.4 Rollout plan

1. Land with `enforce_lockout=false` for one release for telemetry.
2. Flip per-org `enforce_lockout=true`.

### 3.5 Backout plan

Set `enforce_lockout=false`.

### 3.6 Definition of done

- [ ] Migration 0011 applied.
- [ ] Lockout service hooks into all four login paths.
- [ ] Sweeper running.
- [ ] All tests pass.

---

## 4. Critical files

- New: `services/backend/migrations/0011_account_lockouts.sql` (+ rollback)
- New: `services/backend/src/backend_app/identity/lockout.py`
- Modify: A3/A4/A5/A6 service modules to call lockout hooks.
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)
- Modify: `services/backend-facade/src/backend_facade/auth_routes.py`
