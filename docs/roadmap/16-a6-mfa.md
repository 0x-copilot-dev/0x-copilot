# PR 16 — A6: MFA (TOTP + WebAuthn) and Per-org Enforcement

**Spec ID:** A6 | **Track:** Identity & Access | **Wave:** 4 (Auth Completion) | **Estimated effort:** L
**Depends on:** A1, A2, plus at least one of A3/A4/A5 (something to log into)
**Required for:** A9 (frontend MFA prompt), A10 (step-up checks)

---

## 1. Functional Specification

### 1.1 Goal

Add Multi-Factor Authentication. Two factor types — TOTP (RFC 6238) for AAL2, WebAuthn/FIDO2 for AAL3 (phishing-resistant, required by some bank/gov customers). Plus recovery codes for self-service unlock. Per-org enforcement policy.

### 1.2 User-visible behavior

- **End user:** can enroll one or more factors; on next login (when policy requires), prompted for a second factor; can list/remove factors; can use a recovery code if locked out.
- **Org admin:** sets `mfa_required=true` (and optionally `mfa_factors_min_class=phishing_resistant` for FIDO2-only).
- **End user (step-up):** sensitive routes (e.g. password change, admin actions) re-prompt for MFA if last verify > 5 minutes ago.

### 1.3 Out of scope

- SMS/email OTP (deliberately not supported — banks discourage).
- Push-based factors (e.g. Duo, mobile app push) — defer.
- WebAuthn passwordless flow (passkey-as-primary) — separate future PR.

---

## 2. Technical Specification

### 2.1 Architecture

- Each user can have N enabled factors of any kind. Recovery codes are per-user, single-use.
- Login mints a session with `mfa_satisfied_at=NULL` and `permission_scopes=['mfa:pending']` when MFA is required. Backend touch returns `mfa_recent=true/false` based on `mfa_satisfied_at + step_up_window`.
- Until `POST /v1/auth/mfa/verify` succeeds, all routes other than the MFA endpoints return 401.
- Step-up: routes can declare `requires_recent_mfa: 5m`; backend touch returns `mfa_recent` and the route handler enforces.

### 2.2 Schema changes

Migration `services/backend/migrations/0010_mfa.sql`:

```sql
CREATE TABLE mfa_factors (
    factor_id      TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    user_id        TEXT NOT NULL REFERENCES users(user_id),
    kind           TEXT NOT NULL CHECK (kind IN ('totp','webauthn')),
    display_name   TEXT NOT NULL,
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    enrolled_at    TIMESTAMPTZ NOT NULL,
    last_used_at   TIMESTAMPTZ,
    disabled_at    TIMESTAMPTZ
);
CREATE INDEX idx_mfa_factors_user_active
    ON mfa_factors (org_id, user_id, enabled) WHERE disabled_at IS NULL;
CREATE INDEX idx_mfa_factors_user_kind
    ON mfa_factors (user_id, kind, enabled);

CREATE TABLE totp_secrets (
    secret_id          TEXT PRIMARY KEY,
    factor_id          TEXT NOT NULL UNIQUE REFERENCES mfa_factors(factor_id),
    encrypted_secret   TEXT NOT NULL,                -- via TokenVault
    last_step          INTEGER,                      -- replay guard
    created_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE webauthn_credentials (
    credential_id          TEXT PRIMARY KEY,
    factor_id              TEXT NOT NULL REFERENCES mfa_factors(factor_id),
    credential_id_b64      TEXT NOT NULL UNIQUE,
    public_key_cose        BYTEA NOT NULL,
    sign_count             BIGINT NOT NULL DEFAULT 0,
    transports             JSONB NOT NULL DEFAULT '[]'::jsonb,
    aaguid                 TEXT,
    attestation_format     TEXT NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL,
    last_used_at           TIMESTAMPTZ
);
CREATE INDEX idx_webauthn_credentials_factor ON webauthn_credentials (factor_id);

CREATE TABLE mfa_challenges (
    challenge_id         TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL,
    user_id              TEXT NOT NULL REFERENCES users(user_id),
    kind                 TEXT NOT NULL CHECK (kind IN ('totp','webauthn')),
    nonce                TEXT NOT NULL UNIQUE,
    expected_factor_id   TEXT,
    expires_at           TIMESTAMPTZ NOT NULL,
    consumed_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_mfa_challenges_pending
    ON mfa_challenges (expires_at) WHERE consumed_at IS NULL;

CREATE TABLE mfa_recovery_codes (
    code_id      TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    code_hash    TEXT NOT NULL UNIQUE,                -- sha256
    consumed_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_mfa_recovery_active
    ON mfa_recovery_codes (user_id) WHERE consumed_at IS NULL;

-- Extend sessions table
-- (column already exists from A2; this PR populates it)
```

### 2.3 Endpoints

**Facade public:**

