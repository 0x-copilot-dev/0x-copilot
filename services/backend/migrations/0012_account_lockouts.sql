-- A8: Account lockouts + per-org lockout policy.
--
-- ``login_attempts`` (created in 0004 alongside the identity foundation)
-- is the read side: it carries every login outcome from A3 (OIDC), A4
-- (local), and the future A5 (SAML) / A6 (MFA). This migration adds the
-- write side — the active-lockout row that LoginService.check() consults
-- before any password / token verify.
--
-- Two-phase rollout per docs/roadmap/17-a8-lockout.md §3.4: ship with
-- ``enforce_lockout=false`` so the failure curve gets logged for one
-- release before the gate slams shut. Operators flip the per-org policy
-- once they're comfortable with the auto-unlock window.

CREATE TABLE IF NOT EXISTS account_lockouts (
    lockout_id           TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL,
    user_id              TEXT NOT NULL REFERENCES users(user_id),
    locked_at            TIMESTAMPTZ NOT NULL,
    lock_reason          TEXT NOT NULL,
    auto_unlock_at       TIMESTAMPTZ,
    unlocked_at          TIMESTAMPTZ,
    unlocked_by_user_id  TEXT,
    unlock_reason        TEXT
);
-- One active lockout per (org, user). The partial index lets a re-lock
-- after admin unlock (or auto-unlock) succeed without conflict.
CREATE UNIQUE INDEX IF NOT EXISTS idx_account_lockouts_active
    ON account_lockouts (org_id, user_id) WHERE unlocked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_account_lockouts_auto_unlock
    ON account_lockouts (auto_unlock_at) WHERE unlocked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_account_lockouts_locked_at
    ON account_lockouts (org_id, locked_at DESC);

CREATE TABLE IF NOT EXISTS lockout_policies (
    policy_id                    TEXT PRIMARY KEY,
    org_id                       TEXT NOT NULL UNIQUE,
    enforce_lockout              BOOLEAN NOT NULL DEFAULT FALSE,
    max_failures                 INTEGER NOT NULL DEFAULT 5,
    failure_window_seconds       INTEGER NOT NULL DEFAULT 300,
    lockout_duration_seconds     INTEGER NOT NULL DEFAULT 900,
    permanent_after_n_lockouts   INTEGER NOT NULL DEFAULT 0,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);
