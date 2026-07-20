-- 0038_account_merge — the account-merge saga (PRD docs/plan/account-linking §6).
--
-- (1) account_merges: one row per absorbed→survivor merge; the saga record +
--     audit anchor. `state` is the last COMPLETED checkpoint (pending →
--     backend_done → runtime_done → sessions_revoked → completed); a failure
--     sets `error` and leaves `state` at the checkpoint so a resume is safe
--     and nothing is ever half-owned (NFR-3/8).
-- (2) users lineage: absorbed accounts are soft-disabled, never hard-deleted —
--     the stub + these columns + the immutable audit trail are the
--     support-grade reversal record (FR-M7 / NFR-6).
-- (3) oidc_authentications.link_confirm_merge: the user's explicit merge
--     consent recorded server-side at link-start (FR-U2).
-- (4) identity_audit_events immutability: attach the 0002 audit_immutable_guard
--     trigger — deferred as "Phase 2" in 0004 and required before merge events
--     can count as an immutable trail (NFR-5).

CREATE TABLE account_merges (
    merge_id          TEXT PRIMARY KEY,
    survivor_org_id   TEXT NOT NULL REFERENCES organizations (org_id),
    survivor_user_id  TEXT NOT NULL,
    absorbed_org_id   TEXT NOT NULL REFERENCES organizations (org_id),
    absorbed_user_id  TEXT NOT NULL,
    state             TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'backend_done', 'runtime_done',
                         'sessions_revoked', 'completed')),
    proof_ref         TEXT NOT NULL,
    error             TEXT,
    counts            JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ
);

-- Resume + idempotency lookups: "is this absorbed account already merged /
-- mid-merge?" (NFR-8). Partial unique: at most ONE non-completed merge per
-- absorbed account, so two concurrent link-confirms can't double-run the saga.
CREATE INDEX idx_account_merges_absorbed
    ON account_merges (absorbed_org_id, absorbed_user_id);
CREATE UNIQUE INDEX idx_account_merges_absorbed_active
    ON account_merges (absorbed_org_id, absorbed_user_id)
    WHERE state <> 'completed';

ALTER TABLE users
    ADD COLUMN absorbed_into_user_id TEXT,
    ADD COLUMN merged_at TIMESTAMPTZ;

COMMENT ON COLUMN users.absorbed_into_user_id IS
    'Account-merge lineage: the survivor user this account was absorbed into (NULL = never merged).';

ALTER TABLE oidc_authentications
    ADD COLUMN link_confirm_merge BOOLEAN NOT NULL DEFAULT FALSE;

-- NFR-5: identity audit rows (including account.merged) must be append-only at
-- the DB layer, same guard the MCP/skill audit tables got in 0002.
DROP TRIGGER IF EXISTS identity_audit_events_immutable ON identity_audit_events;
CREATE TRIGGER identity_audit_events_immutable
  BEFORE UPDATE OR DELETE ON identity_audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_immutable_guard();