- `GET /v1/auth/mfa/factors` — list mine.
- `POST /v1/auth/mfa/factors/totp/enroll` → `{otpauth_url, secret_b32, recovery_codes_one_time}`. Recovery codes shown ONCE; not retrievable later.
- `POST /v1/auth/mfa/factors/totp/confirm` `{code}` → enables factor.
- `POST /v1/auth/mfa/factors/webauthn/register/start` `{display_name}` → `PublicKeyCredentialCreationOptions`.
- `POST /v1/auth/mfa/factors/webauthn/register/finish` `{attestation}` → enables factor.
- `DELETE /v1/auth/mfa/factors/{factor_id}` — disables (soft).
- `POST /v1/auth/mfa/challenge` → returns challenge for step-up.
- `POST /v1/auth/mfa/verify` `{challenge_id, totp_code | assertion}` → satisfies session MFA.
- `POST /v1/auth/mfa/recovery/consume` `{code}` → satisfies session MFA, marks code used.

**Backend internal:** `/internal/v1/auth/mfa/*` mirroring.

### 2.4 Code changes

**New:**

- `services/backend/src/backend_app/identity/mfa.py` — orchestration.
- `services/backend/src/backend_app/identity/totp.py` — wraps `pyotp`.
- `services/backend/src/backend_app/identity/webauthn.py` — wraps `py_webauthn`.

**Modify:**

- [services/backend/src/backend_app/identity/sessions.py](../../services/backend/src/backend_app/identity/sessions.py) (from A2) — `mfa_satisfied_at` populated by verify endpoint; touch returns `mfa_recent` flag.
- [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py) — register routes.
- `services/backend-facade/src/backend_facade/auth_routes.py` — public surface.
- `services/backend/requirements.txt` — `pyotp`, `webauthn` (`py_webauthn`).

### 2.5 Trust model & failure semantics

- TOTP secret encrypted at rest via TokenVault (C6 makes this KMS-backed in production).
- TOTP code window ±1 step (default 30s × 1).
- TOTP replay guard via `last_step`.
- WebAuthn signature verified against COSE public key; `sign_count` increment enforced (rejects cloned credentials).
- Disabled factor cannot be used.
- Recovery code: single-use (CAS on `consumed_at`).
- Step-up window: configurable per route via decorator; default 5m.

### 2.6 Tenant isolation

- Factor enrollment scoped to org+user.
- Cross-org verify: challenge from org_a + assertion from org_b → rejected (challenge user_id != session user_id).

### 2.7 Observability

- Audit: `mfa.factor.enrolled`, `mfa.factor.removed`, `mfa.verify.succeeded`, `mfa.verify.failed{reason}`, `mfa.recovery.consumed`.
- Metrics: `mfa_verify_total{kind,outcome}`, `mfa_factor_count{kind}`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Enroll TOTP → verify with code from authenticator app → factor enabled.
- [ ] Enroll WebAuthn (test against Chrome's virtual authenticator) → verify → factor enabled.
- [ ] Recovery codes: 10 generated; each works once.
- [ ] Org with `mfa_required=true`: login mints session with `mfa:pending` scope; protected routes 401 until verify.
- [ ] Sensitive route with `requires_recent_mfa: 5m`: returns 403 with `WWW-Authenticate: x-step-up` after window.
- [ ] Disabled factor cannot verify.
- [ ] WebAuthn `sign_count` decreases → reject (cloned credential).

### 3.2 Test plan

**Unit:**

- TOTP code window ±1 step accepted.
- TOTP replay (same `last_step`) rejected.
- WebAuthn invalid signature rejected.
- WebAuthn `sign_count` non-monotonic rejected.
- Recovery code single-use.

**Integration:**

- Full enroll→verify flow for both kinds.
- Login with mfa_required → session 401s on protected routes until verify.
- Step-up: route returns 403 after window, 200 after fresh verify.
- Cross-org factor enrollment isolated.

### 3.3 Compliance evidence produced

- NIST SP 800-63B AAL2 (TOTP).
- AAL3 (FIDO2 phishing-resistant).
- Audit of every MFA event.
- Recovery codes hashed at rest.
- Per-org configurable enforcement.

### 3.4 Rollout plan

- Per-org `mfa_required` config.
- Phased: announce → enable in audit-only mode (banner + audit row, no enforcement) → enforce.

### 3.5 Backout plan

Set `mfa_required=false`. Sessions with `mfa:pending` continue to be `pending` until they verify or expire — they remain unable to use protected routes (intentional).

### 3.6 Definition of done

- [ ] Migration 0010 applied.
- [ ] Both factor kinds working.
- [ ] Recovery codes flow tested.
- [ ] Step-up tested.
- [ ] All audit + metric rows produced.

---

## 4. Critical files

- New: `services/backend/migrations/0010_mfa.sql` (+ rollback)
- New: `services/backend/src/backend_app/identity/{mfa,totp,webauthn}.py`
- Modify: [services/backend/src/backend_app/identity/sessions.py](../../services/backend/src/backend_app/identity/sessions.py)
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)
- Modify: `services/backend-facade/src/backend_facade/auth_routes.py`
- Modify: `services/backend/requirements.txt`
